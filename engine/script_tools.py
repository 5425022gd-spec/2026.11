"""剧本系统交给 AI 的工具：定义与实现同源，一个工具只干一件事。

写法照着本机 DSH DeepSeek Harness的工具插件来，一个工具就是一份定义：

    name         工具名，全局唯一
    description  发给模型的说明 **工具说明只在这里**，不写进提示词
    parameters   参数 JSON Schema：逐项 description、required 逐项标注、
                 enum 收口取值、additionalProperties 显式关闭
    output       {schema, render}：execute 返回**结构化**规范值，
                 模型看到的内容由 render 从该值渲染 不是直接吐一段自然语言
    execute      真正干活，返回 output.schema 约束的结构化值

工具之间彼此独立：不共享"工具包"，不按轮次解锁，各自一份定义、各自一份实现。
DSH 里每个工具由自己的插件包注册、插件设置写在 loader 的 config 里；
本项目的对应物就是本模块的 TOOLS 与 `tools_payload()`。
"""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import script_search as _ss
from .logger import CONSOLE
from .ai_tools import (ToolArgsError, ToolOutputError, ToolNotFound,
                       validate_args, unwrap_envelope, define_tool,
                       tools_payload as _payload_of, tool_index,
                       dispatch as _dispatch)

# 可用域的名字 工具说明里要列给模型看
DOMAIN_HINT = "、".join(_ss.DOMAINS.keys())

# 交卷工具的四件产出：剥信封时用它认内层 免得把正常参数误剥
FINISH_PAYLOAD_KEYS = ("name", "introduction", "outline", "events")


# ═══════════════════════════════════════════════════════════
# 工具定义
# ═══════════════════════════════════════════════════════════

_tool = define_tool


def _search_database() -> Dict[str, Any]:
    return _tool(
        "search_database",
        "按键索词检索游戏数据库里的官方档案与实录，返回最相关段落的摘要。"
        "可检索的域：" + DOMAIN_HINT + "。\n"
        "【怎么用】一次调用提交 queries 数组，2 到 4 条最合适：条数太多会互相挤占输出上限，"
        "反而都看不全。每条的关键词用**一个最核心的词**最容易命中；写成多个词是「全部命中」"
        "才算命中，词越多越搜不到。不确定条目叫什么，先调用 list_database_items 看目录，"
        "再按条目名里的词来搜。\n"
        "【结果怎么看】每条查询一段结果；某条显示「本域检索无命中」就换词或换域，"
        "别原样重试；显示「该项取数失败」是该条目本身读不出来，重查同一域不会变。"
        "单次总输出上限约 6000 字，超出会截断并提示，摘要看不全时缩小 domain 再查。",
        {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "description": "要检索的查询列表，建议 2 到 4 条",
                    "items": {
                        "type": "object",
                        "properties": {
                            "keywords": {
                                "type": "string",
                                "description": "关键词，建议只给一个最核心的词；"
                                               "给多个词时要求全部命中才算命中",
                            },
                            "domain": {
                                "type": "string",
                                "enum": list(_ss.DOMAINS.keys()),
                                "description": "限定检索的域；不填则检索全部域",
                            },
                        },
                        "required": ["keywords"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["queries"],
        },
        {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "array",
                    "description": "每条查询一段结果",
                    "items": {
                        "type": "object",
                        "properties": {
                            "keywords": {"type": "array", "items": {"type": "string"}},
                            "domain": {"type": "string"},
                            "hits": {"type": "integer"},
                            "failed": {"type": "integer"},
                            "text": {"type": "string"},
                            "lines": {"type": "array", "items": {"type": "string"}},
                            "cut": {"type": "boolean"},
                        },
                        "required": ["keywords", "domain", "hits", "failed", "text",
                                     "lines", "cut"],
                        "additionalProperties": False,
                    },
                },
                "truncated": {"type": "boolean"},
                "timed_out": {"type": "boolean"},
            },
            "required": ["blocks", "truncated", "timed_out"],
            "additionalProperties": False,
        },
        lambda args, value: _ss.render_search(value),
        lambda args, state, session: _ss._run_search(state, session, args.get("queries") or []),
    )


def _list_database_items() -> Dict[str, Any]:
    return _tool(
        "list_database_items",
        "列出某个域的全部条目名 只有目录，不含正文。可列出的域：" + DOMAIN_HINT + "。"
        "先列目录看清有哪些条目，再用 search_database 按条目名里的词检索正文，比盲搜省上下文。"
        "目录里显示「该项取数失败」的条目本次读不出来，重复列同一域不会变。",
        {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "enum": list(_ss.DOMAINS.keys()),
                    "description": "要列目录的域",
                },
            },
            "required": ["domain"],
        },
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "title": {"type": "string"},
                "desc": {"type": "string"},
                "items": {"type": "array", "items": {"type": "string"}},
                "error": {"type": "string"},
            },
            "required": ["domain", "title", "desc", "items"],
            "additionalProperties": False,
        },
        _render_items,
        _execute_items,
    )


def _execute_items(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    d = str(args.get("domain") or "").strip()
    items, err = _ss._fetch_items(state, session, d)
    spec = _ss.DOMAINS.get(d) or {}
    return {
        "domain": d,
        "title": spec.get("title", d),
        "desc": _ss._txt(spec.get("desc", "")),
        "items": [] if err else [nm for nm, _g in items],
        "error": err or "",
    }


def _render_items(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    if value.get("error"):
        return str(value["error"])
    names = value.get("items") or []
    lines = ["【%s %s】共 %d 个条目" % (value.get("title"), value.get("domain"), len(names))]
    if value.get("desc"):
        lines.append(str(value["desc"]))
    for i, nm in enumerate(names, 1):
        lines.append("%d. %s" % (i, nm))
    out = "\n".join(lines)
    if len(out) > _ss.LIST_LIMIT:
        out = out[:_ss.LIST_LIMIT] + "\n 条目列表已截断，只显示前 %d 字" % _ss.LIST_LIMIT
    return out


def _finish_script() -> Dict[str, Any]:
    """交卷工具。**剧本的写作与返回规则写在它的 description 里**——提示词只放提示词，
    工具用法与返回格式属于工具的说明。大纲规则、事件展示规则、超控触发规则、
    可执行超控指令清单都由引擎唯一来源现取，拼成这一段说明。

    与 submit_script_end 的分工（两者互斥，同一次调用只会给其中一个）：
      - 剧本有 world 里的 end 数值硬判定 → 只给本工具，写完整剧本交卷；
      - 没有硬判定、需要 AI 自己判断何时收束 → 只给 submit_script_end，
        由它交一份 100-200 字收尾概括。
    不合并成一个工具，是因为「有没有硬判定」决定了要不要收尾概括，
    合并后得靠条件必填字段表达，schema 说不清，模型更容易漏字段。
    """
    from .mainline_script import get_output_definitions, get_impact_definitions
    from .operations import get_all_operations_schema

    description = (
        "确信并完成本次剧本撰写，交回成稿。这是唯一的交卷通道：一旦调用，"
        "系统立刻停止调用模型，用你这次提交的内容落库。\n"
        "调用前请确认剧情名称、剧情介绍、剧情大纲、展示事件都已写好；没写好不要调用。\n\n"
        "【怎么传参】name、introduction、outline、events 四个参数要**直接**作为本次调用的参数传入，"
        "不要再包一层 arguments、function 或 parameters，也不要把它们拼成一整段文字。形状如下：\n"
        "  name：军饷筹措\n"
        "  introduction：各军始犹典衣卖箭，今则鬻子出妻；始犹沿街乞食，今则离伍潜逃。\n"
        "  outline：长篇大纲正文\n"
        "  events：一个数组，每项含 event_id、time、place、cast、type、title、full_text，"
        "按需再带 category_label、content_label、location、draft、reward、impact\n"
        "四个参数缺任何一个都会被退回；退回时系统会写明缺了什么、该怎么传，照提示补齐后再调一次。\n\n"
        "【剧情名称与介绍】名称四到六字，如「军饷筹措」；介绍一两句，"
        "现代普通话或文言文均可，说明当前局面。\n\n"
        "【剧情大纲 outline】要写清：哪些势力被本次剧情影响、哪些势力会加入、"
        "各自会作何反应、之前的剧本里哪些是伏笔、伏笔如何回收、因果如何闭环。\n\n"
        "【展示事件 events】剧本的事件不以长文本展示，而是通过一个个事件渠道立体展示。\n"
        + get_output_definitions() + "\n"
        "- 只展示剧情最开始的一小段，后面剧情推进了会更新事件集。\n"
        "- 事件数量按剧情内容定，重要剧情一般需要数十个事件。\n\n"
        "【超控影响 impact】事件可在特定时间对游戏系统施加动态影响，"
        "做成临时或最终的剧情奖励，或高潮时的临时惩罚。\n"
        + get_impact_definitions() + "\n\n"
        + get_all_operations_schema()
    )
    return _tool(
        "finish_script",
        description,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "剧情名称，简短四到六字，如 军饷筹措"},
                "introduction": {"type": "string",
                                 "description": "当前情况介绍，一两句，现代普通话或文言文均可"},
                "outline": {"type": "string",
                            "description": "详细剧情大纲：哪些势力被影响、哪些势力加入、"
                                           "各自作何反应、前文伏笔如何回收、因果如何闭环"},
                "events": {
                    "type": "array",
                    "description": "本次要展示的事件条目，按事件展示规则的字段填写，可多条",
                    "items": {
                        "type": "object",
                        "properties": {
                            "event_id": {"type": "string", "description": "事件唯一编号，如 jxcc-1"},
                            "time": {"type": "object",
                                     "description": "时间条件：{type:none} / {type:after_start,days,hours,minutes} / "
                                                    "{type:after_tag,tag,days,hours,minutes} / {type:at,at:\"1627-09-01 08:30\"}",
                                     "properties": {"type": {"type": "string"},
                                                    "at": {"type": "string"},
                                                    "tag": {"type": "string"},
                                                    "days": {"type": "integer"},
                                                    "hours": {"type": "integer"},
                                                    "minutes": {"type": "integer"}}},
                            "place": {"type": "object",
                                      "description": "地点条件：{type:none} / {type:in_building,place:殿宇名} / "
                                                     "{type:tag_absent,tag:标签名}",
                                      "properties": {"type": {"type": "string"},
                                                     "place": {"type": "string"},
                                                     "tag": {"type": "string"}}},
                            "cast": {"type": "array", "items": {"type": "string"},
                                     "description": "参与人物姓名，可空数组"},
                            "script": {"type": "string",
                                       "description": "对话类事件专用：这一段戏的剧本片段——"
                                                      "此人为何而来、他知道什么、想要什么、"
                                                      "底线与禁区。会话 AI 拿它现场扮演，"
                                                      "不要在这里预先写台词"},
                            "type": {"type": "string",
                                     "enum": ["memorial", "request_dialogue", "force_dialogue",
                                              "letter", "rewrite_script", "broadcast_tag"],
                                     "description": "事件类型：章奏 / 请求对话 / 强制对话 / 信件；"
                                                    "系统事件 rewrite_script、broadcast_tag 只填 time 与 type"},
                            "trigger": {"type": "string",
                                        "description": "旧字段，兼容用；新剧本请改用 time"},
                            "display_type": {"type": "string", "enum": ["memorial"],
                                             "description": "旧字段，兼容用；新剧本请改用 type"},
                            "title": {"type": "string", "description": "事件标题"},
                            "category_label": {"type": "string", "description": "公文类别，如 题本"},
                            "content_label": {"type": "string", "description": "内容分类，如 钱粮类"},
                            "location": {"type": "string", "description": "事发地点，精确到府"},
                            "full_text": {"type": "string", "description": "事件正文"},
                            "draft": {"type": "string", "description": "内阁票拟，可空"},
                            "reward": {"type": "string", "description": "效果说明，可空"},
                            "impact": {"type": "string",
                                       "description": "要施加的超控指令，写成「即时触发：<指令>」"
                                                      "或「事件后XX天触发：<指令>」；无则留空"},
                        },
                        "required": ["event_id", "title", "full_text"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["name", "introduction", "outline", "events"],
        },
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "introduction": {"type": "string"},
                "outline": {"type": "string"},
                "event_count": {"type": "integer"},
            },
            "required": ["name", "introduction", "outline", "event_count"],
            "additionalProperties": False,
        },
        lambda args, value: "已收到剧本《%s》，共 %d 条展示事件。"
                            % (value.get("name"), value.get("event_count")),
        _execute_finish,
    )


def _execute_finish(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    name = str(args.get("name") or "").strip()
    outline = str(args.get("outline") or "").strip()
    if not name:
        raise ToolArgsError("剧情名称不能为空")
    if not outline:
        raise ToolArgsError("剧情大纲不能为空")
    events = args.get("events")
    if not isinstance(events, list) or not events:
        raise ToolArgsError("展示事件至少要有一条")
    return {
        "name": name,
        "introduction": str(args.get("introduction") or "").strip(),
        "outline": outline,
        "event_count": len(events),
    }


# ── 结束剧本：只在「没有数值硬判定」的剧本里给 AI 用 ──
TOOL_END = "submit_script_end"


def _submit_script_end() -> Dict[str, Any]:
    """结束剧本工具：剧情走到尽头时调用，交一份 100-200 字的收尾概括。

    每个剧本的结束条件只有两种，互相对立：
      - 引擎数值硬判定：由 world 里的 end 条件自动判定，不看 AI；
      - AI 判定：没有数值硬判定时才把本工具发给模型，由它决定何时收束。
    """
    return _tool(
        TOOL_END,
        "结束剧本。当你判断这段剧情已经走到尽头、应当收束时调用它，这是本轮的结束通道："
        "一被调用系统立刻收工。不要先写事件、不要先列超控指令。"
        "summary 用 100 到 200 字概括剧情如何收场：谁做成了什么、谁输了什么、"
        "局面最后停在哪里。不要再写事件、不要再列超控指令，只写这段概括。",
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "100-200 字的剧情收尾概括"},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        lambda args, value: "剧本已收束：%s" % value["summary"][:40],
        _execute_end,
    )


def _execute_end(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    text = str((args or {}).get("summary") or "").strip()
    if not text:
        raise ToolArgsError("summary 不能为空：请写 100 到 200 字的剧情收尾概括。")
    return {"end_summary": text}


def end_payload(args: Any, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """结束剧本的交卷整理：剥信封、按 schema 校验，再落成 {end_summary}。"""
    args, _ = unwrap_envelope(args, TOOL_END, ("summary",))
    checked = validate_args(TOOL_INDEX[TOOL_END]["parameters"], args)
    return _execute_end(checked, state, session)


def end_tool() -> Dict[str, Any]:
    return _submit_script_end()


TOOLS: List[Dict[str, Any]] = [
    _search_database(),
    _list_database_items(),
    _finish_script(),
    _submit_script_end(),          # 是否真的发给模型由 script_workflow 决定
]

TOOL_INDEX: Dict[str, Dict[str, Any]] = {t["name"]: t for t in TOOLS}
TOOL_FINISH = "finish_script"


# ═══════════════════════════════════════════════════════════
# 对外：请求用的工具清单，与调用派发
# ═══════════════════════════════════════════════════════════

def tools_payload() -> List[Dict[str, Any]]:
    """发给模型的工具数组：**只含 name / description / parameters** 同 DSH 的白名单口径。"""
    return _payload_of(TOOLS)


def dispatch(state: Any, session: Any, name: str, args: Any) -> Tuple[str, Any]:
    """派发一次调用 内核在 ai_tools，全站同一套校验与渲染口径。"""
    return _dispatch(TOOL_INDEX, state, session, name, args,
                     payload_keys=FINISH_PAYLOAD_KEYS)

def finish_payload(args: Any, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """交卷：把 finish_script 的参数整理成落库结构 缺字段就是错，不编造。

    参数被裹成信封 {"arguments": ...}时先剥一层——这是模型把函数调用外壳当参数传的
    已知形状；剥完仍不合规就报错，并把「收到的字段」与「该怎么传」写进错误文字，
    让模型下一轮能改对，而不是反复重试同一个形状。
    """
    args, _unwrapped = unwrap_envelope(args, TOOL_FINISH)
    try:
        checked = validate_args(TOOL_INDEX[TOOL_FINISH]["parameters"], args)
        value = _execute_finish(checked, state, session)
    except ToolArgsError as e:
        got = "、".join(sorted(args)) if isinstance(args, dict) and args else "空"
        raise ToolArgsError(
            "%s。本次收到的字段：%s。交卷要的四个参数是 name、introduction、outline、events，"
            "请把它们**直接**作为 finish_script 的参数传入，不要再包一层 arguments 或 function。"
            % (e, got))
    return {
        "name": value["name"],
        "introduction": value["introduction"],
        "outline": value["outline"],
        "events": [e for e in checked.get("events") or [] if isinstance(e, dict)],
    }
