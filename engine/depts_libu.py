"""吏部专业模块：全国各地区腐败程度统计 打表、行政纪律地区修正、文册档案 文官名册。

功能规范：
1. 基本数据：
   - 全国各地区腐败程度打表 包含各承宣布政司/属地基础值、百分比修正、最终腐败指数与评级。
   - 中央腐败程度 基准 50 ×  1 + 百分比修正 + 加减修正。
2. 行政纪律 地区修正与中枢政纪：
   - 党争工具：吏部尚书和文选司郎中作为人事主管，往往是各党派争夺的焦点。全国各地区腐败程度+2%，中央腐败程度+20%
   - 风闻言事：明末言官可以凭“风闻”弹劾任何官员，不需要证据。中央腐败程度+10%
   - 吏律实亡：所谓的法律规则已经瘫痪，政府完全依靠腐败和私人关系运行。全国各地区腐败程度+10%，中央腐败程度+15%
   - 京察瘫痪：明末京察已经沦为中央各党互相清洗的工具。全国各地区腐败程度+5%，全国各地区人口自然承载力-10%
3. 文册档案：
   - 包含知府及以上地方主要文官名册 排除武官总兵等。
   - 按省份/地域分类，支持前端展开与折叠。
"""

from typing import Any, Dict, List, Tuple
from .officials import civil_roster
from .modifier_registry import initial_buffs
from .modifier_registry import apply_formula

# ── 行政纪律修正基础配置 ──
# 修正数据在 world/modifiers.json 唯一数据源。
# ⚠ 本列表会被超控指令在运行时增删，故仍以列表形式存于本模块；
#   初始值由 modifier_registry 的兼容投影给出，消费点改成按 target 读效果后即可去掉投影。
ADMIN_DISCIPLINE_BUFFS = initial_buffs("吏部 · 行政纪律")


def calc_central_corruption(state=None) -> Dict[str, Any]:
    """中央腐败程度：基础值 50，走全站唯一公式。

    返回结构就是 modifier_registry.value() 的原样：base / mult_sum / add_sum /
    value / formula / calculation_text / mechanism / breakdown。
    界面不再为任何单个数值单开字段——威望、腐败、税率都是同一个形状。
    """
    from .modifier_registry import value as mod_value
    r = mod_value(state, "central_corruption", 50.0)
    r["value"] = round(r["value"], 1)
    return r


def calc_local_corruption(state=None, loc=None, pid=None) -> Dict[str, Any]:
    """地方腐败程度：基础值 50，走全站唯一公式。

    限定修正范围：只对**当前属明朝控制**的省份 看控制权加朝廷六部政纪修正；
    非明控省份用原始值。贡献项由 modifier_registry 统一收集。
    """
    from .localdata import under_ming_control
    from .modifier_registry import value as mod_value, mechanism_of
    if state is not None and pid is not None and pid in state.provinces:
        p_controlled = under_ming_control(state.provinces[pid])
    elif loc is not None:
        p_controlled = under_ming_control(loc=loc)
    else:
        p_controlled = False
    if not p_controlled:
        raw_val = float((loc or {}).get("corruption") or 0.0)
        # 非明控省份没有朝廷修正：仍是同一形状，只是没有贡献项
        return {
            "target": "local_corruption",
            "name": "地方腐败程度",
            "base": raw_val,
            "mult_sum": 0.0,
            "add_sum": 0.0,
            "value": raw_val,
            "formula": "非明控制省份，不适用朝廷六部政纪修正",
            "calculation_text": f"原始腐败值 = {raw_val}",
            "breakdown": [],
            "mechanism": mechanism_of("local_corruption"),
        }

    r = mod_value(state, "local_corruption", 50.0, province_id=pid)
    r["value"] = round(r["value"], 1)
    return r


def calc_local_carrying(base_carrying: float, state=None, loc=None, pid=None) -> Dict[str, Any]:
    """地方人口自然承载力：基础值走全站唯一公式。

    限定修正范围：只对**当前属明朝控制**的省份加朝廷六部政纪修正；
    贡献项由 modifier_registry 统一收集。
    """
    from .localdata import under_ming_control
    from .modifier_registry import value as mod_value, mechanism_of
    base = float(base_carrying or 0.0)
    if state is not None and pid is not None and pid in state.provinces:
        p_controlled = under_ming_control(state.provinces[pid])
    elif loc is not None:
        p_controlled = under_ming_control(loc=loc)
    else:
        p_controlled = False
    if not p_controlled:
        return {
            "target": "carrying_pct",
            "name": "人口承载力",
            "base": base,
            "mult_sum": 0.0,
            "add_sum": 0.0,
            "value": base,
            "formula": "非明控制省份，不适用朝廷六部政纪修正",
            "calculation_text": f"原始承载力 = {base:.0f}",
            "breakdown": [],
            "mechanism": mechanism_of("carrying_pct"),
        }

    r = mod_value(state, "carrying_pct", base, province_id=pid)
    r["value"] = round(r["value"], 1)
    return r


def get_libu_roster_groups():
    """吏部文册档案：官职表里 kind=文职 的在任者，按省分组，附加党派。"""
    from .factions import get_character_faction
    roster_groups = civil_roster(include_capital=False)
    for prov, list_officers in roster_groups.items():
        for o in list_officers:
            o["faction"] = get_character_faction(o.get("name", ""), title=o.get("title", ""), location=prov)
    return roster_groups


def get_libu_overview(state) -> Dict[str, Any]:
    """吏部汇总视图数据。"""
    central_corr = calc_central_corruption(state)
    local_corr = calc_local_corruption(state)

    # 统计当前明控各省腐败打表
    from .localdata import under_ming_control
    prov_table = []
    for pid, p in state.provinces.items():
        if not under_ming_control(p):
            continue
        loc = p.get("local") or {}
        p_name = p.get("name", pid)
        raw_carrying = float(loc.get("base_carrying") or 0.0)
        carrying_info = calc_local_carrying(raw_carrying, state, loc, pid)

        # 腐败程度统一计算
        corr_info = calc_local_corruption(state, loc, pid)
        corr_val = corr_info["value"]

        # 腐败评级
        if corr_val >= 70:
            rating = "极度腐败"
            r_color = "#a63c2b"
        elif corr_val >= 55:
            rating = "严重腐败"
            r_color = "#c48b28"
        elif corr_val >= 40:
            rating = "纲纪废弛"
            r_color = "#8a6a2a"
        else:
            rating = "吏治尚可"
            r_color = "#2a5caa"

        prov_table.append({
            "province_id": pid,
            "province_name": p_name,
            "corruption": corr_val,
        })

    # 文官名册 排除武官，附加党派属性
    roster_groups = get_libu_roster_groups()
    total_civil = sum(len(v) for v in roster_groups.values())

    from .modifiers import discipline_rows, merge_modifier_rows

    return {
        "central_corruption": central_corr,
        "local_corruption_common": local_corr,
        "discipline_rows": merge_modifier_rows(discipline_rows()),
        "provinces_table": prov_table,
        "roster_groups": roster_groups,
        "roster_total": total_civil,
    }
