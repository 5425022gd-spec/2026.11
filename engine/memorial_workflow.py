# -*- coding: utf-8 -*-
"""章奏生成 次要事件 m 层的 AI 工作流：多轮工具往返，直到调用交卷工具。

与剧本那套共用 `engine/ai_workflow.run_tool_workflow`：本模块只负责
选提示词、给工具、按条数反复跑到交卷，并把轨迹交给调用方记账。

一次工作流产出**一条**章奏；要几条就按条数各自跑一次 同一份提示词，
每次追一句"不要与已写的重复"，这样每次交卷都是一个干净的、可单独校验的产物。
"""

from typing import Any, Dict, List, Optional

from .logger import CONSOLE
from . import memorial_tools as _tools
from .ai_workflow import run_tool_workflow

TOOL_FINISH = _tools.TOOL_FINISH

MAX_MODEL_CALLS = 12        # 一条章奏最多请求模型多少轮，防止模型打转

SYSTEM_TEXT = "你现在是明朝奏章生成器，通过工具调用查阅变化清单与规则，再撰写章奏交卷。"


def run_memorial_workflow(state: Any, llm: Any, prompt: str, *,
                          session: Any = None,
                          extra_instruction: str = "",
                          already_titles: Optional[List[str]] = None,
                          max_calls: int = MAX_MODEL_CALLS) -> Dict[str, Any]:
    """跑一次章奏工作流，返回 {event, log, error}。event 为 None 表示没交卷。"""
    user = prompt
    if already_titles:
        user += ("\n<already_written>\n本次已经写过的章奏 不要重复：%s\n</already_written>\n"
                 % "、".join(already_titles))
    if extra_instruction:
        user += "\n<extra_instruction>\n%s\n</extra_instruction>\n" % extra_instruction

    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_TEXT},
                                      {"role": "user", "content": user}]
    log: List[Dict[str, Any]] = [{"kind": "prompt", "file": "zhixian.txt", "text": user},
                                 {"kind": "tools",
                                  "names": [t["name"] for t in _tools.TOOLS]}]
    result = run_tool_workflow(
        llm, messages, _tools.TOOLS, state=state, session=session,
        finish_name=TOOL_FINISH, max_calls=max_calls,
        payload_keys=_tools.FINISH_PAYLOAD_KEYS,
        finish_handler=_tools.finish_event, log=log)
    if result["value"] is None:
        CONSOLE.log("side", "章奏工作流没交卷", result["error"])
    return {"event": result["value"], "log": log, "error": result["error"]}
