# -*- coding: utf-8 -*-
"""AI 工具工作流：多轮模型调用 + 工具往返，直到模型调用交卷工具为止。

各套 AI 剧本撰写、章奏生成……的差别只在"给哪些工具、交卷工具叫什么、交卷后怎么整理"，
循环本身只有这一份实现：

    发一次请求 → 模型要么调工具、要么交卷
      调工具：派发 → 结果作为 role=tool 回给模型 → 继续
      交卷：用交卷整理器整出规范值 → 结束
      什么都不调：结束 上层判定为没交卷

工具参数不是合法 JSON 多是内容太长被服务端截断时**如实记为失败**并把原文长度与改法回给模型，
绝不静默当成空参数——那会让模型以为自己没传东西而反复重试同一个形状。
"""

from typing import Any, Callable, Dict, List, Optional, Sequence

from .logger import CONSOLE
from . import ai_tools
from .ai_tools import ToolNotFound


def run_tool_workflow(llm: Any, messages: List[Dict[str, Any]],
                      tools: Sequence[Dict[str, Any]], *,
                      state: Any, session: Any,
                      finish_name: str,
                      max_calls: int = 24,
                      payload_keys: Optional[Sequence[str]] = None,
                      finish_handler: Optional[Callable[[Any, Any, Any], Any]] = None,
                      log: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """跑到交卷为止。返回 {"value", "log", "error", "rounds"}。

    - value：交卷整理器给的规范值；没交卷就是 None。
    - log：逐条轨迹，供事件树与导出使用。
    - error：没交卷时的原因 调用次数用尽 / 模型不再调工具。
    """
    index = ai_tools.tool_index(tools)
    payload = ai_tools.tools_payload(tools)
    log = log if log is not None else []
    value: Any = None
    error = ""
    rounds = 0

    for _ in range(max_calls):
        rounds += 1
        content, think_text, tool_calls = llm.chat_once(messages, tools=payload, think=False)
        if not tool_calls:
            # 没调工具就是没交卷；如实记下这句话，由上层判定脏返回
            if content:
                log.append({"kind": "assistant", "text": content})
            break
        asst: Dict[str, Any] = {"role": "assistant", "content": content or ""}
        if think_text:
            asst["reasoning_content"] = think_text
        asst["tool_calls"] = tool_calls
        messages.append(asst)
        for tc in tool_calls:
            fn = tc.get("function", {})
            tname = fn.get("name", "")
            raw_args = fn.get("arguments")
            if isinstance(raw_args, (dict, list)):
                args, parse_err = raw_args, ""      # 有的适配器直接给参数对象
            else:
                raw_args = raw_args or ""
                if not raw_args.strip():
                    args, parse_err = {}, ""
                else:
                    import json
                    try:
                        args, parse_err = json.loads(raw_args), ""
                    except ValueError as e:
                        args, parse_err = None, str(e)
            if parse_err:
                text = ("本次调用的参数不是合法 JSON 收到 %d 字，%s。多半是内容太长被截断了："
                        "请把内容减到十几条以内、缩短单条正文，再重新调用 %s。"
                        % (len(raw_args), parse_err, tname))
                CONSOLE.log("ai", "工具参数不是合法 JSON",
                            "%s 收到 %d 字：%s…" % (tname, len(raw_args), raw_args[:160]))
                log.append({"kind": "tool", "name": tname, "args": raw_args[:2000],
                            "ok": False, "error": "参数不是合法 JSON %d 字" % len(raw_args),
                            "result": text})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": text})
                continue
            try:
                text, got = ai_tools.dispatch(index, state, session, tname, args,
                                              payload_keys=payload_keys)
                ok, err = True, ""
            except (ai_tools.ToolArgsError, ai_tools.ToolOutputError,
                    ToolNotFound, KeyError, ValueError) as e:
                # 工具报错如实回执，模型据此改参数再试
                text, got, ok, err = "%s：%s" % (type(e).__name__, e), None, False, str(e)
            except Exception as e:                      # 工具自身出错也不吞
                text, got, ok, err = "%s：%s" % (type(e).__name__, e), None, False, str(e)
                CONSOLE.log("ai", "工具执行出错", "%s：%r" % (tname, e))
            log.append({"kind": "tool", "name": tname, "args": args,
                        "ok": ok, "error": err, "result": text})
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": text})
            if ok and tname == finish_name:
                value = (finish_handler(args, state, session) if finish_handler else got)
                return {"value": value, "log": log, "error": "", "rounds": rounds}
    if value is None:
        error = ("调用次数用尽，模型始终没调用 %s" % finish_name if rounds >= max_calls
                 else "模型未调用 %s，本次不落库" % finish_name)
    return {"value": value, "log": log, "error": error, "rounds": rounds}
