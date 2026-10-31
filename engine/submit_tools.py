# -*- coding: utf-8 -*-
"""各处 AI 的**交卷工具**：结构化结果一律走工具调用，不再让模型回 JSONL。

口径（本项目贯穿全站）：
- 工具就是模型的函数调用能力，不是"贴在提示词末尾的格式说明"；
- **要回什么字段、字段怎么写，只写在工具 description 里**；
- 提示词只放提示词文字与数据变量，不再有 <output_format_rules>；
- 交卷工具一被调用，工作流立刻收工，用它的参数落库。

目前四件交卷：
    submit_speeches          廷议 / 召对：参会大臣的发言
    submit_monthly_summary   月度世界总结与大盘更新
    submit_refusal           机构拒绝执行中旨的抗疏
    submit_quarter_plan      国家 AI 的季度方针与活动清单
"""

from typing import Any, Dict, List, Optional, Tuple

from . import ai_tools as _ai
from .ai_tools import ToolArgsError, define_tool

FINISH_SPEECHES = "submit_speeches"
FINISH_MONTHLY = "submit_monthly_summary"
FINISH_REFUSAL = "submit_refusal"
FINISH_QUARTER = "submit_quarter_plan"


# ═══════════════════════════════════════════════════════════
# 一、廷议 / 召对发言
# ═══════════════════════════════════════════════════════════

def _execute_speeches(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    rows = args.get("speeches") or []
    # 本轮该谁说话由引擎决定：上限等于实际被抽中的人数，不再写死 3
    allowed: List[str] = []
    council = getattr(session, "council", None) if session is not None else None
    for pid in (getattr(council, "last_picked", None) or []):
        try:
            from .characters import get_character
            c = get_character(pid)
            if c:
                allowed.append(c["name"])
        except Exception:                          # noqa: BLE001 取不到名字就不校验
            pass
    limit = len(allowed) or len(rows) or 1
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        text = str(r.get("text") or "").strip()
        expr = str(r.get("expr") or "").strip()
        if not (name and text):
            continue
        if allowed and name not in allowed:
            raise ToolArgsError("「%s」不在本轮发言名单里（本轮是 %s），请只写这几位的话。"
                                % (name, "、".join(allowed)))
        row = {"name": name, "text": text}
        if expr:
            row["expr"] = expr
        out.append(row)
    if not out:
        raise ToolArgsError("speeches 里没有一条既有人名又有发言内容的记录，请按选中的大臣重写。")
    return {"speeches": out[:limit]}


def _render_speeches(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    return "已收到 %d 位大臣的发言：%s" % (
        len(value["speeches"]), "、".join(s["name"] for s in value["speeches"]))


def _submit_speeches() -> Dict[str, Any]:
    return define_tool(
        FINISH_SPEECHES,
        "交回本次轮到的参会大臣发言，最多三条，这是唯一的交卷通道：一被调用系统立刻收工。\n"
        "每条的 name 必须与本次指定的大臣姓名完全一致，text 是他本人的发言，100 字左右，言之有物、切中要害。\n"
        "发言里凡涉及具体数据，先用 query_data 查证，不许凭空编数字。",
        {
            "type": "object",
            "properties": {
                "speeches": {
                    "type": "array",
                    "description": "本次发言，条数等于本轮被点名的人数；每条含 name 与 text",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "发言大臣姓名，必须与指定的姓名一致"},
                            "text": {"type": "string", "description": "发言正文，100 字左右"},
                            "expr": {"type": "string",
                                     "description": "可选：本人在本轮的立绘表情。只在他表情发生变化时给出，"
                                                    "取值必须是其资料卡里「可选表情」之一；没导入表情的角色不要写这个字段"},
                        },
                        "required": ["name", "text"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["speeches"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "speeches": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "text": {"type": "string"},
                                   "expr": {"type": "string"}},
                    "required": ["name", "text"],
                    "additionalProperties": False,
                }},
            },
            "required": ["speeches"],
            "additionalProperties": False,
        },
        _render_speeches,
        _execute_speeches,
    )


# ═══════════════════════════════════════════════════════════
# 二、月度世界总结
# ═══════════════════════════════════════════════════════════

def _execute_monthly(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    report = str(args.get("monthly_report") or "").strip()
    if not report:
        raise ToolArgsError("monthly_report 不能为空：月报正文要写 300-400 字。")
    officials: List[Dict[str, Any]] = []
    for r in (args.get("officials_updates") or []):
        if isinstance(r, dict) and str(r.get("name") or "").strip():
            officials.append({k: v for k, v in r.items() if v not in (None, "")})
    trade: List[Dict[str, Any]] = []
    for r in (args.get("trade_updates") or []):
        if isinstance(r, dict) and (r.get("id") or r.get("name")):
            trade.append(dict(r))
    return {"monthly_report": report, "officials_updates": officials, "trade_updates": trade}


def _render_monthly(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    return "已收到月度总结：月报 %d 字，官员更新 %d 条，贸易更新 %d 条。" % (
        len(value["monthly_report"]), len(value["officials_updates"]), len(value["trade_updates"]))


def _submit_monthly_summary() -> Dict[str, Any]:
    return define_tool(
        FINISH_MONTHLY,
        "交回上月的世界总结与大盘更新，这是唯一的交卷通道：一被调用系统立刻收工。\n"
        "monthly_report：以史官起居注与明代公文体裁写的上月天下大势总结，300-400 字，总揽朝局、财赋、边防与天灾民情。\n"
        "officials_updates：本月身份、官职、党派或所在省份有变的官员，每条只写有变的字段；"
        "name 必填，location 只写省份名不写府县。没有变化就不传这个参数。\n"
        "trade_updates：进出口贸易项的修正变化，type 取 export 或 import，id 或 name 至少填一个，"
        "flat_mod 是加减修正，pct_mod 是百分比修正。没有变化就不传这个参数。",
        {
            "type": "object",
            "properties": {
                "monthly_report": {"type": "string", "description": "月报正文，300-400 字"},
                "officials_updates": {
                    "type": "array",
                    "description": "有变动的官员，没有就不传",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "官员姓名"},
                            "faction": {"type": "string", "description": "更新后的党派"},
                            "office": {"type": "string", "description": "更新后的官职"},
                            "location": {"type": "string", "description": "更新后的所在省份"},
                            "personality": {"type": "string", "description": "更新后的性格简述"},
                            "bio_update": {"type": "string", "description": "生平时间线追加"},
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
                "trade_updates": {
                    "type": "array",
                    "description": "有变动的贸易项，没有就不传",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["export", "import"]},
                            "id": {"type": "string", "description": "如 export_tea"},
                            "name": {"type": "string", "description": "商品名称"},
                            "flat_mod": {"type": "number", "description": "加减修正"},
                            "pct_mod": {"type": "number", "description": "百分比修正，如 0.1"},
                        },
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["monthly_report"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "monthly_report": {"type": "string"},
                "officials_updates": {"type": "array", "items": {"type": "object"}},
                "trade_updates": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["monthly_report", "officials_updates", "trade_updates"],
            "additionalProperties": False,
        },
        _render_monthly,
        _execute_monthly,
    )


# ═══════════════════════════════════════════════════════════
# 三、机构抗旨（拒绝执行中旨）
# ═══════════════════════════════════════════════════════════

def _execute_refusal(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    title = str(args.get("title") or "").strip()
    full_text = str(args.get("full_text") or "").strip()
    if not title or not full_text:
        raise ToolArgsError("title 与 full_text 都要填：抗疏要有标题，也要有正文。")
    return {"title": title, "Location": str(args.get("location") or "京师户部").strip(),
            "full_text": full_text}


def _render_refusal(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    return "已收到抗疏《%s》，正文 %d 字，落款 %s。" % (
        value["title"], len(value["full_text"]), value["Location"])


def _submit_refusal() -> Dict[str, Any]:
    return define_tool(
        FINISH_REFUSAL,
        "交回这份拒绝执行中旨的抗疏，这是唯一的交卷通道：一被调用系统立刻收工。\n"
        "title：抗疏标题，明代题本写法。\n"
        "location：抗旨部门所在地点，如 京师户部。\n"
        "full_text：抗疏正文，100 字左右，语气严正、半文半白，引朝政体制规制说明"
        "「旨意不经封驳、难以遵行」；知府及以下可编人名，知府以上只用官职代称；数据从模糊处理。",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "抗疏标题"},
                "location": {"type": "string", "description": "抗旨部门所在地点，如 京师户部"},
                "full_text": {"type": "string", "description": "抗疏正文，100 字左右"},
            },
            "required": ["title", "location", "full_text"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"title": {"type": "string"}, "Location": {"type": "string"},
                           "full_text": {"type": "string"}},
            "required": ["title", "Location", "full_text"],
            "additionalProperties": False,
        },
        _render_refusal,
        _execute_refusal,
    )


# ═══════════════════════════════════════════════════════════
# 四、国家 AI 的季度决策
# ═══════════════════════════════════════════════════════════

def _execute_quarter(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    intent = str(args.get("quarter_intent") or "").strip()
    if not intent:
        raise ToolArgsError("quarter_intent 不能为空：要先写清本季总方针。")
    events = []
    for r in (args.get("events") or []):
        if not isinstance(r, dict):
            continue
        ops = str(r.get("operations_ai") or "").strip()
        if not ops:
            continue
        try:
            day = int(r.get("day") or 1)
        except (TypeError, ValueError):
            day = 1
        events.append({"day": max(1, day), "operations_ai": ops})
    return {"quarter_intent": intent, "events": events}


def _render_quarter(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    return "已收到本季方针与 %d 项活动。" % len(value["events"])


def _submit_quarter_plan() -> Dict[str, Any]:
    return define_tool(
        FINISH_QUARTER,
        "交回本国本季的方针与活动清单，这是唯一的交卷通道：一被调用系统立刻收工。\n"
        "quarter_intent：上帝视角的本季总方针，可顺带总结过去、写下留给下次的思考，不限字数。\n"
        "events：本季逐项活动，day 是活动在本季第几日（本季第一日为 1），"
        "operations_ai 是当天要执行的超控指令内容，只能写 <operation_ai> 里给出的合法指令；"
        "没有活动就不传这个参数。",
        {
            "type": "object",
            "properties": {
                "quarter_intent": {"type": "string", "description": "本季总方针"},
                "events": {
                    "type": "array",
                    "description": "本季活动清单，没有就不传",
                    "items": {
                        "type": "object",
                        "properties": {
                            "day": {"type": "integer", "description": "本季第几日，第一日为 1"},
                            "operations_ai": {"type": "string", "description": "当日的超控指令内容"},
                        },
                        "required": ["day", "operations_ai"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["quarter_intent"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "quarter_intent": {"type": "string"},
                "events": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"day": {"type": "integer"}, "operations_ai": {"type": "string"}},
                    "required": ["day", "operations_ai"],
                    "additionalProperties": False,
                }},
            },
            "required": ["quarter_intent", "events"],
            "additionalProperties": False,
        },
        _render_quarter,
        _execute_quarter,
    )


# ═══════════════════════════════════════════════════════════
# 对外
# ═══════════════════════════════════════════════════════════

def finish_speeches(args: Any, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """把 submit_speeches 的参数整成发言列表：参数被裹成信封时先剥一层，缺字段就报错。"""
    args, _ = _ai.unwrap_envelope(args, FINISH_SPEECHES, ("speeches",))
    try:
        checked = _ai.validate_args(
            _ai.tool_index([_submit_speeches()])[FINISH_SPEECHES]["parameters"], args)
    except ToolArgsError as e:
        got = "、".join(sorted(args)) if isinstance(args, dict) and args else "空"
        raise ToolArgsError("%s。本次收到的字段：%s。发言要放在 speeches 里直接传。" % (e, got))
    return _execute_speeches(checked, state, session)


def speeches_tool() -> Dict[str, Any]:
    return _submit_speeches()


def monthly_tool() -> Dict[str, Any]:
    return _submit_monthly_summary()


def refusal_tool() -> Dict[str, Any]:
    return _submit_refusal()


def quarter_tool() -> Dict[str, Any]:
    return _submit_quarter_plan()
