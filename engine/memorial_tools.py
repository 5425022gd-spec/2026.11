# -*- coding: utf-8 -*-
"""章奏生成 次要事件 m 层交给 AI 的工具：定义与实现同源，一个工具只干一件事。

与剧本那套共用 `engine/ai_tools.py` 的内核 defineTool 口径与
`engine/ai_workflow.py` 的往返循环；本模块只写这一套自己的三个工具：

    list_variations      取近期变化点清单 原来是提示词里塞 {{event_point}}
    get_variation_scope  取某个变化点绑定的超控指令范围 原来没有落实，见 variations.py 的设计
    get_reward_rules     取系统奖惩规则包 原来是提示词里塞 {{formula}}
    finish_memorial      交卷：一条章奏的全部字段 原来是要求模型回 JSONL

**工具怎么用、要回什么字段，只写在 description 里**；提示词只放提示词与数据变量。
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from . import ai_tools as _ai
from .ai_tools import ToolArgsError, ToolNotFound, define_tool

# 两个标签枚举 玩家写在 Prompt/zhixian.txt 的 <rules2> 里，这里作为交卷工具的取值收口，
# 是本项目唯一的枚举来源：提示词、工具说明、校验都用它
CATEGORY_LABELS = ["题本", "奏本", "表", "揭帖", "塘报", "露布"]
CONTENT_LABELS = ["钱粮类", "人事类", "刑名类", "军务类", "工程类", "奏本类",
                  "弹劾疏", "规谏疏", "灾异奏报", "边情塘报", "密揭", "请封疏"]

# 交卷字段 剥信封时用它认内层
FINISH_PAYLOAD_KEYS = ("variation_id", "title", "full_text")

# 变化池为空时给模型的三条地方微观异动 与 memorials.extract_event_points 同一份。
# 它们不在池子里，类型固定为 local_rumor：不绑定任何超控指令，只作题材。
FALLBACK_VARIATIONS = [
    {"id": "var-001", "province": "陕西", "text": "【陕西】赋税催科急迫，民情浮动",
     "variation_type": "local_rumor", "bound": False},
    {"id": "var-002", "province": "山东", "text": "【山东】登莱海防戒严，有司查缉私盐",
     "variation_type": "local_rumor", "bound": False},
    {"id": "var-003", "province": "四川", "text": "【四川】川东粮台转运稍迟，驻军严查关隘",
     "variation_type": "local_rumor", "bound": False},
]


# ═══════════════════════════════════════════════════════════
# 取数：近期变化点清单
# ═══════════════════════════════════════════════════════════

def list_variation_rows(state: Any, limit: int = 40) -> List[Dict[str, Any]]:
    """近期 30 天变化点清单；池子为空时给地方风闻三条。"""
    from .variations import recent_variations

    rows: List[Dict[str, Any]] = []
    for v in recent_variations(state, days=30):
        text = v.get("text") or "地方民政异动"
        rows.append({"variation_id": v.get("id") or "var-000",
                     "province": v.get("province") or "",
                     "variation_type": v.get("type") or "",
                     "date": v.get("date") or "",
                     "text": text,
                     "bound": True})
    if not rows:
        # 兜底行必须与池内行同形（variation_id / variation_type / bound 齐备），
        # 否则 get_variation_scope 拿到的是另一个形状的字典
        rows = [{"variation_id": x["id"],
                 "province": x.get("province") or "",
                 "variation_type": x.get("variation_type") or "local_rumor",
                 "date": "",
                 "text": x["text"],
                 "bound": bool(x.get("bound"))} for x in FALLBACK_VARIATIONS]
    return rows[:limit]


def find_variation(state: Any, variation_id: str) -> Dict[str, Any]:
    """按 id 找变化点；找不到就报错 不许编一个类型蒙过去。"""
    for row in list_variation_rows(state, limit=200):
        if row["variation_id"] == variation_id:
            return row
    raise ToolArgsError("近期变化清单里没有这个 variation_id：%s。"
                        "请先调用 list_variations 看清单，从中挑一条。" % variation_id)


def _scope_of(row: Dict[str, Any]) -> Dict[str, Any]:
    """某个变化点绑定的超控范围 类型由变化池的 type 决定，缺省按文字关键字现算。"""
    from .variations import (VARIATION_SCHEMA_MAP, normalize_variation_type,
                             get_scoped_operations_schema)

    text = row.get("text") or ""
    vtype = normalize_variation_type(row.get("variation_type") or "", text)
    spec = VARIATION_SCHEMA_MAP.get(vtype) or {}
    province = row.get("province") or ""
    if not province:
        import re
        m = re.match(r"^【([^】]+)】", text)
        province = m.group(1) if m else ""
    return {
        "variation_id": row["variation_id"],
        "variation_type": vtype,
        "type_name": spec.get("name") or "地方民政异动",
        "province": province,
        "text": text,
        "allowed_commands": list(spec.get("allowed_commands") or []),
        "schema_text": get_scoped_operations_schema(vtype, province),
        "bound": bool(row.get("bound", True)),
    }


# ═══════════════════════════════════════════════════════════
# 工具一：近期变化清单
# ═══════════════════════════════════════════════════════════

def _execute_list(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    rows = list_variation_rows(state, limit=int(args.get("limit") or 40))
    return {"variations": rows, "total": len(rows),
            "note": ("变化池里目前没有近 30 天的异动，以下是地方风闻，"
                     "可作题材但不绑定超控范围" if rows and not rows[0].get("bound") else "")}


def _render_list(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    lines = ["【近期变化清单】共 %d 条" % value["total"]]
    for r in value["variations"]:
        tail = " %s" % r["date"] if r.get("date") else ""
        lines.append("[%s] %s%s" % (r["variation_id"], r["text"], tail))
    if value.get("note"):
        lines.append(value["note"])
    lines.append ("挑定一条后，用 get_variation_scope 查它绑定的超控范围，再落笔。")
    return "\n".join(lines)


def _list_variations() -> Dict[str, Any]:
    return define_tool(
        "list_variations",
        "列出近 30 天的剧情变化点清单 章奏的题材只能从这里面挑。每条含 variation_id 与异动原文。"
        "清单为空时会给出三条地方风闻，那种情况不绑定超控范围。"
        "挑定一条后，用 get_variation_scope 查它绑定的超控指令范围。",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "最多返回几条，默认 40；一般不用填"},
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "variations": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "variation_id": {"type": "string"},
                        "province": {"type": "string"},
                        "variation_type": {"type": "string"},
                        "date": {"type": "string"},
                        "text": {"type": "string"},
                        "bound": {"type": "boolean"},
                    },
                    "required": ["variation_id", "text"],
                    "additionalProperties": False,
                }},
                "total": {"type": "integer"},
                "note": {"type": "string"},
            },
            "required": ["variations", "total", "note"],
            "additionalProperties": False,
        },
        _render_list,
        _execute_list,
    )


# ═══════════════════════════════════════════════════════════
# 工具二：某个变化点绑定的超控范围
# ═══════════════════════════════════════════════════════════

def _execute_scope(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    return _scope_of(find_variation(state, args["variation_id"]))


def _render_scope(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    lines = ["【%s】变化类型：%s %s"
             % (value["variation_id"], value["type_name"], value["variation_type"])]
    if value.get("allowed_commands"):
        lines.append("本变化点允许使用的超控指令：" + "、".join(value["allowed_commands"]))
    lines.append(value["schema_text"])
    return "\n".join(lines)


def _get_variation_scope() -> Dict[str, Any]:
    return define_tool(
        "get_variation_scope",
        "查某个变化点 variation_id绑定的系统超控指令范围：该变化允许动哪些变量、指令怎么写。"
        "写章奏正文与奖惩效果时，凡涉及改动系统状态，只能在这个范围内写，越权的指令引擎会拒绝执行。"
        "variation_id 必须取自 list_variations 的清单。",
        {
            "type": "object",
            "properties": {
                "variation_id": {"type": "string",
                                 "description": "list_variations 给出的变化点 ID，如 var-001"},
            },
            "required": ["variation_id"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "variation_id": {"type": "string"},
                "variation_type": {"type": "string"},
                "type_name": {"type": "string"},
                "province": {"type": "string"},
                "text": {"type": "string"},
                "allowed_commands": {"type": "array", "items": {"type": "string"}},
                "schema_text": {"type": "string"},
                "bound": {"type": "boolean"},
            },
            "required": ["variation_id", "variation_type", "type_name", "province",
                         "text", "allowed_commands", "schema_text", "bound"],
            "additionalProperties": False,
        },
        _render_scope,
        _execute_scope,
    )


# ═══════════════════════════════════════════════════════════
# 工具三：系统奖惩规则包
# ═══════════════════════════════════════════════════════════

def _execute_rules(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    from .formula import get_formula_rules
    return {"text": get_formula_rules()}


def _get_reward_rules() -> Dict[str, Any]:
    return define_tool(
        "get_reward_rules",
        "取系统奖惩规则包：写 draft_processed / draft_unprocessed 的处置方案，"
        "以及 reward_processed / reward_unprocessed 的奖惩效果时，必须按这份规则里的十类正负操作来描述，"
        "数值范围也要照它说的写。",
        {"type": "object", "properties": {}, "additionalProperties": False},
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        lambda args, value: value["text"],
        _execute_rules,
    )


# ═══════════════════════════════════════════════════════════
# 工具四：交卷
# ═══════════════════════════════════════════════════════════

def _slots_spec() -> str:
    """正文里 {{时间，地点，数据类}} 三要素空位的写法 数据类来源在 engine/slots.py。"""
    from .slots import build_element_text, build_province_text
    return ("正文里可以直接留数据空位，写作 {{时间，地点，数据类}} 三要素，"
            "引擎会把真实数据填进去；三要素缺任何一个都会被原样留在正文里。\n"
            "  时间写成 年+月+日，如 1627年8月1日\n"
            "  地点写「国家」或十六省之一：" + build_province_text() + "\n"
            "  " + build_element_text())


def _finish_description() -> str:
    return (
        "交回一条章奏，这是唯一的交卷通道：一旦调用，系统立刻停止调用模型，用这次提交的内容落库。\n\n"
        "【怎么传参】下面这些字段要**直接**作为本次调用的参数传入，不要再包一层 arguments 或 function。\n"
        "  variation_id：挑中的变化点 ID，必须取自 list_variations 的清单\n"
        "  title：章奏标题，如 题本 陕西旱灾异常疏\n"
        "  category_label：分类标签，只能取 " + "、".join(CATEGORY_LABELS) + "\n"
        "  content_label：内容标签，只能取 " + "、".join(CONTENT_LABELS) + "\n"
        "  location：事发地，至少精确到府一级，如 陕西西安府\n"
        "  full_text：章奏正文，不少于 150 字，半文半白的明代公文语调\n"
        "  draft_processed：内阁积极处理的票拟，写明处置方案与缘由\n"
        "  draft_unprocessed：内阁消极处理的票拟，写明缓办或留中缘由\n"
        "  reward_processed：积极处理时的奖惩效果描述\n"
        "  reward_unprocessed：消极处理时的后果描述\n"
        "缺任何一个字段都会被退回，退回时系统会写明缺了什么、该怎么传，照提示补齐后再调一次。\n\n"
        "【数据严谨度】数字要符合地方实际，用明代语境的模糊数或具体款额，"
        "如「死伤二十余人」「被勒银四百余两」；严禁不合逻辑的夸张大数。\n\n"
        "【人物】知府及以下官员可拟具体姓名，巡抚及部院大员要符合明代体统。\n\n"
        "【数据空位】" + _slots_spec() + "\n\n"
        "【奖惩写法】reward 两项要按 get_reward_rules 给的十类正负操作来写实际影响。"
    )


def _execute_finish(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    """整理成一条章奏事件 键名与 engine/memorials.py 的次要事件结构一致。"""
    return {
        "variation_id": str(args.get("variation_id") or "").strip(),
        "title": str(args.get("title") or "").strip(),
        "category_label": str(args.get("category_label") or "").strip(),
        "content_label": str(args.get("content_label") or "").strip(),
        "Location": str(args.get("location") or "").strip(),
        "full_text": str(args.get("full_text") or "").strip(),
        "draft_processed": str(args.get("draft_processed") or "").strip(),
        "draft_unprocessed": str(args.get("draft_unprocessed") or "").strip(),
        "reward_processed": str(args.get("reward_processed") or "").strip(),
        "reward_unprocessed": str(args.get("reward_unprocessed") or "").strip(),
    }


def _render_finish(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    return "已收到章奏《%s》，绑定变化点 %s，正文 %d 字。%s" % (
        value["title"], value["variation_id"], len(value["full_text"]),
        " 未过字数下限，引擎会照收但请下不为例" if len(value["full_text"]) < 150 else "")


def _finish_memorial() -> Dict[str, Any]:
    return define_tool(
        "finish_memorial",
        _finish_description(),
        {
            "type": "object",
            "properties": {
                "variation_id": {"type": "string",
                                 "description": "挑中的变化点 ID 取自 list_variations"},
                "title": {"type": "string", "description": "章奏标题，如 题本 陕西旱灾异常疏"},
                "category_label": {"type": "string", "enum": CATEGORY_LABELS,
                                   "description": "分类标签"},
                "content_label": {"type": "string", "enum": CONTENT_LABELS,
                                  "description": "内容标签"},
                "location": {"type": "string", "description": "事发地，至少精确到府，如 陕西西安府"},
                "full_text": {"type": "string", "description": "章奏正文，不少于 150 字"},
                "draft_processed": {"type": "string", "description": "内阁积极处理的票拟"},
                "draft_unprocessed": {"type": "string", "description": "内阁消极处理的票拟"},
                "reward_processed": {"type": "string", "description": "积极处理时的奖惩效果描述"},
                "reward_unprocessed": {"type": "string", "description": "消极处理时的后果描述"},
            },
            "required": ["variation_id", "title", "category_label", "content_label",
                         "location", "full_text", "draft_processed", "draft_unprocessed",
                         "reward_processed", "reward_unprocessed"],
        },
        {
            "type": "object",
            "properties": {
                "variation_id": {"type": "string"},
                "title": {"type": "string"},
                "category_label": {"type": "string"},
                "content_label": {"type": "string"},
                "Location": {"type": "string"},
                "full_text": {"type": "string"},
                "draft_processed": {"type": "string"},
                "draft_unprocessed": {"type": "string"},
                "reward_processed": {"type": "string"},
                "reward_unprocessed": {"type": "string"},
            },
            "required": ["variation_id", "title", "category_label", "content_label",
                         "Location", "full_text", "draft_processed", "draft_unprocessed",
                         "reward_processed", "reward_unprocessed"],
            "additionalProperties": False,
        },
        _render_finish,
        _execute_finish,
    )


# ═══════════════════════════════════════════════════════════
# 对外
# ═══════════════════════════════════════════════════════════

TOOLS: List[Dict[str, Any]] = [
    _list_variations(),
    _get_variation_scope(),
    _get_reward_rules(),
    _finish_memorial(),
]

TOOL_INDEX: Dict[str, Dict[str, Any]] = _ai.tool_index(TOOLS)
TOOL_FINISH = "finish_memorial"


def tools_payload() -> List[Dict[str, Any]]:
    """发给模型的工具数组：只含 name / description / parameters。"""
    return _ai.tools_payload(TOOLS)


def dispatch(state: Any, session: Any, name: str, args: Any) -> Tuple[str, Any]:
    """派发一次调用 内核在 ai_tools。"""
    return _ai.dispatch(TOOL_INDEX, state, session, name, args,
                        payload_keys=FINISH_PAYLOAD_KEYS)


def finish_event(args: Any, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """交卷整理：把 finish_memorial 的参数整成一条章奏事件。

    参数被裹成信封时先剥一层；缺字段就报错并写明该怎么传，不编造内容。
    """
    args, _unwrapped = _ai.unwrap_envelope(args, TOOL_FINISH, FINISH_PAYLOAD_KEYS)
    try:
        checked = _ai.validate_args(TOOL_INDEX[TOOL_FINISH]["parameters"], args)
    except ToolArgsError as e:
        got = "、".join(sorted(args)) if isinstance(args, dict) and args else "空"
        raise ToolArgsError(
            "%s。本次收到的字段：%s。章奏要的这些字段要**直接**作为 %s 的参数传入，"
            "不要再包一层 arguments 或 function。" % (e, got, TOOL_FINISH))
    return _execute_finish(checked, state, session)
