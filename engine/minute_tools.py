# -*- coding: utf-8 -*-
"""召对／廷议的归档纪要交给模型的工具：把"JSONL 纪要"收成一个交卷工具。

`Prompt/meeting_2.txt` 原来要求模型回 JSONL，
归档时 `summary` 字段直接存的就是那段 JSONL 原文 月报 `{{council_summaries}}` 取它前 80 字，
于是 JSON 片段串进了月报。现在：
- 纪要字段进 `finish_minutes` 的 parameters，规范进 description；
- 归档用的 `summary` 由引擎从条目拼成人话 不再存 JSONL；
- 条目的 `character` 必须是本次参会名单里的人 引擎校验，不靠提示词自律。
"""

from typing import Any, Dict, List, Tuple

from . import ai_tools as _ai
from .ai_tools import ToolArgsError, define_tool

TOOL_FINISH = "finish_minutes"
FINISH_PAYLOAD_KEYS = ("entries",)


def roster_names(session: Any) -> List[str]:
    """本次参会者姓名 口径在 council.Council.roster_names，这里只是取一下。"""
    council = getattr(session, "council", None)
    if council is not None and hasattr(council, "roster_names"):
        return list(council.roster_names())
    return []


def _execute_finish(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    from .time import date_key
    stamp = date_key(state.date) if state is not None and getattr(state, "date", None) else ""
    entries: List[Dict[str, Any]] = []
    for e in args.get("entries") or []:
        entries.append({
            "character": str(e.get("character") or "").strip(),
            "topic": str(e.get("topic") or "").strip(),
            "summary": str(e.get("summary") or "").strip(),
            "timestamp": stamp,          # 时间戳由引擎补，不由模型写
        })
    # 归档正文＝人话摘要 一条一句，不再把 JSONL 存进 summary 字段
    summary = "；".join("%s：%s" % (e["character"], e["summary"]) for e in entries if e["summary"])
    return {"entries": entries, "summary": summary}


def _render_finish(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    who = "、".join(e["character"] for e in value["entries"])
    return "已收到会议纪要：%d 条，分别记 %s。" % (len(value["entries"]), who)


def _finish_minutes() -> Dict[str, Any]:
    desc = (
        "交回本次会议的归档纪要，这是唯一的交卷通道：一旦调用，系统立刻停止调用模型并归档。\n\n"
        "【怎么传参】把纪要放进 entries 数组**直接**作为本次调用的参数传入，"
        "不要再包一层 arguments 或 function，也不要拼成 JSONL 文本。每个元素含：\n"
        "  character：发言者姓名，必须是本次参会名单里的人，写成别人会被退回\n"
        "  topic：本次议题 与会者讨论的那件事\n"
        "  summary：该大臣的观点归纳，一两句话，写清主张什么、反对什么、要点是什么\n"
        "时间戳由引擎按当前日期补上，你不用写。\n\n"
        "【要点】只归纳会上真实说过的观点，一条一人；"
        "参会名单之外的人、会上没出现过的观点都不要写；"
        "条目数按实际发言人数给，不要漏掉主要人物。"
    )
    entry_schema = {
        "type": "object",
        "properties": {
            "character": {"type": "string", "description": "发言者姓名 须在参会名单内"},
            "topic": {"type": "string", "description": "本次议题"},
            "summary": {"type": "string", "description": "该大臣观点归纳，一两句"},
        },
        "required": ["character", "topic", "summary"],
        "additionalProperties": False,
    }
    params = {
        "type": "object",
        "properties": {
            "entries": {"type": "array", "description": "会议纪要条目，一条一人",
                        "items": entry_schema},
        },
        "required": ["entries"],
    }
    out_schema = {
        "type": "object",
        "properties": {
            "entries": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "character": {"type": "string"},
                    "topic": {"type": "string"},
                    "summary": {"type": "string"},
                    "timestamp": {"type": "string"},
                },
                "required": ["character", "topic", "summary", "timestamp"],
                "additionalProperties": False,
            }},
            "summary": {"type": "string"},
        },
        "required": ["entries", "summary"],
        "additionalProperties": False,
    }
    return define_tool(TOOL_FINISH, desc, params, out_schema, _render_finish, _execute_finish)


TOOLS: List[Dict[str, Any]] = [_finish_minutes()]
TOOL_INDEX: Dict[str, Dict[str, Any]] = _ai.tool_index(TOOLS)


def tools_payload() -> List[Dict[str, Any]]:
    return _ai.tools_payload(TOOLS)


def dispatch(state: Any, session: Any, name: str, args: Any) -> Tuple[str, Any]:
    return _ai.dispatch(TOOL_INDEX, state, session, name, args,
                        payload_keys=FINISH_PAYLOAD_KEYS)


def finish_minutes(args: Any, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """交卷整理：校验字段与发言人姓名，返回 {entries, summary}。"""
    args, _unwrapped = _ai.unwrap_envelope(args, TOOL_FINISH, FINISH_PAYLOAD_KEYS)
    try:
        checked = _ai.validate_args(TOOL_INDEX[TOOL_FINISH]["parameters"], args)
    except ToolArgsError as e:
        got = "、".join(sorted(args)) if isinstance(args, dict) and args else "空"
        raise ToolArgsError(
            "%s。本次收到的字段：%s。纪要要放进 entries 数组**直接**作为 %s 的参数传入，"
            "不要再包一层 arguments 或 function。" % (e, got, TOOL_FINISH))
    value = _execute_finish(checked, state, session)
    if not value["entries"]:
        raise ToolArgsError(
            "entries 是空数组。会上有多少位大臣发了言，就写多少条纪要，至少一条。")
    names = roster_names(session)
    if names:
        bad = [e["character"] for e in value["entries"] if e["character"] not in names]
        if bad:
            raise ToolArgsError(
                "这些人不在本次参会名单里：%s。参会名单是：%s"
                % ("、".join(bad), "、".join(names)))
    return value
