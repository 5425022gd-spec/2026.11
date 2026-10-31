"""成就系统：廷议的衍生系统。

口径：
1. 每条成就对应一个廷议议题 数据在 `world/achievements.json`。
2. **开始时**由引擎判定开启条件 `start`——不满足就不许开这个成就廷议。
3. **结束时**由 AI 判定。引擎把所有硬指标算好交给 AI `data_checks` 与 `end_check`，
   AI 只做「成没成」的判断，判定通过则由引擎发科技点。
   这样「硬数值」也只有一处算 引擎，AI 不自己算数。
4. 奖励只发一次，完成记录写 `state.achievements_done`。
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .logger import CONSOLE

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACHIEVEMENTS_FILE = os.path.join(_BASE, "world", "achievements.json")

_CACHE: Dict[str, Any] = {}


def load_achievements_data() -> Dict[str, Any]:
    """读成就定义。读不到或结构不对就是错，不静默给空表。"""
    if "data" not in _CACHE:
        with open(ACHIEVEMENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("achievements"), list):
            raise TypeError("world/achievements.json: 'achievements' must be a list")
        _CACHE["data"] = data
    return _CACHE["data"]


def all_achievements() -> List[Dict[str, Any]]:
    return list(load_achievements_data()["achievements"])


def get_achievement(aid: str) -> Optional[Dict[str, Any]]:
    for a in all_achievements():
        if a.get("id") == aid:
            return a
    return None


# ═══════════════════════════════════════════════════════════
# 核查项：每一条给出「达成与否」与「一句人话的现状」
# ═══════════════════════════════════════════════════════════

def _is_tech_unlocked(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    from .tech import is_unlocked, tech_name
    tid = str(spec.get("tech") or "")
    ok = is_unlocked(state, tid)
    return ok, "科技「%s」%s" % (tech_name(tid), "已解锁" if ok else "尚未解锁")


def _higher_edu_positive_modifier(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """至少有一项受高等教育人口年增大于 0 的修正，且名字不在排除表里。"""
    exclude = {str(x) for x in (spec.get("exclude") or [])}
    hits = []
    for dmod in (getattr(state, "department_modifiers", None) or {}).values():
        name = str(dmod.get("name") or "")
        for eff in (dmod.get("effects") or []):
            if eff.get("target") != "higher_edu_growth":
                continue
            pct = float(eff.get("delta_pct") or 0.0)
            if pct > 0 and name not in exclude:
                hits.append("%s %+.1f%%" % (name, pct * 100))
    if hits:
        return True, "已有合规修正：" + "、".join(hits)
    return False, "尚无「受高等教育人口年增为正且不属两京国子监」的修正"


def _province_controlled(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    from .localdata import under_ming_control
    pid = str(spec.get("province") or "")
    p = (state.provinces or {}).get(pid)
    if p is None:
        return False, "省份不存在：%s" % pid
    ok = under_ming_control(p)
    return ok, "%s%s" % (p.get("name", pid), "在明朝控制下" if ok else "不在明朝控制下")


def _metric_at_least(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    from .tasks import METRICS, metric_text, metric_value
    key = str(spec.get("metric") or "")
    if key not in METRICS:
        raise KeyError ("成就核查引用了未登记的指标：%s" % key)
    cur = metric_value(state, key)
    want = float(spec.get("value") or 0.0)
    return cur >= want, "%s 当前 %s，门槛 %s" % (METRICS[key]["name"],
                                              metric_text(state, key, cur),
                                              metric_text(state, key, want))


CHECK_KINDS = {
    "tech_unlocked": _is_tech_unlocked,
    "higher_edu_positive_modifier": _higher_edu_positive_modifier,
    "province_controlled": _province_controlled,
    "metric_at_least": _metric_at_least,
}


def run_check(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    kind = str(spec.get("check") or "")
    fn = CHECK_KINDS.get(kind)
    if fn is None:
        raise KeyError ("成就核查项未登记：%r" % kind)
    return fn(state, spec)


# ═══════════════════════════════════════════════════════════
# 对外：开启条件、核查包、结案
# ═══════════════════════════════════════════════════════════

def can_start(state: Any, aid: str) -> Tuple[bool, str]:
    """成就廷议能否开启 引擎判定；前端只据此置灰。"""
    a = get_achievement(aid)
    if a is None:
        return False, "无此成就：%s" % aid
    if aid in state.achievements_done:
        return False, "该成就已完成"
    ok, detail = run_check(state, a["start"])
    return ok, ("开启条件已满足：%s %s" % (a["start"].get("text", ""), detail)) if ok \
        else ("开启条件未满足：%s %s" % (a["start"].get("text", ""), detail))


def achievement_rows(state: Any) -> List[Dict[str, Any]]:
    """成就面板数据 前端只渲染。"""
    rows = []
    for a in all_achievements():
        aid = a["id"]
        start_ok, start_detail = run_check(state, a["start"])
        end_ok, end_detail = run_check(state, a["end"])
        rows.append({
            "id": aid,
            "title": a.get("title", aid),
            "desc": a.get("desc", ""),
            "start_text": a["start"].get("text", ""),
            "start_met": bool(start_ok),
            "start_detail": start_detail,
            "end_text": a["end"].get("text", ""),
            "end_met": bool(end_ok),
            "end_detail": end_detail,
            "reward_points": float(a.get("reward_points") or 0.0),
            "done": aid in state.achievements_done,
        })
    return rows


def check_pack_text(state: Any, aid: str) -> str:
    """开会时发给 AI 的核查数据包：引擎把硬指标算好，AI 只判「成没成」。"""
    a = get_achievement(aid)
    if a is None:
        raise KeyError ("无此成就：%s" % aid)
    lines = ["【成就核查数据%s】" % a.get("title", aid)]
    lines.append("议题说明：%s" % a.get("desc", ""))
    lines.append("开启条件：%s" % a["start"].get("text", ""))
    ok, detail = run_check(state, a["start"])
    lines.append("  - 现状：%s %s" %  "已满足" if ok else "未满足", detail)
    lines.append("结束条件：%s" % a["end"].get("text", ""))
    eok, edetail = run_check(state, a["end"])
    lines.append("  - 现状：%s %s" %  "已达成" if eok else "未达成", edetail)
    lines.append ("相关核查项：")
    for spec in (a.get("data_checks") or []):
        cok, cdetail = run_check(state, spec)
        lines.append("  - %s：%s %s" % (spec.get("text", spec.get("check")),
                                        "达标" if cok else "未达标", cdetail))
    lines.append("结束奖励：科技点 %g" % float(a.get("reward_points") or 0.0))
    lines.append ("注意：以上数值由引擎给出，你只需据此判断本成就的结束条件是否已经达成。")
    return "\n".join(lines)


def complete(state: Any, aid: str) -> Tuple[bool, str]:
    """结案并发奖励 AI 判定通过后由引擎调用；只发一次。"""
    from .tasks import grant_tech_points
    a = get_achievement(aid)
    if a is None:
        return False, "无此成就：%s" % aid
    if aid in state.achievements_done:
        return False, "该成就已完成，不重复发奖"
    points = float(a.get("reward_points") or 0.0)
    state.achievements_done.append(aid)
    if points:
        grant_tech_points(state, points)
    CONSOLE.log("achievement", "成就达成：%s" % a.get("title", aid),
                "科技点 +%g 当前 %g" % (points, float(state.tech_points)))
    return True, "成就【%s】达成，科技点 +%g。" % (a.get("title", aid), points)
