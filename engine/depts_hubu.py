"""户部专业模块：全国实际人口、阶级生产与需求统计、税收体系 十税制、国库与私库收支表。

功能规范：
1. 基本数据：
   - 全国实际人口 各省阶级人口求和，不含非明属省份。
   - 全国各阶级分别的人口总和、产能总和、需求总和打表 宗室、士绅、商人、农民、百工、百役、流民。
2. 十大税收体系 打表与动态征收：
   - 丁银：农民人头税，全国农民人数 × 修正，公历1月1日凌晨0点征收，按人头比例分摊到各省农民。
   - 坊厢银：市民人头税，全国商人+百工+百役人数 × 修正，公历1月1日凌晨0点征收，按人头比例分摊。
   - 班匠银：工匠人头税，全国百工人数 × 修正，公历1月1日凌晨0点征收，按人头比例分摊到各省百工。
   - 门摊银：商人人头税，全国商人人数 × 修正，公历1月1日凌晨0点征收，按人头比例分摊到各省商人。
   - 夏税：农业田赋，全国农民总产能 × 修正，公历6月1日凌晨0点征收，按产能比例分摊到各省农民。
   - 秋粮：农业田赋，全国农民总产能 × 修正，公历9月1日凌晨0点征收，按产能比例分摊到各省农民。
   - 盐课：盐税，全国人口总数 × 修正，公历1月1日凌晨0点征收，按人头比例分摊到各省。
   - 钞关税：内地贸易税，全国商人总产能 × 修正，公历每月1日凌晨0点征收，按产能比例分摊到各省商人。
   - 番舶抽分：国际贸易税，留空 待贸易系统接入，公历每月1日凌晨0点征收。
   - 杂税：全国物理总产能 × 修正，公历每月1日凌晨0点征收，按产能比例分摊到各省生产阶级。
   - 征收规则：按地方银库(local_reserve)与民间流通现银(M1)的实时比例等比抽取解缴国库。
   - 减产修正：同兵部粮饷筹措规则，每分摊1万两税负扣减对应阶级 1.0% (0.01) 产能。
3. 国库与私库收支表：
   - 国库收支表 国库：十税折算收入、辽饷加派解库、银矿课税 vs 宗禄、百官俸禄、军饷、驿站、工部工程、水利、军械制造、朝廷月费。
   - 私库收支表 私库/私库：金矿采金、皇庄例解、朝贡 vs 宫廷用度、二十四衙门杂费、御前赏赐、发帑助饷。
"""

from typing import Any, Dict, List, Optional, Tuple
import json
import os

from .localdata import class_pop_exact
from .economy import (
    PROD_WEIGHTS, DEMAND_WEIGHTS, CLASSES_ORDER,
    class_capacity, class_production, class_demand, class_capacity_base,
    local_production, local_base_production, local_demand
)

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXPENSES_FILE = os.path.join(_BASE, "world", "department_expenses.json")

# ── 十大税收基础配置 ──
TAX_DEFINITIONS = [
    {
        "id": "dingyin",
        "name": "丁银",
        "category": "人头税",
        "desc": "法理上只针对成年农村男性的人头税，但实际上征收范围已经无限扩大。",
        "target_type": "headcount",
        "target_classes": ["农民"],
        "schedule": "annual_jan1",
        "schedule_desc": "公历年初1月1日凌晨0点",
        "rate": 0.6,  # 约 170-180 万两/年
        "rate_display": 6,
        "calc_desc": "全国农民阶层总人数 × 0.1 × 税目修正(6) 按人头比例分摊",
        "calc_formula": "全国农民阶层总人数 × 0.1 × 税目修正",
        "mechanism": "按全国农民阶层人数起征，人越多丁银越多；户口清查与蠲免会直接改动它。",
    },
    {
        "id": "fangxiang",
        "name": "坊厢银",
        "category": "人头税",
        "desc": "市民人头税，课于城坊厢市商工杂役人户。",
        "target_type": "headcount",
        "target_classes": ["商人", "百工", "百役"],
        "schedule": "annual_jan1",
        "schedule_desc": "公历年初1月1日凌晨0点",
        "rate": 0.7,  # 约 30-35 万两/年
        "rate_display": 7,
        "calc_desc": "全国商人+百工+百役阶层总人数 × 0.1 × 税目修正(7) 按人头比例分摊",
        "calc_formula": "全国商人+百工+百役阶层总人数 × 0.1 × 税目修正",
        "mechanism": "按商人、百工、百役三阶层人数起征，城坊繁盛则多，坊厢凋敝或减免则少。",
    },
    {
        "id": "banjiang",
        "name": "班匠银",
        "category": "人头税",
        "desc": "工匠人头税，轮班工匠不赴京服役以银代役之征。",
        "target_type": "headcount",
        "target_classes": ["百工"],
        "schedule": "annual_jan1",
        "schedule_desc": "公历年初1月1日凌晨0点",
        "rate": 1.0,  # 约 15-20 万两/年
        "rate_display": 10,
        "calc_desc": "全国百工阶层总人数 × 0.1 × 税目修正(10) 按人头比例分摊",
        "calc_formula": "全国百工阶层总人数 × 0.1 × 税目修正",
        "mechanism": "按百工阶层人数起征，匠籍越众则多；匠籍改革与放免会削减它。",
    },
    {
        "id": "mentan",
        "name": "门摊银",
        "category": "人头税",
        "desc": "商人人头税，行商坐贾设摊开铺之人头例税。",
        "target_type": "headcount",
        "target_classes": ["商人"],
        "schedule": "annual_jan1",
        "schedule_desc": "公历年初1月1日凌晨0点",
        "rate": 1.3,  # 约 20-25 万两/年
        "rate_display": 13,
        "calc_desc": "全国商人阶层总人数 × 0.1 × 税目修正(13) 按人头比例分摊",
        "calc_formula": "全国商人阶层总人数 × 0.1 × 税目修正",
        "mechanism": "按城乡商户分摊，商税征管的松紧与铺户增减直接决定它。",
    },
    {
        "id": "xiashui",
        "name": "夏税",
        "category": "田赋",
        "desc": "两税法之夏税，以麦、绢、折色银为主，课于夏熟之季。",
        "target_type": "capacity",
        "target_classes": ["农民"],
        "schedule": "annual_jun1",
        "schedule_desc": "公历6月1日凌晨0点",
        "rate": 0.4,  # 约 190-210 万两/年
        "rate_display": 4,
        "calc_desc": "全国农民阶层总产能 × 0.1 × 税目修正(4) 按农民产能比例分摊",
        "calc_formula": "全国农民阶层总产能 × 0.1 × 税目修正",
        "mechanism": "以农民阶层人数为基数按夏征收，年成丰歉与蠲缓会改动它。",
    },
    {
        "id": "qiuliang",
        "name": "秋粮",
        "category": "田赋",
        "desc": "两税法之秋粮，大明正赋核心，以秋收稻谷米麦折银入库。",
        "target_type": "capacity",
        "target_classes": ["农民"],
        "schedule": "annual_sep1",
        "schedule_desc": "公历9月1日凌晨0点",
        "rate": 0.6,  # 约 250-270 万两/年
        "rate_display": 6,
        "calc_desc": "全国农民阶层总产能 × 0.1 × 税目修正(6) 按农民产能比例分摊",
        "calc_formula": "全国农民阶层总产能 × 0.1 × 税目修正",
        "mechanism": "以农民阶层产能为基数按秋征收，收成好坏与漕运是否通畅决定实收。",
    },
    {
        "id": "yanke",
        "name": "盐课",
        "category": "盐税",
        "desc": "天下盐引例课与场灶折银，国家专卖盐利之征。",
        "target_type": "headcount",
        "target_classes": ["all"],
        "schedule": "annual_jan1",
        "schedule_desc": "公历年初1月1日凌晨0点",
        "rate": 0.3,  # 约 140-150 万两/年
        "rate_display": 3,
        "calc_desc": "全国人口总数 × 0.1 × 税目修正(3) 按人口比例分摊",
        "calc_formula": "全国人口总数 × 0.1 × 税目修正",
        "mechanism": "按盐场产量与引额征收，盐政败坏、私盐泛滥会压低实收。",
    },
    {
        "id": "chaoguan",
        "name": "钞关税",
        "category": "关税",
        "desc": "内地水陆通商钞关 运河七大钞关等货物过载贸易税。",
        "target_type": "capacity",
        "target_classes": ["商人"],
        "schedule": "monthly_1st",
        "schedule_desc": "公历每月1日凌晨0点",
        "rate": 0.02,  # 税目修正 0.20 0.65 × 0.3 = 0.195，保留两位小数
        "rate_display": 0.2,
        "calc_desc": "全国商人阶层总产能 × 0.1 × 税目修正(0.2) 按商人产能比例分摊",
        "calc_formula": "全国商人阶层总产能 × 0.1 × 税目修正",
        "mechanism": "按水陆通商关口的货运量征收，商路通塞与战事封锁直接改动它。",
    },
    {
        "id": "fanbo",
        "name": "番舶抽分",
        "category": "关税",
        "desc": "市舶司与沿海口岸外国商船进出口贸易抽分税 留空，待贸易系统接入。",
        "target_type": "capacity",
        "target_classes": ["商人"],
        "schedule": "monthly_1st",
        "schedule_desc": "公历每月1日凌晨0点",
        "rate": 0.0,  # 留空
        "rate_display": 0.0,
        "calc_desc": "留空 后续与贸易系统一并接入",
        "calc_formula": "留空 待贸易系统接入",
        "mechanism": "按市舶司抽解番货征收，海贸开放则多，海禁与海寇则少。",
    },
    {
        "id": "zashui",
        "name": "杂税",
        "category": "杂税",
        "desc": "契税、芦课、牙税、鱼课、坑冶杂课等各项碎杂名目税。",
        "target_type": "capacity",
        "target_classes": ["all_physical"],
        "schedule": "monthly_1st",
        "schedule_desc": "公历每月1日凌晨0点",
        "rate": 0.008,  # 约 3.8-4.2 万两/月 = ~48 万两/年
        "rate_display": 0.08,
        "calc_desc": "全国物理总产能 × 0.1 × 税目修正(0.08) 按产出比例分摊",
        "calc_formula": "全国物理总产能 × 0.1 × 税目修正",
        "mechanism": "各项杂项课税合计，地方征派与朝廷加派都会推高它。",
    },
    {
        "id": "juanna",
        "name": "捐纳得官",
        "category": "捐纳",
        "desc": "捐资得纳散官、职衔、监生与出身，朝廷因国用匮乏而设之捐纳规银。",
        "target_type": "headcount",
        "target_classes": ["士绅"],
        "schedule": "annual_jan1",
        "schedule_desc": "公历年初1月1日凌晨0点",
        "rate": 1.5,  # 修正 50  即 50 * 0.001 = 0.050
        "rate_display": 15,
        "calc_desc": "全国官员阶层总人数 × 0.1 × 税目修正(15) 按官员/士绅人头比例分摊",
        "calc_formula": "全国官员阶层总人数 × 0.1 × 税目修正",
        "mechanism": "遇军兴、河工、赈灾时开捐，国库宽裕或明令停捐则无此项。",
    },
    {
        # 辽饷本指是税目：界面在兵部，账目与分摊走户部同一套税制。
        # 税率反算自开局税基：26201.28 万单位 × 0.0015902531 = 41.67 万两/月 ≈ 500 万两/年，
        # 与并入税目之前 LIAOXIANG_CONFIG 的 41.67 / 500.0 一致。
        "id": "liaoxiang",
        "name": "辽饷加派",
        "category": "加派",
        "desc": "万历末年因辽东战事加派于天下的田赋，天启年间屡增不减，名为军饷，实为民赋。",
        "target_type": "capacity",
        "target_classes": ["all_physical"],
        "schedule": "monthly_1st",
        "schedule_desc": "公历每月1日凌晨0点",
        "rate": 0.0015902531,
        "rate_display": 0.015902531,
        "calc_desc": "全国物理总产能 × 0.1 × 税目修正(0.0159) 按产能比例分摊",
        "calc_formula": "全国物理总产能 × 0.1 × 税目修正",
        "mechanism": "辽东战事一日不停，辽饷一日不减；税额随天下物理产能水涨船高，只在诏令增减时变动，不随年成蠲免。",
    },
]

ALL_CLASSES = ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]
PHYSICAL_CLASSES = ["宗室", "士绅", "农民", "百工", "百役", "流民"]

CLASS_DESCS = {
    "宗室": "天潢贵胄，不事四民之业，坐食朝廷岁禄，人均物质消费极奢",
    "士绅": "官僚缙绅与读书士人，享有优免特权，兼并田产并主导乡土消费",
    "商人": "通商贾利行销四方，自身具虚拟买卖产能，资金周转主导商税",
    "农民": "天下根本，承担夏秋两税田赋与徭役丁粮，产出帝国主力粮食",
    "百工": "手工业匠人，工部织染军器各局应役，产出兵仗器用百货",
    "百役": "公差皂隶、水夫驿卒、狱卒巡警，承担官府基层杂役劳务",
    "流民": "离土失业之饥民游勇，无稳定产能，极易沦为流贼转徙四方",
}


def get_ming_provinces(state) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """获取所有**当前属明朝控制**的省份 (pid, prov_dict, local_dict)。

    只看控制权 local.controller ＝ 明，与核心 local.core无关 ——
    判定在 engine/localdata.py 一处，本函数不再自带一套过滤。
    """
    from .localdata import under_ming_control
    res = []
    for pid, p in state.provinces.items():
        if under_ming_control(p):
            res.append((pid, p, p.get("local") or {}))
    return res


def calc_hubu_basic_stats(state) -> Dict[str, Any]:
    """统计全国实际人口及各阶级人口、基础产能 税基、需求总和 打表。

    此处产能口径为**基础产能** 人口 × 基础权重 ×  1+增益 + 固定增益，
    不含辽饷/税负/欠饷等扣减：税是按基础生产力摊派的，若用扣减后的产能作税基，
    税负会反过来缩小税基，形成"税越重→税基越小→税越轻"的自我循环。
    各省扣除各项修正后的实际产能由 engine/modifiers.py 实时汇总给出。
    """
    ming_provs = get_ming_provinces(state)

    class_pops = {c: 0.0 for c in ALL_CLASSES}
    class_pops_exact = {c: 0 for c in ALL_CLASSES}
    class_caps = {c: 0.0 for c in ALL_CLASSES}
    class_dems = {c: 0.0 for c in ALL_CLASSES}

    for pid, p, loc in ming_provs:
        cls_map = loc.get("classes") or {}
        for c in ALL_CLASSES:
            c_data = cls_map.get(c) or {}
            c_exact = class_pop_exact(c_data) if c_data else 0
            class_pops_exact[c] += c_exact
            pop = float(c_data.get("pop") or (c_exact / 10000.0))
            class_pops[c] += pop
            cap = class_capacity_base(c, pop, loc)
            class_caps[c] += cap
            dem = class_demand(c, pop, float(loc.get("modifier_demand_pct") or 0.0))
            class_dems[c] += dem

    total_actual_pop_exact = sum(class_pops_exact.values())
    total_actual_pop = round(total_actual_pop_exact / 10000.0, 4)
    total_phys_cap = round(sum(class_caps[c] for c in PHYSICAL_CLASSES), 2)
    total_merchant_cap = round(class_caps["商人"], 2)
    total_demand = round(sum(class_dems.values()), 2)

    classes_table = []
    for c in ALL_CLASSES:
        p_pop = round(class_pops_exact[c] / 10000.0, 4)
        p_cap = round(class_caps[c], 2)
        p_dem = round(class_dems[c], 2)
        pop_pct = round((class_pops_exact[c] / total_actual_pop_exact * 100) if total_actual_pop_exact > 0 else 0.0, 2)
        per_cap_prod = round(PROD_WEIGHTS.get(c, 0.0), 2)
        per_cap_dem = round(DEMAND_WEIGHTS.get(c, 0.0), 2)

        classes_table.append({
            "class_name": c,
            "population": p_pop,
            "population_exact": class_pops_exact[c],
            "pop_ratio": pop_pct,
            "capacity": p_cap,
            "demand": p_dem,
            "per_capita_prod": per_cap_prod,
            "per_capita_demand": per_cap_dem,
            "desc": CLASS_DESCS.get(c, ""),
        })

    return {
        "total_actual_pop": total_actual_pop,
        "total_actual_pop_exact": total_actual_pop_exact,
        "total_physical_capacity": total_phys_cap,
        "total_merchant_capacity": total_merchant_cap,
        "total_demand": total_demand,
        "supply_demand_ratio": round(total_phys_cap / max(1e-5, total_demand), 4),
        "classes_table": classes_table,
        "class_pops": class_pops,
        "class_caps": class_caps,
        "class_dems": class_dems,
    }


def calc_hubu_taxes(state) -> Dict[str, Any]:
    """计算十大税收预估额、征收明细及分摊减产惩罚。"""
    stats = calc_hubu_basic_stats(state)
    c_pops = stats["class_pops"]
    c_caps = stats["class_caps"]
    tot_pop = stats["total_actual_pop"]
    tot_phys_cap = stats["total_physical_capacity"]

    ming_provs = get_ming_provinces(state)

    # 银 → 物质：1 两白银 = 1 单位物质，再依物价指数 P_t 折算 与修正系统同一把尺子
    from .economy import calc_price_index, LIANG_TO_MATTER_RATIO
    matter_coef = LIANG_TO_MATTER_RATIO / max(1e-6, calc_price_index(state))

    tax_items = []
    total_annual_revenue = 0.0
    total_monthly_revenue = 0.0

    # 记录各省各阶级每月承担的税负 万两/月
    prov_class_monthly_burden: Dict[str, Dict[str, float]] = {
        pid: {c: 0.0 for c in ALL_CLASSES} for pid, _, _ in ming_provs
    }
    # 税目 × 省 × 阶级 的月承担额 万两/月：修正系统按税目逐笔折算物质扣减
    per_tax_class_burden: Dict[str, Dict[str, Dict[str, float]]] = {}

    def _accrue_tax(tid_: str, pid_: str, cls_: str, amount_: float) -> None:
        """累加一笔税额：既进"省×阶级"合计，也单独按税目记账 供逐笔折物质用。"""
        prov_class_monthly_burden[pid_][cls_] += amount_
        by_tax = per_tax_class_burden.setdefault(tid_, {}).setdefault(pid_, {})
        by_tax[cls_] = by_tax.get(cls_, 0.0) + amount_

    for tdef in TAX_DEFINITIONS:
        tid = tdef["id"]
        rate = float(tdef["rate"])
        ttype = tdef["target_type"]
        sched = tdef["schedule"]
        tclasses = tdef["target_classes"]

        # 确定基数
        if tid == "dingyin":
            base_val = c_pops.get ("农民", 0.0)
        elif tid == "fangxiang":
            base_val = c_pops.get ("商人", 0.0 + c_pops.get ("百工", 0.0 + c_pops.get ("百役", 0.0)))
        elif tid == "banjiang":
            base_val = c_pops.get ("百工", 0.0)
        elif tid == "mentan":
            base_val = c_pops.get ("商人", 0.0)
        elif tid in ("xiashui", "qiuliang"):
            base_val = c_caps.get ("农民", 0.0)
        elif tid == "yanke":
            base_val = tot_pop
        elif tid == "chaoguan":
            base_val = c_caps.get ("商人", 0.0)
        elif tid == "fanbo":
            base_val = 0.0
        elif tid in ("zashui", "liaoxiang"):
            # 辽饷与杂税同基：全国物理总产能；分摊比例即各省基础物理产能占比，
            # 与并入税目之前 calc_liaoxiang_apportionment 的比例口径完全一致。
            base_val = tot_phys_cap
        elif tid == "juanna":
            base_val = c_pops.get ("士绅", 0.0)
        else:
            base_val = 0.0

        if sched.startswith("annual"):
            annual_amt = round(base_val * rate, 2)
            monthly_equiv = round(annual_amt / 12.0, 2)
        else:
            monthly_equiv = round(base_val * rate, 2)
            # 年额用未取整的月额算：免得出 41.67 x 12 = 500.04 这种假差
            annual_amt = round(base_val * rate * 12.0, 2)

        total_annual_revenue += annual_amt
        total_monthly_revenue += monthly_equiv

        # 分摊到各省与各阶级
        if monthly_equiv > 0 and base_val > 0:
            for pid, p, loc in ming_provs:
                cls_map = loc.get("classes") or {}
                if ttype == "headcount":
                    if tclasses == ["all"]:
                        p_sub_base = sum(float((cls_map.get(c) or {}).get("pop", 0.0)) for c in ALL_CLASSES)
                        prov_tax = monthly_equiv * (p_sub_base / base_val)
                        # 按人口比例分给各阶级
                        for c in ALL_CLASSES:
                            c_p = float((cls_map.get(c) or {}).get("pop", 0.0))
                            if p_sub_base > 0:
                                _accrue_tax(tid, pid, c, prov_tax * (c_p / p_sub_base))
                    else:
                        p_sub_base = sum(float((cls_map.get(c) or {}).get("pop", 0.0)) for c in tclasses)
                        prov_tax = monthly_equiv * (p_sub_base / base_val)
                        for c in tclasses:
                            c_p = float((cls_map.get(c) or {}).get("pop", 0.0))
                            if p_sub_base > 0:
                                _accrue_tax(tid, pid, c, prov_tax * (c_p / p_sub_base))
                else:  # capacity 按基础产能比例分摊，见 calc_hubu_basic_stats 口径说明
                    if tclasses == ["all_physical"]:
                        p_sub_base = sum(class_capacity_base(c, float((cls_map.get(c) or {}).get("pop", 0.0)), loc)
                                         for c in PHYSICAL_CLASSES)
                        prov_tax = monthly_equiv * (p_sub_base / base_val)
                        for c in PHYSICAL_CLASSES:
                            c_cap = class_capacity_base(c, float((cls_map.get(c) or {}).get("pop", 0.0)), loc)
                            if p_sub_base > 0:
                                _accrue_tax(tid, pid, c, prov_tax * (c_cap / p_sub_base))
                    else:
                        p_sub_base = sum(class_capacity_base(c, float((cls_map.get(c) or {}).get("pop", 0.0)), loc)
                                         for c in tclasses)
                        prov_tax = monthly_equiv * (p_sub_base / base_val)
                        for c in tclasses:
                            c_cap = class_capacity_base(c, float((cls_map.get(c) or {}).get("pop", 0.0)), loc)
                            if p_sub_base > 0:
                                _accrue_tax(tid, pid, c, prov_tax * (c_cap / p_sub_base))

        # 单项税目在各省的分摊明细 用于前端展开/收起
        prov_apportionment = []
        for pid, p, loc in ming_provs:
            p_name = p.get("name", pid)
            cls_map = loc.get("classes") or {}

            if ttype == "headcount":
                if tclasses == ["all"]:
                    p_sub_base = sum(float((cls_map.get(c) or {}).get("pop", 0.0)) for c in ALL_CLASSES)
                    target_cls_desc = "全阶级"
                else:
                    p_sub_base = sum(float((cls_map.get(c) or {}).get("pop", 0.0)) for c in tclasses)
                    target_cls_desc = "/".join(tclasses)
            else:
                if tclasses == ["all_physical"]:
                    p_sub_base = sum(class_capacity_base(c, float((cls_map.get(c) or {}).get("pop", 0.0)), loc) for c in PHYSICAL_CLASSES)
                    target_cls_desc = "商/农/工/役"
                else:
                    p_sub_base = sum(class_capacity_base(c, float((cls_map.get(c) or {}).get("pop", 0.0)), loc) for c in tclasses)
                    target_cls_desc = "/".join(tclasses)

            p_ratio = (p_sub_base / base_val) if base_val > 0 else 0.0
            p_monthly_wan = round(monthly_equiv * p_ratio, 2)
            p_annual_wan = round(annual_amt * p_ratio, 1)
            # 地区修正 = 该省本税目月摊银额折成的物质产能扣减 1两白银=1单位物质 ÷ 物价指数
            p_matter_loss = round(p_monthly_wan * matter_coef, 2)
            p_penalty_str = (f"折物质 -{p_matter_loss:.2f} 万单位/月 ({target_cls_desc})"
                             if p_matter_loss > 0 else "无减产")

            prov_apportionment.append({
                "province_id": pid,
                "province_name": p_name,
                "base_val": round(p_sub_base, 1 if ttype == "headcount" else 2),
                "ratio": round(p_ratio, 4),
                "ratio_pct": f"{p_ratio * 100:.1f}%",
                "monthly_tax": p_monthly_wan,
                "annual_tax": p_annual_wan,
                "affected_classes": target_cls_desc,
                # 本税目在本省由各阶级分别承担的月额 万两/月
                "class_burden": {c: round(per_tax_class_burden.get(tid, {}).get(pid, {}).get(c, 0.0), 4)
                                 for c in ALL_CLASSES},
                "matter_loss": p_matter_loss,
                "penalty_desc": p_penalty_str,
                "modifier_text": (f"{tdef['name']}分摊 {p_monthly_wan:.2f}万两/月 → 地区修正 "
                                  f"折物质 -{p_matter_loss:.2f} 万单位/月 ({target_cls_desc})"
                                  if p_matter_loss > 0 else f"{tdef['name']}暂无分摊修正"),
            })

        tax_items.append({
            # 税目ID 与名称都给：AI 下超控指令要能指名道姓地引用税目
            "id": tid,
            "name": tdef["name"],
            "category": tdef["category"],
            "desc": tdef["desc"],
            "calc_desc": tdef["calc_desc"],
            "calc_formula": tdef.get("calc_formula", tdef["calc_desc"]),
            "mechanism": tdef["mechanism"],
            "schedule_desc": tdef["schedule_desc"],
            "target_type": "人头税  按人头比例分摊" if ttype == "headcount" else "产能税  按产能比例分摊",
            "rate": rate,
            "rate_display": tdef.get("rate_display", rate),
            "base_val": round(base_val, 2),
            "monthly_amount": monthly_equiv,
            "annual_amount": annual_amt,
            "prov_apportionment": prov_apportionment,
        })

    # 各省税负与减产汇总打表
    prov_breakdown = []
    for pid, p, loc in ming_provs:
        p_name = p.get("name", pid)
        cls_map = loc.get("classes") or {}
        p_pop = sum(float(c.get("pop", 0)) for c in cls_map.values())
        p_prod = local_base_production(loc)   # 基础产能 税基口径

        p_monthly_tax = sum(prov_class_monthly_burden[pid].values())
        p_annual_tax = p_monthly_tax * 12.0

        # 【v0.3.7】旧的"每万两扣 1% 产能"percentages 已删：
        # 税负实际扣的是物质绝对量 税额 × 1两=1单位 ÷ 物价指数，由 modifiers 实时折算；
        # 两份口径并存过，界面早已改用物质口径，这个 penalties 没有任何消费者。
        penalty_strs = [f"{c}-{prov_class_monthly_burden[pid][c]:.2f}万两"
                        for c in ALL_CLASSES if prov_class_monthly_burden[pid][c] > 0]

        # 税负修正不写入省份副本：户部账册即真相源，修正系统实时读取本函数结果

        prov_breakdown.append({
            "province_id": pid,
            "province_name": p_name,
            "population": round(p_pop, 1),
            "production": round(p_prod, 2),
            "monthly_tax": round(p_monthly_tax, 2),
            "annual_tax": round(p_annual_tax, 1),
            # 各阶级每月承担的税额 万两/月：修正系统据此实时折算物质产能扣减
            "class_tax_burdens": {c: round(prov_class_monthly_burden[pid][c], 4) for c in ALL_CLASSES},
            "penalty_desc": ("税负 " + "、".join(penalty_strs)) if penalty_strs else "无显著税负",
        })

    # 辽饷月额写回 state 作为派生缓存：真相源是税目 liaoxiang，
    # 经济结算与各部分读取点仍可照旧读 state.liaoxiang_monthly，但它只是这份税目的影子。
    if state is not None:
        for _it in tax_items:
            if _it["id"] == "liaoxiang":
                state.liaoxiang_monthly = float(_it["monthly_amount"])
                break

    return {
        "taxes": tax_items,
        "total_annual_revenue": round(total_annual_revenue, 2),
        "total_monthly_revenue": round(total_monthly_revenue, 2),
        "prov_breakdown": prov_breakdown,
        "prov_burdens": prov_class_monthly_burden,
    }


def department_expense_rows() -> Dict[str, Any]:
    """六部、外诸司、二十四衙门各部门的年费支出 万两/年。

    部门名册取引擎里的三套名册本身 depts.SIX_DEPTS / depts_waizhusi.WAIDEPS /
    depts_neiting.DEPTS，金额写在 world/department_expenses.json 改数值不碰代码。
    名册里有、数据里没写金额的部门会报错，不许静默算 0。
    """
    from .depts import SIX_DEPTS
    from .depts_waizhusi import WAIDEPS
    from .depts_neiting import DEPTS as NEITING_DEPTS

    with open(_EXPENSES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    groups = [
        ("六部", list(SIX_DEPTS)),
        ("外诸司", list(WAIDEPS.keys())),
        ("二十四衙门", list(NEITING_DEPTS.keys())),
    ]
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    subtotals: Dict[str, float] = {}
    total_annual = 0.0
    for gname, names in groups:
        fee_map = data.get(gname)
        if not isinstance(fee_map, dict):
            raise KeyError ("world/department_expenses.json 缺分组：%s" % gname)
        sub = 0.0
        for n in names:
            if n not in fee_map:
                # 超控新建的部门还没有年费记录：如实记为「未登记」，界面不显示该栏，
                # 也不给它编一个假的 0。原先这里直接抛 KeyError，建完部门户部整页就崩。
                missing.append("%s（%s）" % (n, gname))
                continue
            annual = round(float(fee_map[n]), 2)
            sub += annual
            rows.append({
                "name": n,
                "group": gname,
                "item_name": gname,
                "timestamp": "公历每月摊支",
                "monthly": round(annual / 12.0, 4),
                "annual": annual,
            })
        subtotals[gname] = round(sub, 2)
        total_annual += sub
    return {
        "rows": rows,
        "missing": missing,          # 已建部门但年费表里没登记金额的，界面上如实标「未登记」
        "subtotals": subtotals,
        "total_annual": round(total_annual, 2),
        "total_monthly": round(total_annual / 12.0, 4),
    }


def department_names() -> List[str]:
    """三套部门名册里的全部部门名 六部 + 外诸司 + 二十四衙门。

    宫殿、城门这类设施不是部门，不在这张表上——它们没有年费这个属性。
    """
    from .depts import SIX_DEPTS
    from .depts_waizhusi import WAIDEPS
    from .depts_neiting import DEPTS as NEITING_DEPTS
    return list(SIX_DEPTS) + list(WAIDEPS.keys()) + list(NEITING_DEPTS.keys())


def department_expense_of(name: str) -> Optional[Dict[str, Any]]:
    """某个部门自己的年费 部门属性，随 /api/dept 一起给出。

    - 不是部门（宫殿、城门等设施）：返回 None，它们本就没有年费这个属性；
    - 是部门但年费表里还没登记（超控新建的部门）：返回 None，界面不显示该栏，
      也不编一个假的 0；未登记的名单由 `department_expense_rows()["missing"]` 如实给出。
    """
    if name not in department_names():
        return None
    data = department_expense_rows()
    for row in data["rows"]:
        if row["name"] == name:
            return {"annual": row["annual"], "monthly": row["monthly"],
                    "group": row["group"]}
    return None


def calc_treasury_balance_sheets(state) -> Dict[str, Any]:
    """生成国库收支表与私库收支表。"""
    tax_info = calc_hubu_taxes(state)
    basic_info = calc_hubu_basic_stats(state)

    # 1. ── 国库收支表 国库──
    # 收入项
    inc_items = []
    for t in tax_info["taxes"]:
        # 辽饷是税目、界面在兵部：账只记这一次，文案按其身份写「解库／军费加派」
        _is_lx = t["id"] == LIAOXIANG_TAX_ID
        inc_items.append({
            "name": "辽饷加派解库" if _is_lx else t["name"],
            "item_name": "军费加派" if _is_lx else ("国家常赋 " + t["category"]),
            "timestamp": t["schedule_desc"],
            "monthly": t["monthly_amount"],
            "annual": t["annual_amount"],
        })

    # 兵部辽饷加派解库：**不在这里另起一行** —— 辽饷已是税目 liaoxiang，
    # 上面的税目循环已经按同一份月额给它记过账了；此处再记一次就是同一个东西两套口径。
    # 界面文案由税目循环里那一处按其身份给出（见上 loop 内的 辽饷 分支）。

    # 地方官办银矿课税 【v0.3.4】按各省工程的 buff.silver 年额 ÷ 12 现算，不再写死 4.58
    from .localdata import under_ming_control
    mining_monthly = 0.0
    for _p in state.provinces.values():
        if not under_ming_control(_p):
            continue
        for _pj in ((_p.get("local") or {}).get("projects") or []):
            if str(_pj.get("status") or "运营中") != "运营中":
                continue
            from .projects import scale as _proj_scale
            mining_monthly += float((_pj.get("buff") or {}).get("silver") or 0.0) * _proj_scale(_pj) / 12.0
    mining_monthly = round(mining_monthly, 2)
    inc_items.append({
        "name": "官办银矿课税",
        "item_name": "坑冶杂课",
        "timestamp": "每月常例",
        "monthly": mining_monthly,
        "annual": round(mining_monthly * 12, 1),
    })

    tot_nat_income_monthly = round(sum(i["monthly"] for i in inc_items), 2)
    tot_nat_income_annual = round(sum(i["annual"] for i in inc_items), 2)
    national_ledger = list(getattr(state, "national_ledger", []))

    # 支出项：六部、外诸司、二十四衙门各部门年费 数据见 world/department_expenses.json
    dep = department_expense_rows()
    exp_items = list(dep["rows"])

    national_treasury = {
        "balance": float(getattr(state, "treasury", 240.0)),
        "incomes": inc_items,
        "expenses": exp_items,
        "transactions": national_ledger,
        "total_monthly_income": tot_nat_income_monthly,
        "total_annual_income": tot_nat_income_annual,
        "total_monthly_expense": dep["total_monthly"],
        "total_annual_expense": dep["total_annual"],
        "expense_subtotals": dep["subtotals"],
    }

    # 2. ── 私库 私库 / 私库 仅账单交易，无虚构常例──
    privy_silver = float(getattr(state, "privy_treasury", 120.0))
    privy_gold = float(getattr(state, "gold_treasury", 100.0))
    privy_ledger = list(getattr(state, "privy_ledger", []))

    privy_treasury = {
        "silver_balance": privy_silver,
        "gold_balance": privy_gold,
        "transactions": privy_ledger,
    }

    return {
        "national_treasury": national_treasury,
        "privy_treasury": privy_treasury,
    }


LIAOXIANG_TAX_ID = "liaoxiang"


def liaoxiang_tax_row(state) -> Optional[Dict[str, Any]]:
    """辽饷税目行 唯一取用处：国库收入行与兵部粮饷筹措都从这里拿，不另算一份。"""
    for t in calc_hubu_taxes(state)["taxes"]:
        if t["id"] == LIAOXIANG_TAX_ID:
            return t
    return None


def find_tax_index(key: str) -> int:
    """按税目ID或税名找 TAX_DEFINITIONS 里的下标；找不到返回 -1。"""
    for i, t in enumerate(TAX_DEFINITIONS):
        if key in (t.get("id"), t.get("name")):
            return i
    return -1


def modify_tax_rate(state, key: str, rate_display: float) -> Tuple[bool, str]:
    """改一个税目的「税目修正」 唯一一处。

    口径：税目修正 rate_display 与算式用的 rate 是同一个数的两种写法，rate = rate_display × 0.1
    （见 calc_hubu_taxes：月额 = 税基 × rate）。key 可给税目ID或税名；
    改的是 TAX_DEFINITIONS 里那一条，月额由下一次 calc_hubu_taxes 现算，不另存一份。
    """
    i = find_tax_index(key)
    if i < 0:
        return False, "户部税则里没有税目【%s】。" % key
    t = TAX_DEFINITIONS[i]
    if rate_display <= 0:
        return False, "税目修正必须大于 0：%r" % rate_display
    t["rate_display"] = float(rate_display)
    t["rate"] = float(rate_display) * 0.1
    return True, "户部赋税体系：税目【%s】的税目修正已改为 %g。" % (t["name"], float(rate_display))


def get_hubu_overview(state) -> Dict[str, Any]:
    """户部前端总览数据打包入口。"""
    basic = calc_hubu_basic_stats(state)
    taxes = calc_hubu_taxes(state)
    sheets = calc_treasury_balance_sheets(state)

    from .modifiers import department_modifier_rows
    return {
        "basic_stats": basic,
        "modifier_rows": department_modifier_rows(state, "户部"),
        "tax_system": {
            "taxes": taxes["taxes"],
            "total_annual_revenue": taxes["total_annual_revenue"],
            "total_monthly_revenue": taxes["total_monthly_revenue"],
            "prov_breakdown": taxes["prov_breakdown"],
        },
        "national_treasury": sheets["national_treasury"],
        "privy_treasury": sheets["privy_treasury"],
    }
