"""任务系统：数值达标的一次性任务，达成后给科技点 见 world/tasks.json。

口径 本模块是任务系统的唯一出处：
1. 任务定义全在 `world/tasks.json`——改数值不碰代码。
2. **指标 metric只在 METRICS 一处登记**：每个指标给出「当前值怎么算」与「怎么显示」。
   任务定义只写 `metric / op / value`，不在数据里另算第二套。
3. 判定与发点在 `evaluate(state)` 一处：推进时调一次；达成即发科技点，
   完成记录写 `state.tasks_done` 只此一份，**一次性，不重复发**。
4. 任务不可撤销：数值后来跌回去也不退回科技点 这是「一次性任务」的定义。
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .logger import CONSOLE

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS_FILE = os.path.join(_BASE, "world", "tasks.json")

_CACHE: Dict[str, Any] = {}

# 比较运算符：任务定义里只允许这几种，写错直接报错 不静默当不成立
_OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: abs(a - b) < 1e-9,
}


def load_tasks_data() -> Dict[str, Any]:
    """读任务定义 world/tasks.json。读不到就是错，不静默给空表。"""
    if "data" not in _CACHE:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("tasks"), list):
            raise TypeError("world/tasks.json: 'tasks' must be a list")
        _CACHE["data"] = data
    return _CACHE["data"]


def all_tasks() -> List[Dict[str, Any]]:
    return list(load_tasks_data()["tasks"])


def _task(tid: str) -> Optional[Dict[str, Any]]:
    for t in all_tasks():
        if t.get("id") == tid:
            return t
    return None


# ═══════════════════════════════════════════════════════════
# 指标登记：名 ->  取值函数, 显示名, 单位, 显示精度
# 取值函数一律 (state) -> float；取不到就让它报错，不返回假值。
# ═══════════════════════════════════════════════════════════

def _ming_provinces(state: Any) -> List[Dict[str, Any]]:
    from .localdata import under_ming_control
    return [p for p in state.provinces.values() if under_ming_control(p)]


def _carrying_total(state: Any) -> float:
    from . import localdata
    total = 0.0
    for p in _ming_provinces(state):
        total += localdata.carrying(p.get("local") or {}, state)
    return round(total, 1)


def _mining_silver_annual(state: Any) -> float:
    """运营中的官办矿场银年额合计 万两/年：与户部账册同一口径 buff.silver。"""
    total = 0.0
    for p in _ming_provinces(state):
        for pj in ((p.get("local") or {}).get("projects") or []):
            if str(pj.get("status") or "运营中") != "运营中":
                continue
            from .projects import scale as _proj_scale
            total += float((pj.get("buff") or {}).get("silver") or 0.0) * _proj_scale(pj)
    return round(total, 2)


def _army_arrears_total(state: Any) -> float:
    """明朝军队 机动部队 + 镇军欠饷合计 万两。"""
    from .armies import get_all_armies, select_units
    total = 0.0
    for u in select_units(get_all_armies(state), owner="ming"):
        total += float(u.get("arrears_wan") or 0.0)
    return round(total, 2)


METRICS: Dict[str, Dict[str, Any]] = {
    "financial_rate": {
        "name": "金融替代率", "unit": "%", "scale": 100.0, "digits": 1,
        "get": lambda st: float(st.financial_rate),
    },
    "treasury": {
        "name": "国库存银", "unit": "万两", "scale": 1.0, "digits": 1,
        "get": lambda st: float(st.treasury),
    },
    "ming_province_count": {
        "name": "明属控制省份", "unit": "处", "scale": 1.0, "digits": 0,
        "get": lambda st: float(len(_ming_provinces(st))),
    },
    "tech_unlocked_count": {
        "name": "已解锁科技", "unit": "条", "scale": 1.0, "digits": 0,
        "get": lambda st: float(len(st.tech_unlocked)),
    },
    "local_educated_pop": {
        "name": "受教育人口", "unit": "万", "scale": 1.0, "digits": 1,
        "get": lambda st: float(st.local_educated_pop),
    },
    "higher_educated_pop": {
        "name": "受高等教育人口", "unit": "万", "scale": 1.0, "digits": 1,
        "get": lambda st: float(st.higher_educated_pop),
    },
    "liquidity_health": {
        "name": "货币健康度", "unit": "", "scale": 1.0, "digits": 2,
        "get": lambda st: float(_liquidity_health(st)),
    },
    "central_corruption": {
        "name": "中央腐败程度", "unit": "", "scale": 1.0, "digits": 1,
        "get": lambda st: float(_central_corruption(st)),
    },
    "prestige": {
        "name": "皇室威望", "unit": "", "scale": 1.0, "digits": 1,
        "get": lambda st: float(_prestige(st)),
    },
    "mining_silver_annual": {
        "name": "官办银矿年额", "unit": "万两/年", "scale": 1.0, "digits": 1,
        "get": _mining_silver_annual,
    },
    "army_arrears_total": {
        "name": "全国军队欠饷", "unit": "万两", "scale": 1.0, "digits": 2,
        "get": _army_arrears_total,
    },
    "carrying_total": {
        "name": "明属各省承载力合计", "unit": "万", "scale": 1.0, "digits": 0,
        "get": _carrying_total,
    },
}


def _liquidity_health(state: Any) -> float:
    from .economy import calc_liquidity_health, calc_national_demand
    return calc_liquidity_health(float(state.m1), float(state.financial_rate),
                                 calc_national_demand(state))


def _central_corruption(state: Any) -> float:
    from .depts_libu import calc_central_corruption
    return float(calc_central_corruption(state)["value"])


def _prestige(state: Any) -> float:
    from .depts_rites import calc_prestige
    return float(calc_prestige(state)["value"])


def metric_value(state: Any, key: str) -> float:
    meta = METRICS.get(key)
    if meta is None:
        raise KeyError ("未登记的指标：%s 任务定义只能引用 engine/tasks.py 的 METRICS" % key)
    return float(meta["get"](state))


def metric_text(state: Any, key: str, value: Optional[float] = None) -> str:
    """指标显示文本：按登记表换算单位与精度 如 金融替代率 0.2 → 20.0%。"""
    meta = METRICS[key]
    v = metric_value(state, key) if value is None else float(value)
    shown = v * float(meta["scale"])
    text = ("%." + str(int(meta["digits"])) + "f") % shown
    return text + str(meta["unit"])


# ═══════════════════════════════════════════════════════════
# 判定与发点
# ═══════════════════════════════════════════════════════════

def _target_text(task: Dict[str, Any]) -> str:
    meta = METRICS[task["metric"]]
    shown = float(task["value"]) * float(meta["scale"])
    text = ("%." + str(int(meta["digits"])) + "f") % shown
    return "%s %s%s" % (task["op"], text, meta["unit"])


def task_done_ids(state: Any) -> List[str]:
    return [str(x) for x in state.tasks_done]


def is_done(state: Any, tid: str) -> bool:
    return tid in state.tasks_done


def task_rows(state: Any) -> List[Dict[str, Any]]:
    """任务面板数据 前端只渲染，不再自己算指标。"""
    rows = []
    for t in all_tasks():
        op = _OPS.get(t["op"])
        if op is None:
            raise ValueError("任务 %s 的比较符不合法：%r" % (t["id"], t["op"]))
        cur = metric_value(state, t["metric"])
        done = is_done(state, t["id"])
        rows.append({
            "id": t["id"],
            "name": t["name"],
            "desc": t.get("desc", ""),
            "metric_name": METRICS[t["metric"]]["name"],
            "current_text": metric_text(state, t["metric"], cur),
            "target_text": _target_text(t),
            "met": bool(op(cur, float(t["value"]))),
            "done": done,
            "reward_points": float(t.get("reward_points") or 0.0),
        })
    return rows


def evaluate(state: Any) -> List[Dict[str, Any]]:
    """推进时调一次：把刚达成的任务发点记档。返回本次完成的记录 供邸报/日志。"""
    newly = []
    for t in all_tasks():
        tid = t["id"]
        if is_done(state, tid):
            continue
        op = _OPS.get(t["op"])
        if op is None:
            raise ValueError("任务 %s 的比较符不合法：%r" % (tid, t["op"]))
        if not op(metric_value(state, t["metric"]), float(t["value"])):
            continue
        points = float(t.get("reward_points") or 0.0)
        state.tasks_done.append(tid)
        if points:
            grant_tech_points(state, points)
        rec = {"id": tid, "name": t["name"], "reward_points": points}
        newly.append(rec)
        CONSOLE.log("task", "任务达成：%s" % t["name"], "科技点 +%g 当前 %g"
                    % (points, float(state.tech_points)))
    return newly


def grant_tech_points(state: Any, points: float) -> float:
    """唯一的发点入口 任务/成就/主线都走这里。"""
    state.tech_points = round(float(state.tech_points) + float(points), 4)
    return float(state.tech_points)
