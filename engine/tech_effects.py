"""科技效果：解锁科技带来的数值调整 数据在 world/tech_effects.json。

口径：
1. **效果只写在数据里** `world/tech_effects.json`，一条科技一个数组，
   本模块不自造第二套；`techs.json` 的 `effect` 字段不再另写一份文案，
   科技界面的「效果」由 `effect_text()` 从结构化效果现渲染。
2. `EFFECT_TARGETS` 是本模块唯一的登记表：每个 target 写清「叫什么、单位、由谁消费」。
   任务与成就只能用登记过的 target，写错直接报错。
3. **聚合只此一处**：`active_totals(state)` 按已解锁科技求和；各消费点调用它，
   自己不再遍历 tech_unlocked 二次求和。
4. `financial_rate` 是唯一需要「回写 state」的目标 其它都是读取时实时算：
   `financial_rate = 基础值 + Σ科技效果 + Σ部门修正`，由 `recompute_financial_rate()`
   在开局、读档、解锁科技、增删改部门修正后调用。
    部门修正里的 `替代率f` 原来只显示不生效，这里一并接上，界面与实算同源。
"""

import json
import os
from typing import Any, Dict, List

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EFFECTS_FILE = os.path.join(_BASE, "world", "tech_effects.json")

_CACHE: Dict[str, Any] = {}

# target 词表由 engine/modifier_registry 独占 修正与科技效果共用同一套名字。
# 这里直接沿用，避免出现第二份词表。
from .modifier_registry import TARGETS as EFFECT_TARGETS


def load_effects_data() -> Dict[str, Any]:
    """读科技效果数据。读不到或结构不对就是错，不静默给空表。"""
    if "data" not in _CACHE:
        with open(EFFECTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("effects"), dict):
            raise TypeError("world/tech_effects.json: 'effects' must be an object")
        for tid, rows in data["effects"].items():
            if not isinstance(rows, list):
                raise TypeError ("科技 %s 的效果必须是数组" % tid)
            for row in rows:
                tgt = str(row.get("target") or "")
                if tgt not in EFFECT_TARGETS:
                    raise KeyError("科技 %s 用了未登记的 target：%r" % (tid, tgt))
        _CACHE["data"] = data
    return _CACHE["data"]


def effects_of(tid: str) -> List[Dict[str, Any]]:
    """某条科技的效果条目 没有就空表。"""
    return list(load_effects_data()["effects"].get(tid) or [])


def effect_text(tid: str) -> str:
    """科技界面显示的「效果」文案：由结构化效果现渲染，不另存一份文案。

    数值按登记表的单位换算 小数 → 百分比，一条一句。
    """
    rows = effects_of(tid)
    if not rows:
        return ""
    parts = []
    for r in rows:
        tgt = str(r["target"])
        val = float(r.get("value") or 0.0)
        desc = str(r.get("desc") or EFFECT_TARGETS[tgt]["name"])
        if EFFECT_TARGETS[tgt]["unit"] == "%":
            parts.append("%s %+.1f%%" % (desc, val * 100.0))
        else:
            parts.append(desc)
    return "；".join(parts)


def active_totals(state: Any) -> Dict[str, float]:
    """已解锁科技的效果合计：target -> 数值 同类相加。"""
    totals: Dict[str, float] = {}
    for tid in (state.tech_unlocked or []):
        for r in effects_of(str(tid)):
            tgt = str(r["target"])
            totals[tgt] = totals.get(tgt, 0.0) + float(r.get("value") or 0.0)
    return totals


def total_of(state: Any, target: str) -> float:
    """某个 target 的合计 未解锁任何相关科技则为 0。"""
    if target not in EFFECT_TARGETS:
        raise KeyError ("未登记的科技效果 target：%s" % target)
    return float(active_totals(state).get(target, 0.0))


def financial_rate_calc(state: Any) -> Dict[str, Any]:
    """金融替代率的算账明细 唯一一处：走 modifier_registry 的唯一公式。"""
    from .modifier_registry import value as mod_value
    base = float(getattr(state, "financial_rate_base", state.financial_rate))
    return mod_value(state, "financial_rate", base)


def recompute_financial_rate(state: Any) -> float:
    """金融替代率 回写 state 的唯一一处；界面读的就是同一份算账明细。

    口径 玩家定义：值 = 基础值 ×  1 + Σ乘法修正 + Σ加法修正。
    科技效果与部门修正都由 modifier_registry 统一收集，本函数不再自己加一遍。
    """
    state.financial_rate = round(financial_rate_calc(state)["value"], 4)
    return state.financial_rate


def recompute(state: Any) -> None:
    """科技效果里需要落回 state 的部分 目前只有金融替代率。"""
    recompute_financial_rate(state)
