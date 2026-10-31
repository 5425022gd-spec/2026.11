"""礼部专业模块：外交事务、对外贸易、宗教管理、礼仪常祀与灾异祭祀、科举教育与皇室威望。

功能规范：
1. 外交事务 外交关系总览打表、对外贸易管理：
   - 外交关系：朝鲜、东番、日本、满清 旧西藏各部印区已随地图删除。
   - 贸易管理：出口收汇 茶叶/丝绸/瓷器、进口付汇 动物制品/粮食/香料药材。
   - 贸易差额每月 1 日结算，顺差入地方窖藏，逆差自地方窖藏扣除。
2. 宗教管理：
   - 宗教行：儒家 默认60、佛教 默认20、道教 默认10、民间宗教 默认10。
   - 宗教影响力随祭祀等动态调整。
   - 产能、需求与威望加成根据当前影响力动态核算。
3. 礼仪祭祀体系：
   - 大明常祀 国祭：
     - 社稷坛：每年二月、八月上戊日
     - 山川坛：每年二月、八月1日 避戊顺延至2日
     - 雩祀：仲夏 五月1日
     - 先农坛：每年二月上亥日
   - 灾异临时祭祀：
     - 每次灾害死亡判定后记录待办，于次月 1 日刷新加入祭祀清单。
     - 每个灾害同时刷新三教祭祀 佛教超度、儒教消灾、道教驱邪，彼此互不冲突。
     - 祭祀花费：死亡人数 × 0.05 两 折万两。
     - 超时惩罚：扣除威望，死亡人数 × 0.00005 点。
     - 超时计时：30 天倒计时。
     - 祭祀选择：点击开始祭祀，第二天正式举行并生效：该宗教影响力相对值 +1%。
4. 科举教育：
   - 地方教育受教育人口与高等教育人口年度结算增长。
5. 皇室威望 0至100：
   - 基础值 ×  1 + 宗教威望修正 + 其他加成 + 加减修正。
"""

import json
import os
import uuid
from typing import Any, Dict, List, Tuple
from .economy import get_gregorian_date
from .calendar import calc_annual_sacrifices

# ── 外交关系总览基础配置 ──
DIPLOMATIC_COUNTRIES = [
    {
        "id": "chaoxian",
        "name": "朝鲜",
        "relation": "和平",
        "trade_status": "禁贸",
        "tribute_annual": 0.0,
    },
    {
        "id": "dongfan",
        "name": "东番",
        "relation": "藩属",
        "trade_status": "贸易",
        "tribute_annual": 0.5,
    },
    {
        "id": "riben",
        "name": "日本",
        "relation": "和平",
        "trade_status": "禁贸",
        "tribute_annual": 0.0,
    },
    {
        "id": "houjin",
        "name": "满清",
        "relation": "战争",
        "trade_status": "禁贸",
        "tribute_annual": 0.0,
    }
]

# ── 贸易管理基础条目配置 ──
TRADE_EXPORTS = [
    {
        "id": "export_tea",
        "name": "茶叶出口",
        "flat_mod": 3.50,
        "pct_mod": 0.0,
    },
    {
        "id": "export_silk",
        "name": "丝绸出口",
        "flat_mod": 5.00,
        "pct_mod": 0.0,
    },
    {
        "id": "export_porcelain",
        "name": "瓷器出口",
        "flat_mod": 3.00,
        "pct_mod": 0.0,
    }
]

TRADE_IMPORTS = [
    {
        "id": "import_animal",
        "name": "动物制品进口",
        "flat_mod": 2.00,
        "pct_mod": 0.0,
    },
    {
        "id": "import_grain",
        "name": "粮食进口",
        "flat_mod": 1.50,
        "pct_mod": 0.0,
    },
    {
        "id": "import_spice",
        "name": "香料与药材进口",
        "flat_mod": 1.20,
        "pct_mod": 0.0,
    }
]

# ── 宗教管理基础配置 ──
RELIGION_CONFIGS = [
    {
        "name": "儒家",
        "default_inf": 60.0,
        "prod_coeff": 0.001,     # 产能百分比加成 = 影响力 × 0.1%
        "demand_coeff": 0.0,
        "prestige_coeff": 0.001,  # 威望百分比加成 = 影响力 × 0.1%
    },
    {
        "name": "佛教",
        "default_inf": 20.0,
        "prod_coeff": -0.001,    # 产能百分比加成 = 影响力 × (-0.1%)
        "demand_coeff": -0.001,  # 需求百分比加成 = 影响力 × (-0.1%)
        "prestige_coeff": 0.002, # 威望百分比加成 = 影响力 × 0.2%
    },
    {
        "name": "道教",
        "default_inf": 10.0,
        "prod_coeff": 0.0,
        "demand_coeff": 0.0,
        "prestige_coeff": 0.0015, # 威望百分比加成 = 影响力 × 0.15%
    },
    {
        "name": "民间宗教",
        "default_inf": 10.0,
        "prod_coeff": 0.0015,    # 产能百分比加成 = 影响力 × 0.15%
        "demand_coeff": 0.0,
        "prestige_coeff": 0.0,
    }
]


def calc_trade_summary(state=None) -> Dict[str, Any]:
    """计算对外贸易出口收汇、进口付汇与贸易差额。"""
    exports = []
    total_export_monthly = 0.0
    for exp in TRADE_EXPORTS:
        flat = float(exp.get("flat_mod", 0.0))
        pct = float(exp.get("pct_mod", 0.0))
        amt = round(flat * (1.0 + pct), 2)
        total_export_monthly += amt
        exports.append({
            "id": exp["id"],
            "name": exp["name"],
            "flat_mod": flat,
            "pct_mod": pct,
            "pct_str": f"{'+' if pct > 0 else ''}{pct * 100:.1f}%",
            "monthly": amt,
            "annual": round(amt * 12.0, 1),
            "calc_text": f"{flat:.2f} × (1 + {pct * 100:.1f}%) = {amt:.2f} 万两/月",
        })

    imports = []
    total_import_monthly = 0.0
    for imp in TRADE_IMPORTS:
        flat = float(imp.get("flat_mod", 0.0))
        pct = float(imp.get("pct_mod", 0.0))
        amt = round(flat * (1.0 + pct), 2)
        total_import_monthly += amt
        imports.append({
            "id": imp["id"],
            "name": imp["name"],
            "flat_mod": flat,
            "pct_mod": pct,
            "pct_str": f"{'+' if pct > 0 else ''}{pct * 100:.1f}%",
            "monthly": amt,
            "annual": round(amt * 12.0, 1),
            "calc_text": f"{flat:.2f} × (1 + {pct * 100:.1f}%) = {amt:.2f} 万两/月",
        })

    total_export_monthly = round(total_export_monthly, 2)
    total_import_monthly = round(total_import_monthly, 2)
    net_trade_monthly = round(total_export_monthly - total_import_monthly, 2)
    net_trade_annual = round(net_trade_monthly * 12.0, 1)

    is_surplus = net_trade_monthly >= 0
    trade_type = "顺差" if is_surplus else "逆差"

    return {
        "exports": exports,
        "imports": imports,
        "total_export_monthly": total_export_monthly,
        "total_export_annual": round(total_export_monthly * 12.0, 1),
        "total_import_monthly": total_import_monthly,
        "total_import_annual": round(total_import_monthly * 12.0, 1),
        "net_trade_monthly": net_trade_monthly,
        "net_trade_annual": net_trade_annual,
        "trade_type": trade_type,
        # 机制：这一项为什么会动 界面 ⓘ 的「机制」那一批念的就是这两句
        "mechanism": "出口收汇与进口付汇各按本口岸的加减修正与百分比修正算出月额；"
                     "沿海口岸的贸易量随海贸政策、战事封锁与白银存量变动。",
        "net_mechanism": "各国贸易差＝出口收汇 − 进口付汇。顺差直接加入地方窖藏，"
                         "逆差从地方窖藏中扣除，每月一日凌晨结算。",
    }


def calc_religion_summary(state=None) -> Dict[str, Any]:
    """计算宗教动态影响力及对产能、需求与威望的影响。"""
    rows = []
    total_prod_pct = 0.0
    total_demand_pct = 0.0
    total_prestige_pct = 0.0

    current_influences = state.religion_influences if state is not None else {}

    for r in RELIGION_CONFIGS:
        r_name = r["name"]
        inf = float(current_influences.get(r_name, r["default_inf"]))
        p_pct = round(inf * r["prod_coeff"], 4)
        d_pct = round(inf * r["demand_coeff"], 4)
        pr_pct = round(inf * r["prestige_coeff"], 4)

        total_prod_pct += p_pct
        total_demand_pct += d_pct
        total_prestige_pct += pr_pct

        mods = []
        if p_pct != 0.0:
            mods.append (f"全国各地区全阶级产能 {p_pct * 100:+.1f}%")
        if d_pct != 0.0:
            mods.append (f"全国各阶级需求 {d_pct * 100:+.1f}%")
        if pr_pct != 0.0:
            mods.append (f"威望 {pr_pct * 100:+.1f}%")

        rows.append({
            "name": r_name,
            "influence": round(inf, 1),
            "modifier_text": "；".join(mods) if mods else "无",
            "prod_pct": p_pct,
            "demand_pct": d_pct,
            "prestige_pct": pr_pct,
            # 系数原样给出 界面用它写公式说明，数值只有这一处来源
            "coeffs": {"prod": r["prod_coeff"], "demand": r["demand_coeff"],
                       "prestige": r["prestige_coeff"]},
        })

    return {
        "religions": rows,
        "total_prod_pct": round(total_prod_pct, 4),
        "total_demand_pct": round(total_demand_pct, 4),
        "total_prestige_pct": round(total_prestige_pct, 4),
        "other_modifiers": []
    }


def calc_prestige(state=None) -> Dict[str, Any]:
    """皇室威望 0~100。

    返回结构就是 modifier_registry.value() 的原样：base / mult_sum / add_sum /
    value / formula / calculation_text / mechanism / breakdown。
    与中央腐败、地方腐败、税率是同一个形状——不再有「宗教加成」「其他加成」这种
    单为威望开的字段，宗教只是 breakdown 里的一行。
    """
    from .modifier_registry import value as mod_value
    base = float(getattr(state, "base_prestige", 60.0)) if state else 60.0
    r = mod_value(state, "prestige", base)
    r["value"] = round(min(100.0, max(0.0, r["value"])), 1)   # 威望有上下限，算完再夹
    return r


def calc_education_summary(state=None) -> Dict[str, Any]:
    """科举与教育人口：两个人口栏共用一套教育修正，另有教育本身的产能加成。

    受教育人口 = 已有对应认知但未考中者 + 已考中者 不等于学堂招生数，也不等于中式人数。
    """
    tot_pop = 16980.0
    if state is not None:
        from .depts_hubu import calc_hubu_basic_stats
        tot_pop = calc_hubu_basic_stats(state)["total_actual_pop"]

    # 教育修正就是修正：只从统一修正里按词条 target 取，不再有第二处存储
    def _edu_rows(target):
        out = []
        for dmod in (getattr(state, "department_modifiers", None) or {}).values():
            for eff in (dmod.get("effects") or []):
                if eff.get("target") != target:
                    continue
                out.append({"name": dmod.get("name", ""),
                            "pct_mod": float(eff.get("delta_pct") or 0.0),
                            "flat_mod": (0.0 if eff.get("delta_pct") is not None
                                         else float(eff.get("delta") or 0.0)),
                            "desc": dmod.get("intro") or eff.get("desc", "")})
        return out

    local_mods = _edu_rows("edu_growth")
    higher_mods = _edu_rows("higher_edu_growth")
    local_pct = sum(m["pct_mod"] for m in local_mods)
    local_flat = sum(m["flat_mod"] for m in local_mods)
    higher_pct = sum(m["pct_mod"] for m in higher_mods)
    higher_flat = sum(m["flat_mod"] for m in higher_mods)

    # 修正值由登记处出 教育修正行 + 科技效果都是它的贡献项，走全站唯一公式
    from .modifier_registry import value as _mod_value
    local_mod_val = _mod_value(state, "edu_growth", 1.0)["value"] if state is not None else 1.0
    higher_mod_val = _mod_value(state, "higher_edu_growth", 1.0)["value"] if state is not None else 1.0

    local_educated_pop = getattr(state, "local_educated_pop", 185.0) if state else 185.0
    higher_educated_pop = getattr(state, "higher_educated_pop", 15.0) if state else 15.0

    local_annual_growth = round(tot_pop * 0.0001 * local_mod_val, 2)
    higher_annual_growth = round(local_educated_pop * 0.1 * higher_mod_val, 2)

    t_local = (local_educated_pop / tot_pop) if tot_pop > 0 else 0.0
    t_higher = (higher_educated_pop / tot_pop) if tot_pop > 0 else 0.0
    local_bonus = 1.549 * (t_local ** 1.735) / ((t_local ** 1.735) + 0.549)
    higher_bonus = 1.00235 * (t_higher ** 1.955) / ((t_higher ** 1.955) + 0.00235)

    unemp = structural_unemployment(t_higher)

    return {
        "total_pop": round(tot_pop, 2),
        "mod_calc_rule": "1 ×  1 + 百分比修正 + 加减修正",
        "higher_education": {
            "current_pop": higher_educated_pop,
            "annual_growth_expected": higher_annual_growth,
            "calc_rule": "总受教育人口 × 0.1 × 受高等教育人口修正",
            "mod_val": higher_mod_val,
            "pct_mod": higher_pct,
            "flat_mod": higher_flat,
            "edu_modifiers": [{
                "name": m.get("name", ""),
                "pct_mod": float(m.get("pct_mod") or 0.0),
                "flat_mod": float(m.get("flat_mod") or 0.0),
                "desc": m.get("desc", ""),
            } for m in higher_mods],
        },
        "local_education": {
            "current_pop": local_educated_pop,
            "annual_growth_expected": local_annual_growth,
            "calc_rule": "总人口 × 0.0001 × 受教育人口修正",
            "mod_val": local_mod_val,
            "pct_mod": local_pct,
            "flat_mod": local_flat,
            "edu_modifiers": [{
                "name": m.get("name", ""),
                "pct_mod": float(m.get("pct_mod") or 0.0),
                "flat_mod": float(m.get("flat_mod") or 0.0),
                "desc": m.get("desc", ""),
            } for m in local_mods],
        },
        "bonus": {
            # formula 只写"加成怎么算" pct 本身就是加成本身，不是乘数，t 的定义与取值由前端 ⓘ 说明，避免重复
            "local": {"t": t_local, "pct": local_bonus,
                      "formula": "加成 = 1.549 × t^1.735 / (t^1.735 + 0.549)"},
            "higher": {"t": t_higher, "pct": higher_bonus,
                       "formula": "加成 = 1.00235 × t^1.955 / (t^1.955 + 0.00235)"},
            "total_pct": local_bonus + higher_bonus,
            "desc": "全国各地区各阶级生产力增加",
        },
        "unemployment": unemp,
        "exams": exam_rows(state),
    }


# ── 教育本身的产能加成：全国各地区各阶级生产力增加 ──

STRUCTURAL_UNEMPLOYMENT_NAME = "结构性失业"
UNEMPLOYMENT_THRESHOLD = 0.1      # 受高等教育人口 / 总人口 超过此值即触发
UNEMPLOYMENT_STEP = 0.001         # 每超出 0.001 即 0.1%
UNEMPLOYMENT_PENALTY_PER_STEP = 0.0002   # 每档全国各地区各阶级产能 -0.02%


def structural_unemployment(t_higher: float) -> Dict[str, Any]:
    """受高等教育人口占比过高 → 结构性失业：每超出一档，全国产能减一档。"""
    if t_higher <= UNEMPLOYMENT_THRESHOLD:
        return {"active": False, "t": t_higher, "excess": 0.0, "steps": 0, "penalty_pct": 0.0,
                "rule": "受高等教育人口 / 总人口 超过 %g 即触发，每超出 %g 全国各地区各阶级产能 -%.2f%%"
                        % (UNEMPLOYMENT_THRESHOLD, UNEMPLOYMENT_STEP, UNEMPLOYMENT_PENALTY_PER_STEP * 100)}
    excess = t_higher - UNEMPLOYMENT_THRESHOLD
    steps = int(excess / UNEMPLOYMENT_STEP)
    return {"active": True, "t": t_higher, "excess": excess, "steps": steps,
            "penalty_pct": steps * UNEMPLOYMENT_PENALTY_PER_STEP,
            "rule": "受高等教育人口 / 总人口 超过 %g 即触发，每超出 %g 全国各地区各阶级产能 -%.2f%%"
                    % (UNEMPLOYMENT_THRESHOLD, UNEMPLOYMENT_STEP, UNEMPLOYMENT_PENALTY_PER_STEP * 100)}


def refresh_structural_unemployment(state: Any) -> str:
    """按当前占比刷新 或撤掉「结构性失业」修正；返回一条日志。"""
    if state is None:
        return ""
    from .economy import CLASSES_ORDER
    edu = calc_education_summary(state)
    unemp = edu["unemployment"]
    mods = state.department_modifiers
    if mods is None:
        mods = state.department_modifiers = {}
    if not unemp["active"]:
        if STRUCTURAL_UNEMPLOYMENT_NAME in mods:
            del mods[STRUCTURAL_UNEMPLOYMENT_NAME]
            return "礼部岁首科举教育：受高等教育人口占比回落，结构性失业已消解。"
        return ""
    classes = list(CLASSES_ORDER)
    penalty = unemp["penalty_pct"]
    desc = "全国各地区各阶级产能%.2f%%" % (-penalty * 100)
    mods[STRUCTURAL_UNEMPLOYMENT_NAME] = {
        "dept": "礼部",
        "name": STRUCTURAL_UNEMPLOYMENT_NAME,
        "content_str": desc,
        "effects": [{"target": "capacity", "classes": classes, "delta_pct": -penalty, "desc": desc}],
        "summary": desc,
    }
    return ("礼部岁首科举教育：受高等教育人口占比 %.4f 已过 %.2f，结构性失业加剧 %s。"
            % (unemp["t"], UNEMPLOYMENT_THRESHOLD, desc))


def education_bonus_pct(state: Any) -> float:
    """教育带来的全国各地区各阶级产能加成 小数，如 0.0123 即 +1.23%。"""
    if state is None:
        return 0.0
    return float(calc_education_summary(state)["bonus"]["total_pct"])


# ── 科举考试 乡试/会试/殿试：间隔相对上次考试，并同步到日历 ──

EDU_FILE = os.path.join("world", "education.json")

_EDU_DATA = None


def load_education_data() -> Dict[str, Any]:
    global _EDU_DATA
    if _EDU_DATA is None:
        # 读不到就报出来，不偷偷给空表
        with open(EDU_FILE, "r", encoding="utf-8") as f:
            _EDU_DATA = json.load(f)
    return _EDU_DATA


def _edu_mod_rows(key: str) -> List[Dict[str, Any]]:
    out = []
    for m in load_education_data().get(key, []):
        out.append({"name": m.get("name", ""), "pct_mod": float(m.get("pct_mod") or 0.0),
                    "flat_mod": float(m.get("flat_mod") or 0.0), "desc": m.get("desc", "")})
    return out


def seed_education_modifiers(state) -> int:
    """把教育修正登记成统一修正（显示位置＝礼部，范围＝全国）。

    真相源只有 state.department_modifiers 一处：老的 local_edu_modifiers /
    higher_edu_modifiers 在这里一次性搬过去并清空，老存档读进来也走这里。
    """
    moved = 0
    for key, target, label in (("local_edu_modifiers", "edu_growth", "受教育人口年增"),
                               ("higher_edu_modifiers", "higher_edu_growth", "受高等教育人口年增")):
        rows = list(getattr(state, key, None) or [])
        for m in rows:
            name = str(m.get("name") or "")
            if not name:
                continue
            pct = float(m.get("pct_mod") or 0.0)
            flat = float(m.get("flat_mod") or 0.0)
            effs = []
            if pct:
                effs.append({"target": target, "delta_pct": pct,
                             "desc": "%s%+.2f%%" % (label, pct * 100)})
            if flat:
                effs.append({"target": target, "delta": flat,
                             "desc": "%s%+g" % (label, flat)})
            if not effs:
                continue
            state.department_modifiers[name] = {
                "name": name, "location": "礼部", "dept": "礼部", "scope": "全国",
                "intro": m.get("desc", ""), "content_str": m.get("desc", ""),
                "effects": effs, "summary": "，".join(e["desc"] for e in effs),
            }
            moved += 1
        setattr(state, key, [])
    return moved


def opening_local_edu_modifiers() -> List[Dict[str, Any]]:
    """开局的受教育人口修正。"""
    return _edu_mod_rows("local_edu_modifiers")


def opening_higher_edu_modifiers() -> List[Dict[str, Any]]:
    """开局的受高等教育人口修正。"""
    return _edu_mod_rows("higher_edu_modifiers")


def _exam_next_year(state: Any, exam: Dict[str, Any]) -> int:
    """下次开考的年：上次开考年 + 间隔 没考过就从今年起。"""
    from .calendar import game_to_gregorian
    cur_year = int(getattr(state, "date", {}).get("year", 1627)) if state is not None else 1627
    interval = int(exam.get("interval_years") or 3)
    last = (getattr(state, "exam_last", None) or {}).get(exam.get("id", "")) if state is not None else None
    if last is None:
        return cur_year
    y = int(last) + interval
    return max(y, cur_year)


def exam_rows(state: Any) -> List[Dict[str, Any]]:
    """三场考试的开考设置：上次开考、间隔、下次开考 同步到日历、开设科目。"""
    from .calendar import game_to_gregorian
    rows = []
    last_map = (getattr(state, "exam_last", None) or {}) if state is not None else {}
    for e in load_education_data().get("exams", []):
        idx = e.get("id", "")
        last_year = last_map.get(idx)
        last_date = ""
        if last_year is not None:
            g = game_to_gregorian({"year": int(last_year), "month": int(e.get("lunar_month", 1)),
                                   "day": int(e.get("lunar_day", 1))})
            last_date = "%d年%d月%d日" % (g.year, g.month, g.day)
        y = _exam_next_year(state, e)
        g = game_to_gregorian({"year": y, "month": int(e.get("lunar_month", 1)),
                               "day": int(e.get("lunar_day", 1))})
        rows.append({
            "id": idx,
            "name": e.get("name", ""),
            "interval_years": int(e.get("interval_years") or 3),
            "subjects": e.get("subjects", ""),
            "note": e.get("note", ""),
            "lunar": "%d月%d日" % (int(e.get("lunar_month", 1)), int(e.get("lunar_day", 1))),
            "last_date": last_date or "未开考",
            "next_year": y,
            "next_date": "%d年%d月%d日" % (g.year, g.month, g.day),
            "next_greg": {"year": g.year, "month": g.month, "day": g.day},
        })
    return rows


def tick_exams(state: Any) -> List[str]:
    """考试日到了就算开过一场 下次开考以这次为基准。"""
    if state is None:
        return []
    from .calendar import game_to_gregorian
    greg = get_gregorian_date(state.date)
    last_map = getattr(state, "exam_last", None)
    if last_map is None:
        last_map = state.exam_last = {}
    notes = []
    for r in exam_rows(state):
        d = r["next_greg"]
        if (d["year"], d["month"], d["day"]) <= (greg.year, greg.month, greg.day):
            if last_map.get(r["id"]) != r["next_year"]:
                last_map[r["id"]] = r["next_year"]
                notes.append("礼部科举：%d 年%s已开考 开设科目：%s，下次开考隔 %d 年。"
                             % (r["next_year"], r["name"], r["subjects"], r["interval_years"]))
    return notes


# ── 灾异祭祀核心系统 ──

def tick_sacrifices_monthly(state: Any) -> List[str]:
    """次月 1 日刷新上月累积的灾害死亡事件，为每个灾害生成 3 个宗教的祭祀要求。"""
    notes = []
    if state is None:
        return notes

    pending = getattr(state, "pending_sacrifices", [])
    if not pending:
        return notes

    sac_list = state.sacrifices

    count_created = 0
    for idx, p_ev in enumerate(pending):
        deaths = int(p_ev.get("deaths", 0))
        if deaths <= 0:
            continue

        title = p_ev.get("title") or "灾异"
        cost_silver = round(deaths * 0.05, 1)        # 花费银两 = 死亡人数 × 0.05
        cost_wan = round(cost_silver / 10000.0, 4)   # 万两
        penalty = round(deaths * 0.00005, 4)         # 惩罚威望 = 死亡人数 × 0.00005

        token = uuid.uuid4().hex[:6]
        # 三教祭祀定义  彼此互不冲突
        rel_triplet = [
            ("佛教超度", "佛教", "佛门设水陆道场，超度亡魂，往生极乐。"),
            ("儒教消灾", "儒家", "有司致祭社稷山川，抚绥遗氓，修省德政。"),
            ("道教驱邪", "道教", "羽流启建黄箓大斋，驱邪弭灾，清净坛界。"),
        ]

        for r_label, target_r, desc_text in rel_triplet:
            sac_id = f"sac_{state.date['year']}_{state.date['month']}_{token}_{target_r}"
            sac_item = {
                "id": sac_id,
                "title": title,
                "disaster_name": p_ev.get("disaster_name", ""),
                "province_name": p_ev.get("province_name", ""),
                "religion": r_label,
                "target_religion": target_r,
                "deaths": deaths,
                "cost_silver": cost_silver,
                "cost_wan": cost_wan,
                "prestige_penalty": penalty,
                "remain_days": 30,  # 30 天内祭祀
                "status": "pending",  # pending 待祭祀 / in_progress 准备中次日举行 / completed 已祭祀 / expired 已超时
                "created_date": dict(state.date),
                "desc": desc_text,
            }
            sac_list.append(sac_item)
            count_created += 1

    state.pending_sacrifices = []
    if count_created > 0:
        note_text = "礼部呈进灾异祭祀清单：已根据各省灾情生成 %d 项超度消灾祭典 请于30日内敕定行礼" % count_created
        notes.append(note_text)

    return notes


def tick_sacrifices_daily(state: Any) -> List[Dict[str, Any]]:
    """每日推进礼部祭祀倒计时、超时惩罚与次日完成结算。"""
    events = []
    if state is None:
        return events

    sac_list = getattr(state, "sacrifices", [])
    if not sac_list:
        return events

    for item in sac_list:
        status = item.get("status", "pending")

        # 1. 准备中的祭祀 -> 第二天开始祭祀并完成
        if status == "in_progress":
            item["status"] = "completed"
            target_r = item.get ("target_religion", "儒家")
            curr_inf = float(state.religion_influences.get(target_r, 10.0))
            new_inf = round(curr_inf * 1.01, 2)  # 相对值 +1%
            state.religion_influences[target_r] = new_inf

            ev_info = {
                "type": "sacrifice_completed",
                "id": item["id"],
                "title": item["title"],
                "religion": item["religion"],
                "target_religion": target_r,
                "new_influence": new_inf,
            }
            events.append(ev_info)

        # 2. 待祭祀的条目 -> 倒计时递减
        elif status == "pending":
            item["remain_days"] = int(item.get("remain_days", 30)) - 1
            if item["remain_days"] <= 0:
                item["status"] = "expired"
                pen = float(item.get("prestige_penalty", 0.0))
                state.prestige_penalty = round(getattr(state, "prestige_penalty", 0.0) + pen, 4)

                ev_info = {
                    "type": "sacrifice_expired",
                    "id": item["id"],
                    "title": item["title"],
                    "religion": item["religion"],
                    "penalty": pen,
                }
                events.append(ev_info)

    return events


def start_sacrifice(state: Any, sacrifice_id: str) -> Tuple[bool, str]:
    """皇帝敕令开始祭祀：扣除国库银两，次日正式行礼并使该宗教影响力相对值 +1%。"""
    if state is None:
        return False, "系统状态异常"

    sac_list = getattr(state, "sacrifices", [])
    item = next((s for s in sac_list if s["id"] == sacrifice_id), None)
    if not item:
        return False, "未找到该祭祀条目"

    if item.get("status") != "pending":
        if item.get("status") == "completed":
            return False, "该项祭祀已完成"
        if item.get("status") == "in_progress":
            return False, "该项祭祀已在进行中"
        if item.get("status") == "expired":
            return False, "该项祭祀已超时失效"
        return False, "该项祭祀无法执行"

    cost_wan = float(item.get("cost_wan", 0.0))
    if state.treasury < cost_wan:
        return False, f"国库银两不足 需 {cost_wan:.2f} 万两，当前存银 {state.treasury:.2f} 万两"

    # 扣除国库银两
    state.treasury = round(state.treasury - cost_wan, 2)
    state.national_ledger.append({
        "date": dict(state.date),
        "type": "expense",
        "category": "礼仪祭祀",
        "amount": cost_wan,
        "desc": f"灾异祭祀支出 {item['title']} {item['religion']}",
    })

    # 置为准备中，次日完成行礼
    item["status"] = "in_progress"
    return True, f"已敕令礼部备妥祭仪，调拨国库银 {cost_wan:.2f} 万两，次日正式开坛行礼。"


# ── 行礼日排期与当日应祭 ──

# 三教祭祀场所：儒家在奉先殿 内廷家庙、道教在钦安殿 内廷道庙、佛教在英华殿 内廷佛堂
RELIGION_PALACE = {"儒家": "奉先殿", "道教": "钦安殿", "佛教": "英华殿"}
# 同日有多项待祭时，依此优先次序请驾 先奉先殿，无则钦安殿，再无则英华殿
SACRIFICE_RELIGION_ORDER = ["儒家", "道教", "佛教"]


def _scheduled_key(item: Dict[str, Any]) -> str:
    """条目已定行礼日的 YYYY-MM-DD；未排期返回空串。"""
    sd = item.get("scheduled_date") or {}
    if not sd:
        return ""
    try:
        return "%04d-%02d-%02d" % (int(sd.get("year", 0)), int(sd.get("month", 0)), int(sd.get("day", 0)))
    except (TypeError, ValueError):
        return ""


def schedule_sacrifice(state: Any, sacrifice_id: str, year: int, month: int, day: int) -> Tuple[bool, str]:
    """为待祭条目指定行礼日：须落在限期窗口内，且不早于今日。"""
    from .time import date_key, date_shift_key

    if state is None:
        return False, "系统状态异常"
    item = next((s for s in getattr(state, "sacrifices", []) if s.get("id") == sacrifice_id), None)
    if not item:
        return False, "未找到该祭祀条目"
    if item.get("status") != "pending":
        return False, "该项祭祀已非待祭状态，无法排期"

    want = "%04d-%02d-%02d" % (int(year), int(month), int(day))
    today = date_key(state.date)
    if want < today:
        return False, "行礼日不得早于今日"
    last = date_shift_key(state.date, max(0, int(item.get("remain_days", 30)) - 1))
    if want > last:
        return False, "行礼日超出 %s 日限期 最迟可定 %s" % (item.get("remain_days", 30), last)

    item["scheduled_date"] = {"year": int(year), "month": int(month), "day": int(day)}
    palace = RELIGION_PALACE.get(item.get("target_religion", ""), "内廷")
    return True, "已定行礼日 %s：%s %s，届期于%s行礼。" % (
        want, item.get("religion", ""), item.get("target_religion", ""), palace)


def clear_schedule(state: Any, sacrifice_id: str) -> Tuple[bool, str]:
    """撤销已定的行礼日 条目退回未排期状态。"""
    item = next((s for s in getattr(state, "sacrifices", []) if s.get("id") == sacrifice_id), None)
    if not item:
        return False, "未找到该祭祀条目"
    item.pop("scheduled_date", None)
    return True, "已撤销行礼日，该祭仍列待祭。"


def due_sacrifice(state: Any) -> Optional[Dict[str, Any]]:
    """当日应行礼的祭祀：已排期且行礼日不晚于今日，按 儒 > 道 > 佛 取最高优先的一条。"""
    from .time import date_key

    if state is None:
        return None
    today = date_key(state.date)
    cands = []
    for item in getattr(state, "sacrifices", []):
        if item.get("status") != "pending":
            continue
        k = _scheduled_key(item)
        if k and k <= today:
            cands.append(item)
    if not cands:
        return None

    def _rank(it):
        r = it.get("target_religion", "")
        return SACRIFICE_RELIGION_ORDER.index(r) if r in SACRIFICE_RELIGION_ORDER else 99

    cands.sort(key=_rank)
    item = cands[0]
    return {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "religion": item.get("religion", ""),
        "target_religion": item.get("target_religion", ""),
        "palace": RELIGION_PALACE.get(item.get("target_religion", ""), ""),
        "scheduled_date": _scheduled_key(item),
        "cost_wan": float(item.get("cost_wan", 0.0)),
        "remain_days": int(item.get("remain_days", 0)),
        "desc": item.get("desc", ""),
    }


def decline_sacrifice(state: Any, sacrifice_id: str) -> Tuple[bool, str]:
    """圣驾未往：等同逾期未祭 —— 该祭作废，并计入威望扣减。"""
    if state is None:
        return False, "系统状态异常"
    item = next((s for s in getattr(state, "sacrifices", []) if s.get("id") == sacrifice_id), None)
    if not item:
        return False, "未找到该祭祀条目"
    if item.get("status") != "pending":
        return False, "该项祭祀已非待祭状态"

    item["status"] = "expired"
    pen = float(item.get("prestige_penalty", 0.0))
    state.prestige_penalty = round(getattr(state, "prestige_penalty", 0.0) + pen, 4)
    return True, "圣驾未往，%s遂罢 等同逾期未祭，皇室威望 -%.3f。" % (item.get("religion", ""), pen)


def get_rites_overview(state: Any) -> Dict[str, Any]:
    """礼部汇总视图数据。"""
    curr_year = state.date["year"] if state and hasattr(state, "date") else 1627
    regular_sacs = calc_annual_sacrifices(curr_year)
    # 每项常祀给出「下一次」的日期：本年已过的，顺延到明年同项
    today = ""
    if state is not None:
        from .time import date_greg
        today = date_greg(state.date)
    next_year = {r["id"].rsplit("_", 1)[0]: r
                 for r in calc_annual_sacrifices(curr_year + 1)}
    for r in regular_sacs:
        key = r["id"].rsplit("_", 1)[0]
        if today and r.get("greg_date", "") < today and key in next_year:
            r["next_date"] = next_year[key]["greg_date"]
            r["next_date_str"] = next_year[key]["game_date_str"]
        else:
            r["next_date"] = r.get("greg_date", "")
            r["next_date_str"] = r.get("game_date_str", "")
    disaster_sacs = list(getattr(state, "sacrifices", [])) if state else []

    return {
        "countries": DIPLOMATIC_COUNTRIES,
        "trade": calc_trade_summary(state),
        "religion": calc_religion_summary(state),
        "prestige": calc_prestige(state),
        "education": calc_education_summary(state),
        "regular_sacrifices": regular_sacs,
        "disaster_sacrifices": disaster_sacs,
    }
