# -*- coding: utf-8 -*-
"""近侍：皇帝身边的三名近侍（左、中、右三个位次）。

口径（玩家定义）：
- **近侍分主次**：位次从左到右，最左为「主」，显示与用语默认取主位；
  某位空缺时顺延到下一个有人的位次。
- 三个位次可空可满；人名可以留空（点击「+」选人，或「更换人物」里选空即撤下）。
- 每次人员变动都打一条「变化」标签，进控制台归档（旧系统的变化池）。
- **用语**：每个位次一套，按类别（应答 / 问寝 / 祭祀）各一段文本，
  一段内用分号「;」分隔多条，显示时随机抽一条。默认值在
  `data/attendant_phrases.json`，玩家改过的存 state，随存档。
"""

import json
import os
import random
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PHRASES_FILE = os.path.join(_BASE, "data", "attendant_phrases.json")

SLOT_COUNT = 3
SLOT_NAMES = ("左", "中", "右")
CATEGORIES = ("应答", "问寝", "祭祀")

_defaults_cache: Optional[Dict[str, List[str]]] = None


def default_phrases() -> Dict[str, List[str]]:
    """默认用语（一个类别一组，各组多条）。"""
    global _defaults_cache
    if _defaults_cache is None:
        try:
            with open(PHRASES_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        _defaults_cache = {c: list(raw.get(c) or [""]) for c in CATEGORIES}
    return _defaults_cache


def _default_text(category: str) -> str:
    return ";".join(default_phrases().get(category) or [])


def ensure_state(state: Any) -> List[Dict[str, Any]]:
    """保证 state.attendants 是三格结构；老存档按迁移补齐。"""
    slots = getattr(state, "attendants", None)
    if not isinstance(slots, list) or len(slots) != SLOT_COUNT:
        slots = []
        for i in range(SLOT_COUNT):
            old = (getattr(state, "attendants", None) or [])[i] if isinstance(
                getattr(state, "attendants", None), list) and i < len(
                getattr(state, "attendants", None) or []) else {}
            slots.append(_slot_dict(old))
        state.attendants = slots
    for s in slots:
        s.setdefault("name", "")
        ph = s.get("phrases")
        if not isinstance(ph, dict):
            ph = {}
        for c in CATEGORIES:
            if not ph.get(c):
                ph[c] = _default_text(c)
            # 老存档里沿用旧默认用语（含内侍自称），统一改成臣属口吻
            if "奴婢" in str(ph.get(c) or ""):
                ph[c] = str(ph[c]).replace("奴婢", "臣")
        s["phrases"] = ph
    return slots


def _slot_dict(old: Any) -> Dict[str, Any]:
    old = old or {}
    if isinstance(old, str):
        old = {"name": old}
    return {
        "name": old.get("name") or "",
        "phrases": dict(old.get("phrases") or {}),
    }


def slots(state: Any) -> List[Dict[str, Any]]:
    """三格近侍：人名 + 姓（界面圆内显示）+ 用语。"""
    out = []
    for s in ensure_state(state):
        name = s.get("name") or ""
        out.append({
            "name": name,
            "surname": name[:1] if name else "",
            "phrases": dict(s.get("phrases") or {}),
            "filled": bool(name),
        })
    return out


def set_slot(state: Any, index: int, name: Optional[str]) -> Dict[str, Any]:
    """设置某一位次的人：name 为空即撤下。返回变化记录。"""
    if not 0 <= int(index) < SLOT_COUNT:
        raise ValueError("近侍位次只有 0~%d，收到 %s" % (SLOT_COUNT - 1, index))
    s = ensure_state(state)[int(index)]
    old = s.get("name") or ""
    new = (name or "").strip()
    if old == new:
        return {"index": int(index), "old": old, "new": new, "changed": False}
    s["name"] = new
    return {"index": int(index), "old": old, "new": new, "changed": True}


def set_phrase(state: Any, index: int, category: str, text: str) -> bool:
    """改写某位次某类别的用语（一段内可含分号分隔的多条）。"""
    if category not in CATEGORIES:
        raise ValueError("用语类别只有 %s，收到 %s" % ("/".join(CATEGORIES), category))
    if not 0 <= int(index) < SLOT_COUNT:
        raise ValueError("近侍位次只有 0~%d，收到 %s" % (SLOT_COUNT - 1, index))
    s = ensure_state(state)[int(index)]
    s.setdefault("phrases", {})[category] = (text or "").strip()
    return True


def _split(text: str) -> List[str]:
    parts = [p.strip() for p in str(text or "").replace("；", ";").split(";")]
    return [p for p in parts if p]


def phrase(state: Any, category: str, index: Optional[int] = None) -> str:
    """取一句用语。

    - 指定 index 就用该位次；不指定就按主次取**最左有人的位次**（近侍分主次）。
    - 一段内多条时随机抽一条；该位次为空或没写，回落到默认用语。
    """
    if category not in CATEGORIES:
        raise ValueError("用语类别只有 %s，收到 %s" % ("/".join(CATEGORIES), category))
    slots_list = ensure_state(state)
    order = [int(index)] if index is not None else list(range(SLOT_COUNT))
    for i in order:
        if not 0 <= i < SLOT_COUNT:
            continue
        s = slots_list[i]
        if not (s.get("name") or "") and index is None:
            continue                      # 空位次不发声，顺延到下一个
        variants = _split((s.get("phrases") or {}).get(category) or "")
        if variants:
            return random.choice(variants)
    return random.choice(default_phrases().get(category) or [""])


def primary_slot(state: Any) -> int:
    """主位：最左的有人的位次；一个都没有时回落到 0。"""
    for i, s in enumerate(ensure_state(state)):
        if s.get("name"):
            return i
    return 0


def change_text(change: Dict[str, Any]) -> str:
    """把一次人员变动写成一条「变化」标签的正文。"""
    pos = SLOT_NAMES[change["index"]]
    old, new = change.get("old") or "", change.get("new") or ""
    if not old and new:
        return "【近侍】%s位简选 %s 侍直" % (pos, new)
    if old and not new:
        return "【近侍】%s位 %s 撤下" % (pos, old)
    return "【近侍】%s位由 %s 换为 %s" % (pos, old, new)


def apply_slot_change(state: Any, index: int, name: Optional[str]) -> Dict[str, Any]:
    """改某位近侍，并在变化池登记一条「变化」标签（旧系统，进控制台归档）。"""
    ch = set_slot(state, index, name)
    if ch.get("changed"):
        from .variations import record_variation
        record_variation(state, "official", change_text(ch), province="")
    return ch


def answer_prompt(state: Any, choice: str) -> Dict[str, Any]:
    """回答近侍问话（问寝 / 祭祀），返回界面该做什么。"""
    prompt = dict(getattr(state, "attendant_prompt", None) or {})
    if not prompt:
        return {"ok": False, "reason": "当前没有待答的近侍问话"}
    kind = prompt.get("kind")
    state.attendant_prompt = {}
    if kind == "问寝":
        if choice == "rest":
            return {"ok": True, "kind": kind, "action": "rest",
                    "location": "养心殿", "text": "移驾养心殿安置。"}
        return {"ok": True, "kind": kind, "action": "stay", "text": "仍在中庭批阅。"}
    if kind == "祭祀":
        return {"ok": True, "kind": kind, "action": choice or "ack",
                "text": "祭祀事已奉旨。"}
    return {"ok": True, "kind": kind, "action": choice}
