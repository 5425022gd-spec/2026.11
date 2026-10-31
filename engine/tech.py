"""科技树。

数据 只此一份：`world/techs.json`
- 每条科技写自己的**直接前置** prereqs，后置由前置反查得出，不另存关系表。
- 坐标 (x, y) 的零点＝ `png/keji.png` 的左上角，x 向右、y 向下，
  直接就是图里该科技文字所在的中心点 —— 排布照图，前后置照玩家口述，两者不混。

运行状态 随存档：`state.tech_points` 科技点、`state.tech_unlocked` 已解锁科技 id。
开局点数在世界起始数据里 world/provinces.json 的 tech_points；每条科技的解锁消耗写在
`world/techs.json` 的 `cost` 当前每条 1 点。解锁规则 前置齐、点数够只在 `unlock_state()` 一处判定，
按钮文案由 `unlock_text()` 一处给出，前端不自己算。

界面读口只有一个：`tech_view(state)` —— 点数量、两棵树、每条科技的坐标与前后置名字、
效果文案 由 `engine/tech_effects.py` 从结构化效果现渲染，
前端不再自己算前后置，也不自己拼名字。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from . import tech_effects

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TECHS_FILE = os.path.join(_BASE, "world", "techs.json")

_CACHE: Optional[Dict[str, Any]] = None


def load_techs() -> Dict[str, Any]:
    """科技树数据 静态，不含运行状态。"""
    global _CACHE
    if _CACHE is None:
        with open(TECHS_FILE, encoding="utf-8") as f:
            _CACHE = json.load(f)
        _CACHE.setdefault("trees", [])
        _CACHE.setdefault("techs", [])
    return _CACHE


def trees() -> List[Dict[str, str]]:
    """全部科技树 理论科学 / 科学技术。"""
    return [dict(t) for t in load_techs()["trees"]]


def tree_name(tree_id: str) -> str:
    for t in trees():
        if t.get("id") == tree_id:
            return t.get("name", tree_id)
    return tree_id


def all_techs() -> List[Dict[str, Any]]:
    """全部科技，每条补齐直接前置 id 列表。"""
    out: List[Dict[str, Any]] = []
    for raw in load_techs()["techs"]:
        t = dict(raw)
        t["prereqs"] = [str(p) for p in (t.get("prereqs") or [])]
        out.append(t)
    return out


def tech_by_id(tid: str) -> Optional[Dict[str, Any]]:
    for t in all_techs():
        if t.get("id") == tid:
            return t
    return None


def tech_name(tid: str) -> str:
    t = tech_by_id(tid)
    return t.get("name", tid) if t else tid


def prereqs_of(tid: str) -> List[str]:
    """直接前置 上一级。"""
    t = tech_by_id(tid)
    return list(t.get("prereqs") or []) if t else []


def dependents_of(tid: str) -> List[str]:
    """直接后置 下一级：以它为前置的科技，由前置反查。"""
    return [t["id"] for t in all_techs() if tid in (t.get("prereqs") or [])]


def unlocked_ids(state: Any) -> List[str]:
    return [str(x) for x in (getattr(state, "tech_unlocked", None) or [])]


def is_unlocked(state: Any, tid: str) -> bool:
    return tid in unlocked_ids(state)


def points(state: Any) -> float:
    return float(getattr(state, "tech_points", 0.0) or 0.0)


def prereqs_met(state: Any, tid: str) -> bool:
    """前置是否都已完成。"""
    return all(is_unlocked(state, p) for p in prereqs_of(tid))


def cost_of(tid: str) -> float:
    """解锁该科技要花的科技点 写在数据里，缺省视作未定＝0。"""
    t = tech_by_id(tid)
    return float((t or {}).get("cost") or 0.0)


def unlock_state(state: Any, tid: str) -> str:
    """能否解锁：已解锁 / 前置未完成 / 科技点不足 / 可解锁 唯一一处判定。"""
    if is_unlocked(state, tid):
        return "已解锁"
    if not prereqs_met(state, tid):
        return "前置未完成"
    cost = cost_of(tid)
    if cost <= 0:
        return "消耗未定"
    if points(state) < cost:
        return "科技点不足"
    return "可解锁"


def unlock_text(state: Any, tid: str) -> str:
    """解锁按钮上的字 只有这一处写文案，前端照抄。"""
    st = unlock_state(state, tid)
    if st == "可解锁":
        return "消耗%g科技点解锁" % cost_of(tid)
    return st


def unlock(state: Any, tid: str) -> Tuple[bool, str]:
    """解锁一条科技：校验前置与点数，扣点并记入已解锁。"""
    t = tech_by_id(tid)
    if t is None:
        return False, "没有这条科技"
    st = unlock_state(state, tid)
    if st != "可解锁":
        return False, "%s：%s" % (t.get("name", tid), st)
    cost = cost_of(tid)
    state.tech_points = round(points(state) - cost, 4)
    ids = unlocked_ids(state)
    if tid not in ids:
        ids.append(tid)
    state.tech_unlocked = ids
    # 解锁即生效：科技效果里需要落回 state 的部分 金融替代率等在这里重算一次
    from .tech_effects import recompute as _recompute_tech_effects
    _recompute_tech_effects(state)
    return True, "已解锁 %s，消耗 %g 科技点" % (t.get("name", tid), cost)


def tech_view(state: Any) -> Dict[str, Any]:
    """界面读口：科技点 + 两棵树 + 全部科技的坐标与前后置名字。"""
    nodes: List[Dict[str, Any]] = []
    for t in all_techs():
        tid = t["id"]
        pre = prereqs_of(tid)
        post = dependents_of(tid)
        nodes.append({
            "id": tid,
            "name": t.get("name", tid),
            "tree": t.get("tree", ""),
            "tree_name": tree_name(t.get("tree", "")),
            "x": float(t.get("x", 0.0)),
            "y": float(t.get("y", 0.0)),
            "unlocked": is_unlocked(state, tid),
            "prereqs": pre,
            "prereq_names": [tech_name(p) for p in pre],
            "dependents": post,
            "dependent_names": [tech_name(p) for p in post],
            "intro": t.get("intro", ""),
            "effect": tech_effects.effect_text(tid),
            "effect_rows": tech_effects.effects_of(tid),
            "line": t.get("line", ""),
            "cost": cost_of(tid),
            "unlock_state": unlock_state(state, tid),
            "unlock_text": unlock_text(state, tid),
            "unlockable": unlock_state(state, tid) == "可解锁",
        })
    return {
        "points": points(state),
        "trees": trees(),
        "nodes": nodes,
    }
