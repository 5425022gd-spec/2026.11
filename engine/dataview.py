"""数据查询工具 供 AI tool 调用，群策会议用。

独立于支线留空模块 slots.py，不参与支线生成逻辑。
群策会议中 AI 可在思考中调用 query_data 工具查询数值：

- 实时 缺省 date：取最新快照值
- 历史 传 date，格式「年+月+日」，如 1627年8月1日：
  取不晚于该日的最近一条实际记录 回溯，与支线留空规则一致
参数 place 全局/省名+ element 数据类，返回 值+单位 文本。
"""

import re
from typing import Any, Dict


from .slots import ELEMENTS, PROVINCES

_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")

# 工具定义：与全站同一套 define_tool 口径（name/description/parameters 发给模型，
# output.schema 约束返回值，render 渲染成模型看到的文字）——不在提示词里写格式说明。
from .ai_tools import ToolArgsError, define_tool


def _execute_query(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    snaps = getattr(session, "snapshots", None) or []
    text = query(args.get("place", ""), args.get("element", ""), snaps, date=args.get("date"))
    if text is None:
        raise ToolArgsError(
            "查无此项：place 取「全局」或省名（%s），element 取数据类名，"
            "两样都要与清单里的写法一致。" % "、".join(PROVINCES))
    return {"text": text}


def _query_data() -> Dict[str, Any]:
    return define_tool(
        "query_data",
        "查阅数据。place 取「全局」或省名："
        + "、".join(PROVINCES) + "。"
        "element 数据类：全局类-内库余额/人口/陆军人数/海军人数（place 必须为全局）；"
        "省份类-地方人口/镇军人数/基建程度/税收总额/扶持总额（place 必须为省名）。"
        "date 可选：查历史某日数据，格式「年+月+日」如 1627年8月1日，"
        "取不晚于该日的最近实际记录；缺省查当前实时值。返回值为数值加单位。",
        {
            "type": "object",
            "properties": {
                "place": {"type": "string", "description": "全局 或 省名"},
                "element": {"type": "string", "description": "数据类名"},
                "date": {"type": "string",
                         "description": "可选。历史日期，格式 年+月+日，如 1627年8月1日；缺省为当前实时"},
            },
            "required": ["place", "element"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        lambda args, value: value["text"],
        _execute_query,
    )


DATA_TOOLS = [_query_data()]


def _find_snapshot(snaps, y, mo, d):
    """回溯查找：不晚于目标日期、且最接近的实际记录 snaps 按时间有序。"""
    best = None
    for s in snaps:
        if (s["year"], s["month"], s["day"]) <= (y, mo, d):
            best = s
        else:
            break
    return best


def query(place, element, snaps, date=None):
    """查值 -> 值+单位 文本；无法解析返回 None。

    例：query(("全局", "内库余额", snaps          -> "240.0万两" 实时))
        query ((("江西", "镇军人数", snaps))),
              date="1627年5月1日"               -> "1.2万人" 历史回溯
    """
    place = (place or "").strip()
    element = (element or "").strip()
    if place in  ("全局", "国家"):
        place = "国家"
    if element not in ELEMENTS:
        return None
    if not snaps:
        return None
    meta = ELEMENTS[element]

    if date:
        m = _DATE_RE.search(date)
        if not m:
            return None
        snap = _find_snapshot(snaps, int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if snap is None:
            return None
    else:
        snap = snaps[-1]  # 实时：最新快照

    if meta["province"]:
        if place not in PROVINCES:
            return None
        v = (snap.get("provinces") or {}).get(place, {}).get(meta["field"])
    else:
        if place != "国家":
            return None
        v = snap.get(meta["field"])
    if v is None:
        return None
    num = meta["fmt"] % v
    return num + meta["unit"] if meta["unit"] else num
