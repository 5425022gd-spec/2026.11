"""主线剧本 AI 工作流：一次把全部工具交给模型，AI 自己决定查什么、何时交卷。

口径 玩家定义，本模块是唯一实现处：
1. **只两套提示词**：剧本首次撰写用 `Prompt/meeting_pizou_free.txt`，
   改写 玩家干预后重排用 `Prompt/meeting_pizou_replan.txt`。不再分阶段。
2. **工具就是函数调用**：工具彼此独立，不分轮解锁，也没有"工具包扩展"。
   一次把全部工具交给模型，模型自己反复调用，直到调用 `finish_script` 交卷。
   工具的用法、返回格式都在各自的 `description` 里 见 `script_tools.py`，**不写进提示词**；
   提示词只放提示词与数据变量。
3. 多轮工具往返本身由 `engine/ai_workflow.run_tool_workflow` 统一实现 各套 AI 共用一份；
   本模块只负责：选提示词、填数据变量、给工具、整理交卷。
4. AI 调用 `finish_script` 后**不再调用模型**：把剧情名称、剧情介绍、剧情大纲与展示事件
   交给 `mainline_script.update_mainline_script` 落库；整段对话写进 `session.script_sessions`，
   在前端「事件树」查看。
5. 脏返回 不调 finish / 缺字段 / 工具报错→ 如实记账，不写假剧本。
"""

from typing import Any, Dict, List

from .logger import CONSOLE
from . import script_tools as _tools
from .ai_workflow import run_tool_workflow

TOOL_FINISH = _tools.TOOL_FINISH

MAX_MODEL_CALLS = 24        # 一次剧本工作流最多请求模型多少轮，防止模型打转


# ═══════════════════════════════════════════════════════════
# 主流程 多轮工具往返由 engine/ai_workflow.py 统一实现
# ═══════════════════════════════════════════════════════════

def _fill_prompt(tpl: str, *, state: Any, session: Any, topic: str, context_data: str,
                 book_text: str, report_text: str, members: str,
                 conversation: str, mainline: Dict[str, Any], tool_a: str = "") -> str:
    """把提示词里的数据变量填满 {{book}}、{{ report}}、历史与对话等。

    提示词里只填**数据**；写作与返回格式属于工具说明，不在这里注入。
    """
    from .session import (extract_history_all, extract_history_1,
                          extract_history_6, extract_history_12)
    from .mainline_script import get_history_script_summary
    out = (tpl.replace("{{toolA}}", tool_a)
              .replace("{{topic}}", topic)
              .replace("{{context_data}}", context_data)
              .replace("{{conversation}}", conversation)
              # 世界历史四个口径：全部 / 1 月 / 6 月 / 12 月
              .replace("{{history}}", extract_history_all(state))
              .replace("{{history_1}}", extract_history_1(state))
              .replace("{{history_6}}", extract_history_6(state))
              .replace("{{history_12}}", extract_history_12(state))
              .replace("{{members}}", members)
              .replace("{{book}}", book_text)
              .replace("{{ report}}", report_text)
              .replace("{{report}}", report_text)
              .replace("{{history_phases_summary}}", get_history_script_summary(mainline)))
    from .council import _ensure_timestamp
    return _ensure_timestamp(out, state.date)


def run_script_workflow(state: Any, session: Any, *, mainline: Dict[str, Any], topic: str,
                        context_data: str, book_text: str, report_text: str,
                        members: str, conversation: str,
                        llm: Any = None) -> Dict[str, Any]:
    """跑完剧本工具工作流，返回 {script, log}。

    script 为 None 表示没拿到可用剧本 脏返回，log 是事件树记录用的对话轨迹。
    """
    from .council import read_prompt

    first_time = not (mainline.get("long_term_script") or mainline.get("events"))
    # 系统事件「重新撰写剧本」命中后，一律走 replan_2
    rewrite = bool((getattr(state, "mainline_state", {}) or {}).get(mainline.get("id"), {}).get("rewrite_requested"))
    if rewrite:
        prompt_file = "meeting_pizou_replan_2.txt"
    else:
        prompt_file = "meeting_pizou_free.txt" if first_time else "meeting_pizou_replan.txt"
    tpl = read_prompt(prompt_file)
    if not tpl:
        return {"script": None, "log": [], "error": "缺提示词：Prompt/%s" % prompt_file}

    # 结束条件二选一，互相对立：
    #   有计划数值硬判定 → {{toolA}} 空，交卷仍走 finish_script；
    #   没有数值硬判定   → 发 submit_script_end，由 AI 判定收束并写 100-200 字概括。
    has_hard_end = bool(mainline.get("end"))
    end_only = (prompt_file == "meeting_pizou_replan.txt") and not has_hard_end
    tool_a = _tools.end_tool()["description"] if end_only else ""

    prompt = _fill_prompt(tpl, state=state, session=session, topic=topic,
                          context_data=context_data, book_text=book_text,
                          report_text=report_text, members=members,
                          conversation=conversation, mainline=mainline,
                          tool_a=tool_a)

    if llm is None:
        from .llm import get_llm_for_system
        llm = get_llm_for_system("court", getattr(session, "llm", None))

    log: List[Dict[str, Any]] = [{"kind": "prompt", "file": prompt_file, "text": prompt}]

    sys_text = "你现在是主线剧本生成器，通过工具调用查阅资料并撰写剧本。"
    messages: List[Dict[str, Any]] = [{"role": "system", "content": sys_text},
                                      {"role": "user", "content": prompt}]
    tools = [t for t in _tools.TOOLS if t["name"] != _tools.TOOL_END]
    if end_only:
        tools = tools + [_tools.end_tool()]
    log.append({"kind": "tools", "names": [t["name"] for t in tools]})

    if getattr(llm, "mock", False):
        return _mock_run(state, session, log, mainline=mainline, topic=topic)

    result = run_tool_workflow(
        llm, messages, tools, state=state, session=session,
        finish_name=(_tools.TOOL_END if end_only else TOOL_FINISH), max_calls=MAX_MODEL_CALLS,
        payload_keys=(("summary",) if end_only else _tools.FINISH_PAYLOAD_KEYS),
        finish_handler=(_tools.end_payload if end_only else _tools.finish_payload), log=log)
    if result["value"] is None:
        return {"script": None, "log": log, "error": result["error"]}

    if end_only:
        log.append({"kind": "end", "end_summary": result["value"]})
        CONSOLE.log("court", "剧本收束", (result["value"].get("end_summary") or "")[:40])
        return {"script": None, "end_summary": result["value"].get("end_summary", ""), "log": log}
    script = result["value"]
    log.append({"kind": "finish", "script": script})
    CONSOLE.log("court", "剧本工作流完成",
                "《%s》事件 %d 条" % (script["name"], len(script["events"])))
    return {"script": script, "log": log}


def _mock_run(state: Any, session: Any, log: List[Dict[str, Any]],
              *, mainline: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """mock 模式 未配 key：不请求网络，给一份确定性的最小剧本，保证管线可演示。"""
    mid = mainline.get("id") or "custom"
    script = {
        "name": mainline.get("title") or topic or "朝堂新政",
        "introduction": " mock 模式未配置大模型，本次剧本由引擎给出最小骨架。",
        "outline": " mock 模式未配置大模型，故不推演大纲；解禁后由 AI 依数据目录自行检索撰写。",
        "events": [{
            "event_id": "%s-1" % mid,
            "trigger": "剧本开始后1天",
            "display_type": "memorial",
            "title": "题本 %s开局奏报" % (mainline.get("title") or topic or "新政"),
            "category_label": "题本",
            "content_label": "人事类",
            "location": "北直隶顺天府",
            "full_text": "臣等谨题：此要紧事务既已立案，谨先具报，候圣裁施行。",
            "draft": "著该部详议具奏。",
            "reward": "",
            "impact": "",
        }],
    }
    log.append({"kind": "mock", "text": "mock 模式：未调用大模型，使用最小剧本骨架"})
    CONSOLE.log("court", "mock 模式生成剧本骨架", script["name"])
    return {"script": script, "log": log}
