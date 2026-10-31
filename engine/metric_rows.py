# -*- coding: utf-8 -*-
"""主界面顶栏各项数值的三段结构：修正一批、计算方式一批、机制一批。

口径：
- 界面上的【ⓘ】只有这一种形态（见前端 metricTip），三段**全部来自引擎**，
  前端不许自己写说明文字顶着——所以要显示的数值，这里都得给出对象来。
- 一个数只有一处来源：存量数取自 state 与各部账册，构成项/流水就是它们本身，不另算一份。

返回结构：
    {"value", "base", "breakdown": [{"name", "value_str", "from"}],
     "calculation_text", "mechanism"}
"""

from typing import Any, Dict, List

SOURCE = "主界面顶栏"


def _row(value: Any, base: Any, breakdown: List[Dict[str, Any]],
         calc: str, mech: str) -> Dict[str, Any]:
    return {"value": value, "base": base, "breakdown": breakdown,
            "calculation_text": calc, "mechanism": mech}


def _ledger_rows(items: List[Dict[str, Any]], sign: str) -> List[Dict[str, Any]]:
    """账册条目 → 修正行：名称 + 本月额 + 出处。"""
    out = []
    for it in items or []:
        name = str(it.get("name") or it.get("item_name") or "").strip()
        amt = it.get("monthly")
        if not name or amt in (None, 0, 0.0):
            continue
        out.append({"name": name,
                    "value_str": "%s%.2f 万两" % (sign, abs(float(amt))),
                    "from": str(it.get("item_name") or it.get("timestamp") or "")})
    return out


def treasury_rows(state: Any) -> Dict[str, Any]:
    """国库：当前结存，附带账册上的本月入账与出账逐项。"""
    from .depts_hubu import calc_treasury_balance_sheets
    sheets = calc_treasury_balance_sheets(state)["national_treasury"]
    incomes = _ledger_rows(sheets.get("incomes"), "+")
    expenses = _ledger_rows(sheets.get("expenses"), "−")
    income_sum = sum(float(i.get("monthly") or 0.0) for i in (sheets.get("incomes") or []))
    expense_sum = sum(float(i.get("monthly") or 0.0) for i in (sheets.get("expenses") or []))
    value = round(float(getattr(state, "treasury", 0.0)), 2)
    return _row(
        value, value, incomes + expenses,
        "当前结存 %.2f 万两；账册本月入账合计 %.2f 万两、出账合计 %.2f 万两"
        % (value, income_sum, expense_sum),
        "国库每月按账册结存：十税解库、辽饷加派、矿课等入账，宗禄、百官俸禄、军饷、"
        "驿站、工程、水利、军械与朝廷月费出账，仓储另有自然损耗。地方银矿每月课税解库入银。")


def privy_rows(state: Any) -> Dict[str, Any]:
    """私库：皇帝内帑流水。"""
    ledger = list(getattr(state, "privy_ledger", []) or [])[-12:]
    rows = []
    for tx in ledger:
        amt = float(tx.get("amount") or 0.0)
        if not amt:
            continue
        rows.append({"name": str(tx.get("reason") or tx.get("name") or "内帑收支"),
                     "value_str": "%s%.2f 万两" % ("+" if amt > 0 else "−", abs(amt)),
                     "from": str(tx.get("time") or tx.get("date") or "")})
    value = round(float(getattr(state, "privy_treasury", 0.0)), 2)
    net = sum(float(tx.get("amount") or 0.0) for tx in (getattr(state, "privy_ledger", []) or [])[-12:])
    return _row(
        value, round(value - net, 2), rows,
        "期初 %.2f + 近十二笔内帑流水合计 %.2f = %.2f 万两" % (value - net, net, value),
        "私库只记皇帝内廷的赏赐、发帑与进奉，不参与朝廷常例收支；由司礼监掌管。")


def m1_rows(state: Any) -> Dict[str, Any]:
    value = round(float(getattr(state, "m1", 0.0)), 2)
    return _row(
        value, value, [],
        "流通 M₁ 由市场交易与窖藏活化、沉淀逐月结算，本月无逐项加成",
        "民间跨区域商品与实体物资流通结算的白银总量；每月在市场交易与流通中自然磨损 0.05%。"
        "社会预期波动时，地方窖藏会活化流入或沉淀退出市场。")


def reserve_rows(state: Any) -> Dict[str, Any]:
    value = round(float(getattr(state, "local_reserve", 0.0)), 2)
    return _row(
        value, value, [],
        "地方银库（地方窖藏）每月按信心变化活化：ΔM₁ = 0.5 × (1 − 地方银库/M₂) × 地方银库 × Δ信心，"
        "为正则窖藏活化流入市场，为负则市场银回流沉淀",
        "全国地方府库与民间沉淀窖藏白银；地方解缴国库、国库拨付地方都记在这一本账上，"
        "每月另有 0.005% 的自然损耗。活化量不另存一份，直接进出流通 M₁。")


def changying_rows(state: Any) -> Dict[str, Any]:
    value = round(float(getattr(state, "changying_treasury", 0.0)), 2)
    return _row(
        value, value, [],
        "常盈库为太仆寺马政专账余额，每年公历元旦自地方窖藏抽解马价银后重新结算",
        "太仆寺马政存银自成一本账：马匹生产自此处扣支，不足则当月停产；"
        "与国库、私库、地方银库一样计入广义货币总量 M₂。")


def m2_rows(state: Any) -> Dict[str, Any]:
    parts = [
        ("流通 M₁", "m1"),
        ("国库", "treasury"),
        ("私库", "privy_treasury"),
        ("地方银库", "local_reserve"),
        ("太仆寺常盈库", "changying_treasury"),
    ]
    breakdown = []
    total = 0.0
    for label, attr in parts:
        v = float(getattr(state, attr, 0.0) or 0.0)
        total += v
        breakdown.append({"name": label, "value_str": "%.2f 万两" % v, "from": "账册余额"})
    value = round(float(getattr(state, "m2", total)), 2)
    return _row(
        value, 0.0, breakdown,
        "M₂ = %.2f + %.2f + %.2f + %.2f + %.2f = %.2f 万两"
        % tuple([float(getattr(state, a, 0.0) or 0.0) for _, a in parts] + [value]),
        "广义货币总量是全域白银存量资产的总规模，随各项账册余额逐月变动；"
        "成立的前提是账册里各构成项都列出来，屏幕上 M₂ 才能与下列各项对上。")


def health_rows(state: Any) -> Dict[str, Any]:
    from .economy import calc_liquidity_health, calc_national_demand
    m1 = float(getattr(state, "m1", 0.0) or 0.0)
    frate = float(getattr(state, "financial_rate", 0.0) or 0.0)
    demand = float(calc_national_demand(state) or 0.0)
    value = round(float(calc_liquidity_health(m1, frate, demand)), 2)
    return _row(
        value, 1.0, [],
        "g = 流通 M₁ %.1f ÷ ((1 − 替代率 f %.1f%%) × 全国需求 G %.0f) = %.2f"
        % (m1, frate * 100.0, demand, value),
        "标准健康值为 1.0：大于 1.0 是流通货币过剩，易引发物价虚高或白银外流；"
        "小于 1.0 是通货紧缩，现银短缺将阻碍物资流通。")


def demand_rows(state: Any) -> Dict[str, Any]:
    """全国需求 G：界面显示的是需求规模本身，修正按需求那一本账逐条列出。

    注意：登记处给的 value 是**修正乘数**（如 0.83），与界面上以万单位计的 G 不是一个量，
    所以这里不拿两者做算术，只把乘数与修正行如实列出，算式写清各自的量纲。
    """
    from .economy import calc_national_demand
    from .modifier_registry import value as mod_value
    p = mod_value(state, "demand", 1.0)
    value = round(float(calc_national_demand(state) or 0.0), 1)
    mult = float(p.get("value") or 1.0)
    return _row(
        value, value, list(p.get("breakdown") or []),
        "需求规模 %.1f 万单位；需求修正乘数 %.2f 即 %+.1f%%"
        % (value, mult, (mult - 1.0) * 100.0),
        "需求按人口与生活水平推算；物价、贸易状态与各界面上的修正按全站唯一公式增减。")


def gold_rows(state: Any) -> Dict[str, Any]:
    parts = [("国库黄金", "gold_treasury"), ("地方窖藏黄金", "gold_local_reserve"),
             ("流通黄金", "gold_m1")]
    breakdown = []
    total = 0.0
    for label, attr in parts:
        v = float(getattr(state, attr, 0.0) or 0.0)
        total += v
        breakdown.append({"name": label, "value_str": "%.2f 万两" % v, "from": "账册余额"})
    value = round(float(getattr(state, "gold_total", total)), 2)
    return _row(
        value, 0.0, breakdown,
        "黄金储备 = %.2f + %.2f + %.2f = %.2f 万两"
        % (float(getattr(state, "gold_treasury", 0.0) or 0.0),
           float(getattr(state, "gold_local_reserve", 0.0) or 0.0),
           float(getattr(state, "gold_m1", 0.0) or 0.0), value),
        "黄金与白银分账：国库黄金、地方窖藏黄金与流通黄金三处相加即总存量，"
        "拨付、起获与流通磨损都记在各自那一本账上。")


def population_rows(state: Any, provinces: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = sum(float(p.get("population") or 0.0) for p in provinces)
    breakdown = [{"name": str(p.get("name") or p.get("id") or ""),
                  "value_str": "%.1f 万" % float(p.get("population") or 0.0),
                  "from": "地方合计"} for p in provinces
                 if float(p.get("population") or 0.0) > 0]
    return _row(
        round(total, 1), 0.0, breakdown,
        "人口 = 各地方人口之和 = %.1f 万" % round(total, 1),
        "只统计当前由朝廷控制的省份；出生、死亡、饥荒与流民转化按月度结算。")


def faction_rows(state: Any) -> Dict[str, Any]:
    """中央党派控制比例：权重口径与当前分布。"""
    from .factions import calc_overall_faction_control
    d = calc_overall_faction_control(state) or {}
    ratios = d.get("ratios") or {}
    rows = [{"name": str(k), "value_str": "%.1f%%" % float(v), "from": "权重占比"}
            for k, v in ratios.items()]
    total = float(d.get("total_weight") or sum(float(v) for v in ratios.values()))
    return _row(
        round(total, 2), 0.0, rows,
        "控制比例按权重算出：六部、司礼监、内阁管理层每人 ×5；其他中央部门管理层每人 ×1；"
        "吏部文册档案里的地方官每人 ×0.1。",
        "比例用于判断票拟倾向与党争阻力；司礼监与内阁同党时，留中的章奏按消极结算，不同党则生掣肘。")


def cabinet_faction_rows(state: Any) -> Dict[str, Any]:
    """内阁党派控制比例：辅臣席位占比。"""
    from .factions import get_central_faction_distribution
    d = get_central_faction_distribution() or {}
    ratios = d.get("ratios") or {}
    rows = [{"name": str(k), "value_str": "%.1f%%" % float(v), "from": "阁臣席位"}
            for k, v in ratios.items()]
    return _row(
        round(sum(float(v) for v in ratios.values()), 2), 0.0, rows,
        "内阁党派控制比例 = 内阁大学士与管理层各党席位占比",
        "内阁占多数的一党决定票拟倾向：章奏的积极与消极处置、以及廷议中的阻力都受它影响。")


def all_rows(state: Any, provinces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一份打包：接口直接挂到 /api/state 上，前端只渲染。"""
    return {
        "treasury_calc": treasury_rows(state),
        "privy_calc": privy_rows(state),
        "m1_calc": m1_rows(state),
        "local_reserve_calc": reserve_rows(state),
        "changying_calc": changying_rows(state),
        "m2_calc": m2_rows(state),
        "health_calc": health_rows(state),
        "demand_calc": demand_rows(state),
        "gold_calc": gold_rows(state),
        "population_calc": population_rows(state, provinces),
        "faction_calc": faction_rows(state),
        "cabinet_faction_calc": cabinet_faction_rows(state),
    }
