# -*- coding: utf-8 -*-
"""剧本事件调度：时间 × 地点 × 人物 × 事件类型，四维正交。

设计口径（玩家定的）：
- 时间：无 / 剧本开始后 Δ / 获得某标签后 Δ / 固定时刻（精确到分钟）
- 地点：无 / 玩家在某建筑内 / 某标签不存在
- 人物：无 / 任选一至数人（名单由事件给定，引擎不猜）
- 事件：发布章奏 / 请求对话 / 强制对话 / 发布信件
- 系统事件：重新撰写剧本 / 广播标签（只吃时间，不解析地点与人物）

时间一律用**游戏内日期**（state.date 的年月日时分），与全站其它时间口径一致。
"""
from typing import Any, Dict, List, Optional, Tuple

# 事件类型 → 前端/会话层的处理方式
EVENT_TYPES = {
    "memorial": "发布一个章奏",
    "request_dialogue": "角色请求开始对话",
    "force_dialogue": "角色强制开始对话",
    "letter": "发布一个信件",
}
SYSTEM_TYPES = {
    "rewrite_script": "重新撰写一次剧本",
    "broadcast_tag": "广播一个标签",
}

# 引擎自动发布的标签前缀（固定格式，AI 不选）
TAG_EVENT_DONE = "事件结束"
TAG_LETTER_READ = "信件已读"
TAG_DIALOGUE_REFUSED = "对话被拒"
TAG_PURPOSE_FAILED = "未达目的"
TAG_MEMORIAL_AS_SCRIPTED = "章奏按剧本"
TAG_MEMORIAL_DEVIATED = "章奏偏离剧本"
TAG_PLACE_FAILED = "地点判定失败"


# ═════════════════════════════ 标签 ═════════════════════════════
def ensure_state(state: Any) -> None:
    """老存档补字段：script_tags = {标签: {"at": 时间键, "from": 事件id}}。"""
    if not isinstance(getattr(state, "script_tags", None), dict):
        state.script_tags = {}


def tag_key(state: Any) -> str:
    d = getattr(state, "date", {}) or {}
    return "%04d-%02d-%02d %02d:%02d" % (
        int(d.get("year", 0)), int(d.get("month", 0)), int(d.get("day", 0)),
        int(d.get("hour", 0)), int(d.get("minute", 0)))


def has_tag(state: Any, tag: str) -> bool:
    ensure_state(state)
    return tag in state.script_tags


def tag_time(state: Any, tag: str) -> Optional[str]:
    ensure_state(state)
    rec = state.script_tags.get(tag)
    return (rec or {}).get("at") if isinstance(rec, dict) else None


def publish_tag(state: Any, tag: str, source: str = "") -> str:
    """发一个标签。重复发布只更新时间，不重复记行。"""
    ensure_state(state)
    if not tag:
        return ""
    new = tag not in state.script_tags
    state.script_tags[tag] = {"at": tag_key(state), "from": source}
    return "【标签】%s%s" % (tag, "" if new else "（更新）")


# ═════════════════════════════ 时间 ═════════════════════════════
def _now_tuple(state: Any) -> Tuple[int, int, int, int, int]:
    d = getattr(state, "date", {}) or {}
    return (int(d.get("year", 0)), int(d.get("month", 0)), int(d.get("day", 0)),
            int(d.get("hour", 0)), int(d.get("minute", 0)))


def parse_stamp(text: str) -> Optional[Tuple[int, int, int, int, int]]:
    """"1627-09-01 08:30" / "1627-09-01" → (年,月,日,时,分)。"""
    if not text or not isinstance(text, str):
        return None
    body = text.strip().replace("T", " ")
    day_part, _, time_part = body.partition(" ")
    try:
        y, m, d = [int(x) for x in day_part.split("-")]
    except (ValueError, TypeError):
        return None
    hh = mm = 0
    if time_part:
        try:
            hh, mm = [int(x) for x in time_part.split(":")[:2]]
        except (ValueError, TypeError):
            hh = mm = 0
    return (y, m, d, hh, mm)


def add_delta(base: Tuple[int, int, int, int, int],
              days: int = 0, hours: int = 0, minutes: int = 0) -> Tuple[int, int, int, int, int]:
    """只在「分钟」这一层做加法，跨日按月 30 天粗算——事件排期不需要天文精度。"""
    total = base[4] + int(minutes) + (int(hours) + int(days) * 24) * 60
    minute = total % 60
    total_hours = base[3] + total // 60
    hour = total_hours % 24
    total_days = base[2] + total_hours // 24
    # 30 天一月，12 月一年：与游戏日历的粗粒度一致
    month = base[1] + (total_days - 1) // 30
    day = (total_days - 1) % 30 + 1
    year = base[0] + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return (year, month, day, hour, minute)


def _time_due(state: Any, cond: Dict[str, Any], started: Optional[Tuple[int, int, int, int, int]]):
    """返回 (是否到点, 说明)。缺省条件视为「无时间条件」，恒真。"""
    if not cond:
        return True, ""
    kind = cond.get("type") or ""
    now = _now_tuple(state)
    if kind == "at":
        target = parse_stamp(cond.get("at") or "")
        if not target:
            return False, "时间格式不对：%r" % cond.get("at")
        return now >= target, "到点 %s" % cond.get("at")
    if kind == "after_start":
        if started is None:
            return False, "剧本尚未开始"
        target = add_delta(started, cond.get("days", 0), cond.get("hours", 0), cond.get("minutes", 0))
        return now >= target, "剧本开始后 %s 天 %s 时 %s 分" % (
            cond.get("days", 0), cond.get("hours", 0), cond.get("minutes", 0))
    if kind == "after_tag":
        tag = cond.get("tag") or ""
        if not has_tag(state, tag):
            return False, "尚未获得标签：%s" % tag
        base = parse_stamp(tag_time(state, tag) or "")
        if not base:
            return False, "标签 %s 缺时间戳" % tag
        target = add_delta(base, cond.get("days", 0), cond.get("hours", 0), cond.get("minutes", 0))
        return now >= target, "获得「%s」后 %s 天 %s 时 %s 分" % (
            tag, cond.get("days", 0), cond.get("hours", 0), cond.get("minutes", 0))
    return True, ""          # 未知类型按「无时间条件」处理，不卡死事件


# ═════════════════════════════ 地点 ═════════════════════════════
def _place_due(state: Any, cond: Dict[str, Any]):
    """返回 (是否满足, 说明)。"""
    if not cond:
        return True, ""
    kind = cond.get("type") or ""
    if kind == "in_building":
        here = getattr(state, "emperor_location", "") or ""
        want = cond.get("place") or ""
        return here == want, "圣躬在%s（当前%s）" % (want, here or "未知")
    if kind == "tag_absent":
        tag = cond.get("tag") or ""
        return (not has_tag(state, tag)), "标签「%s」不存在" % tag
    return True, ""


# ═════════════════════════════ 求值 ═════════════════════════════
def event_due(state: Any, ev: Dict[str, Any],
              started: Optional[Tuple[int, int, int, int, int]] = None):
    """四维同时满足才算触发。返回 (bool, 原因列表)。"""
    reasons: List[str] = []
    ok_t, why_t = _time_due(state, ev.get("time") or {}, started)
    reasons.append(why_t)
    if not ok_t:
        return False, reasons
    ok_p, why_p = _place_due(state, ev.get("place") or {})
    reasons.append(why_p)
    if not ok_p:
        return False, reasons
    return True, [r for r in reasons if r]


def tick(state: Any, events: List[Dict[str, Any]],
         started: Optional[Tuple[int, int, int, int, int]] = None,
         fired: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """推进时调一次：把到点的事件挑出来，每个只触发一次。

    fired 传主线里的 fired 字典（{event_id: 时间键}），由调用方持久化。
    返回 [{"event": 事件, "reasons": [...]}]。
    """
    ensure_state(state)
    fired = fired if isinstance(fired, dict) else {}
    out: List[Dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("event_id") or "")
        if eid and eid in fired:
            continue
        due, reasons = event_due(state, ev, started)
        if not due:
            continue
        if eid:
            fired[eid] = tag_key(state)
        out.append({"event": ev, "reasons": reasons})
    return out


def is_system(ev: Dict[str, Any]) -> bool:
    return str(ev.get("type") or "") in SYSTEM_TYPES


def describe(ev: Dict[str, Any]) -> str:
    """给归档/日志用的一行说明。"""
    t = str(ev.get("type") or "")
    if t in SYSTEM_TYPES:
        return "系统事件：%s" % SYSTEM_TYPES[t]
    return "事件：%s" % EVENT_TYPES.get(t, t or "未指定")
