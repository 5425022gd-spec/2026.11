# -*- coding: utf-8 -*-
"""工程（建筑）的损毁程度与生效折算 —— 唯一实现处。

口径（玩家定义）：
- **损毁是数字，不是二值状态**：`pj["damage"]` 取 0~1，0＝完好，1＝全毁。
  状态栏仍是「运营中／未完工」，损毁程度另记，二者不混。
- **损毁多少由修正系统给**：大黄汛损毁时按 `damage_degree` 这个 target 算
  （基础值 1.0＝全毁，任何修正都可以把它加减下来），不在 weather 里写死。
- **生效一律按 (1 − 损毁程度) 折算**：产能、白银、黄金、减灾比例都乘这个系数。
  这样「损毁 60% 的海塘还剩四成减灾」是算出来的，不是另写一套。
"""

from typing import Any, Dict, List


def damage_of(pj: Dict[str, Any]) -> float:
    """该工程的损毁程度 0~1。没写就是 0（完好）。"""
    try:
        return max(0.0, min(1.0, float((pj or {}).get("damage") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def set_damage(pj: Dict[str, Any], value: float) -> float:
    """设置损毁程度（0~1 截断），返回写入后的值。"""
    v = max(0.0, min(1.0, float(value)))
    pj["damage"] = round(v, 4)
    return pj["damage"]


def scale(pj: Dict[str, Any]) -> float:
    """该工程当前生效系数 ＝ 1 − 损毁程度。"""
    return round(1.0 - damage_of(pj), 4)


def effective_buff(pj: Dict[str, Any]) -> Dict[str, Any]:
    """按损毁程度折算后的 buff：数值字段乘 (1−损毁)，desc 原样保留。

    所有消费工程 buff 的地方都必须走这里，不许直接读 pj["buff"]，
    否则「损毁」只影响显示、不影响数值。
    """
    buff = (pj or {}).get("buff") or {}
    k = scale(pj)
    out: Dict[str, Any] = {}
    for key, val in buff.items():
        if key == "desc":
            out[key] = val
        elif isinstance(val, dict):
            out[key] = {c: round(float(v) * k, 6) for c, v in val.items()}
        elif isinstance(val, (int, float)):
            out[key] = round(float(val) * k, 6)
        else:
            out[key] = val
    return out


def damage_text(pj: Dict[str, Any]) -> str:
    """界面上的损毁说明：完好时给空串。"""
    d = damage_of(pj)
    if d <= 0:
        return ""
    if d >= 1:
        return "已全毁"
    return "损毁 %d%%" % round(d * 100)


def damaged_projects(state: Any) -> List[Dict[str, Any]]:
    """全部有损毁的工程（跨省），带 province 名，供界面与校验用。"""
    out = []
    for p in (getattr(state, "provinces", {}) or {}).values():
        for pj in (p.get("local") or {}).get("projects") or []:
            if damage_of(pj) > 0:
                out.append({"province": p.get("name", ""), "project": pj})
    return out
