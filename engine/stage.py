# -*- coding: utf-8 -*-
"""立绘调度：场景里有谁、谁站哪个位置、谁在最上层。

口径（玩家定义）：
- 主界面同时最多显示 **7 个**立绘，位置从左到右是
  **左一 / 左二 / 左三 / 中间 / 右三 / 右二 / 右一**。
- 显示层级从**上到下**是：中间 ＞ 左三 ＞ 右三 ＞ 左二 ＞ 右二 ＞ 左一 ＞ 右一。
- 中间位比左右**略大**。
- 场景人物超过 7 个时：**只记录不显示**多出来的人；本次要发言的人**跳到中间**，
  原来在中间的人按层级往下挤，所以立绘位置会随发言不断变化。
- 本次不发言的人：透明度更低、更小、更灰更暗。
- 同一场景**不允许出现同名立绘**，重复直接报错。
"""

from typing import Any, Dict, Iterable, List, Optional

# 从左到右的位置
POSITIONS = ("左一", "左二", "左三", "中间", "右三", "右二", "右一")

# 显示层级：越靠前越在上层。发言者永远占第一层（中间）。
LAYERS = ("中间", "左三", "右三", "左二", "右二", "左一", "右一")

MAX_VISIBLE = len(POSITIONS)

# 每个位置的几何与层级：left/width 是视口百分比，中间略大
SLOT_SPEC: Dict[str, Dict[str, Any]] = {
    "中间": {"left": 50.0, "width": 34.0, "z": 70, "scale": 1.00},
    "左三": {"left": 33.0, "width": 24.0, "z": 60, "scale": 0.96},
    "右三": {"left": 67.0, "width": 24.0, "z": 55, "scale": 0.96},
    "左二": {"left": 21.0, "width": 22.0, "z": 50, "scale": 0.93},
    "右二": {"left": 79.0, "width": 22.0, "z": 45, "scale": 0.93},
    "左一": {"left": 9.0, "width": 20.0, "z": 40, "scale": 0.90},
    "右一": {"left": 91.0, "width": 20.0, "z": 35, "scale": 0.90},
}

# 不发言者的观感
IDLE_SCALE = 0.94
IDLE_OPACITY = 0.55
IDLE_FILTER = "grayscale(0.55) brightness(0.68)"


def _check_unique(cast: List[str]) -> None:
    seen = set()
    dup = []
    for n in cast:
        if not n:
            continue
        if n in seen:
            dup.append(n)
        seen.add(n)
    if dup:
        raise ValueError("同一场景出现重复立绘：%s（一个角色只能有一个立绘）"
                         % "、".join(sorted(set(dup))))


def plan(cast: Iterable[str], speaker: Optional[str] = None) -> List[Dict[str, Any]]:
    """排出这一帧的立绘位置。

    cast    场景里的角色名（顺序即优先级，重复会报错）
    speaker 本次发言的人；不在 cast 里就当作没人发言

    返回每条：
      name / position / layer / z / scale / opacity / filter / active / visible
    超出 7 人的，visible=False，只记录位置为空。
    """
    names = [str(n).strip() for n in (cast or []) if str(n).strip()]
    _check_unique(names)

    if speaker and speaker not in names:
        speaker = None

    # 发言者排第一，其余保持原顺序；再按层级顺序依次落位
    ordered = ([speaker] if speaker else []) + [n for n in names if n != speaker]
    out: List[Dict[str, Any]] = []
    for i, name in enumerate(ordered):
        if i >= MAX_VISIBLE:
            out.append({"name": name, "position": None, "layer": None, "z": 0,
                        "scale": 0.0, "opacity": 0.0, "filter": IDLE_FILTER,
                        "active": False, "visible": False})
            continue
        pos = LAYERS[i]
        spec = SLOT_SPEC[pos]
        active = (name == speaker)
        out.append({
            "name": name,
            "position": pos,
            "layer": i,
            "z": spec["z"],
            "left": spec["left"],
            "width": spec["width"],
            "scale": spec["scale"] if active else round(spec["scale"] * IDLE_SCALE, 3),
            "opacity": 1.0 if active else IDLE_OPACITY,
            "filter": "none" if active else IDLE_FILTER,
            "active": active,
            "visible": True,
        })
    return out


def speakers_phrase_slot(attendants: List[Dict[str, Any]]) -> int:
    """近侍默认只显示最左（主位）的用语；这里给界面一个统一入口。"""
    for i, s in enumerate(attendants or []):
        if s.get("name"):
            return i
    return 0
