"""地方数据：结构约定 + 派生值计算 唯一真相源。

地方数据结构 state.provinces[pid].local，按玩家预案落地：

  core              核心情况 国家认同的统称：核心 / 宣称 / 占领 / 非核
  corruption        ((腐败 0-100 玩家给定；corruption_note 存主要原因文本))
  classes           地方阶级 宗室/士绅/商人/农民/百工/百役/流民，各阶级：
                    {pop 人口, attitude 态度,
                     stock 物质库存 万单位, output 产出 万两/月-兼容,
                     tax_rate 税收比例, support 扶持 两/月}
  infra             基建程度 0-100
  army              军队 机动部队：{}
  projects          地方工程 [{name, kind, scope, status, buff}]

  注：镇军 人数 / 发饷比例 / 军饷需求不在这里 —— 军队数据只有一份，
      全在 world/armies.json 见 engine/armies.py，本模块不再存副本。
  carrying          ((可承载人口))
  weather           天气
  local_market_stock 本地市场未售物资 万单位物质，月末清零

派生值 不存档，统一在此计算：
  population(loc)           地方人口 = 各阶级人口总和
  material_production(loc)  本地物质实际产出总量 万单位/月，不含商人
  material_demand(loc)      本地物质总需求量 万单位/月
  supply_demand_ratio(loc)  地方供需指数 = 物质产出 / 物质总需求
  class_material_stock(loc) 本地阶级库存总和 万单位
    support_total(loc)        扶持合计 = Σ 扶持 两/月
"""

from typing import Any, Dict, Optional
from .economy import (
    PROD_WEIGHTS, DEMAND_WEIGHTS, CLASSES_ORDER,
    class_capacity, class_capacity_base, class_production, class_demand,
    local_production, local_base_production, local_demand, supply_demand_index
)

CORE_ORDER = {"核心": 0, "宣称": 1, "占领": 2, "非核": 3}
CLASSES = ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]


def class_pop_exact(c: Dict[str, Any]) -> int:
    """获取阶级人口精确个位数 人。"""
    if "pop_exact" in c:
        return int(c["pop_exact"])
    pop_val = float(c.get("pop", 0.0))
    if pop_val < 50000:
        return int(round(pop_val * 10000))
    return int(round(pop_val))


def population_exact(loc: Dict[str, Any]) -> int:
    """地方总人口精确到个位数 人。"""
    cls = loc.get("classes") or {}
    return sum(class_pop_exact(c) for c in cls.values())


def population(loc: Dict[str, Any]) -> float:
    """地方人口：各阶级人口总和 万人。"""
    return round(population_exact(loc) / 10000.0, 4)


def _weighted(loc: Dict[str, Any], key: str) -> float:
    """人口加权平均。"""
    cls = loc.get("classes") or {}
    tot = 0.0
    acc = 0.0
    for c in cls.values():
        p = c.get("pop") or 0
        tot += p
        acc += (c.get(key) or 0) * p
    return acc / tot if tot else 0.0



def material_production(loc: Dict[str, Any], live: Optional[Dict[str, Any]] = None) -> float:
    """本地物质实际产出总量 万单位/月。live 为修正系统的部门修正实时汇总。"""
    return local_production(loc, live)


def material_demand(loc: Dict[str, Any]) -> float:
    """本地物质总需求量 万单位/月。"""
    return local_demand(loc)


def supply_demand_ratio(loc: Dict[str, Any], live: Optional[Dict[str, Any]] = None) -> float:
    """地方供需指数。"""
    return supply_demand_index(loc, live)


def class_material_stock(loc: Dict[str, Any]) -> float:
    """本地各阶级物资库存合计 万单位。"""
    cls = loc.get("classes") or {}
    return round(sum(float(c.get("stock") or 0.0) for c in cls.values()), 2)


def support_total(loc: Dict[str, Any]) -> float:
    """扶持合计 两/月。"""
    return sum((c.get("support") or 0) for c in (loc.get("classes") or {}).values())


def controller_of(prov: Dict[str, Any] = None, loc: Dict[str, Any] = None) -> str:
    """该省**当前控制权**归属 世界数据写在 local.controller；没有就按归属推。

    控制权与核心 local.core是两码事：核心讲的是这地方认不认这个国家，
    控制权讲的是现在谁占着 —— 别拿 core 当控制权用。prov / loc 给一个即可。
    返回值一律是 `armies.OWNERS` 里的那一个写法 存档里写「明朝/明廷」这类同义写法
    也在这里归到同一个名字，所以下游只比较字符串即可。
    """
    from .armies import owner_label, owner_of
    if loc is None:
        loc = ((prov or {}).get("local") or {})
    ctrl = (loc or {}).get("controller")
    if ctrl:
        code = owner_of(ctrl)
        return owner_label(code) if code else ctrl
    if not prov:
        return ""
    return owner_label(prov.get("owner"))


def under_ming_control(prov: Dict[str, Any] = None, loc: Dict[str, Any] = None) -> bool:
    """当前属于明朝控制的省份 控制权＝明。范围写"全国"的修正只写进这些省。"""
    from .armies import owner_label
    return controller_of(prov, loc) == owner_label("ming")


def corruption(loc: Dict[str, Any], state=None) -> float:
    """地方腐败程度：50 ×  1 + 百分比修正 + 加减修正。"""
    from .depts_libu import calc_local_corruption
    return calc_local_corruption(state, loc)["value"]


def carrying(loc: Dict[str, Any], state=None) -> float:
    """地方承载人口：基础值 ×  1 + 百分比修正 + 加减修正。"""
    from .depts_libu import calc_local_carrying
    raw_base = float(loc.get("base_carrying") or 0.0)
    return calc_local_carrying(raw_base, state, loc)["value"]


def build_province_view(state, pid: str) -> Optional[Dict[str, Any]]:
    """地方详情页的完整载荷 【v0.3.7】从 server.py 抽出，服务端与测试共用这一份。

    含派生值 人口/物质产出/需求/供需/库存/税收、腐败与承载力明细、各阶级产能明细、
    辽饷物质折算、地区修正汇总、驻军等。前端读什么，这里就必须给什么。
    """
    p = (state.provinces or {}).get(pid)
    if p is None:
        return None
    loc = p.get("local") or {}
    view = dict(p)

    from .modifiers import (build_local_modifier_index, get_province_local_modifiers,
                            get_province_class_capacity_summary, get_province_all_modifiers,
                            province_monthly_tax)
    from . import depts_libu, economy
    live_index = build_local_modifier_index(state)
    live = get_province_local_modifiers(state, pid, live_index)
    raw_carrying = float(loc.get("base_carrying") or 0.0)
    corr_calc = depts_libu.calc_local_corruption(state, loc, pid)
    carry_calc = depts_libu.calc_local_carrying(raw_carrying, state, loc, pid)
    view["corruption"] = corr_calc["value"]
    view["carrying"] = carry_calc["value"]
    # 阶级基础权重：界面 ⓘ 直接念这一份，前端不写死数字（改了权重界面自动跟着变）
    view["class_weights"] = {
        "order": list(economy.CLASSES_ORDER),
        "prod": dict(economy.PROD_WEIGHTS),
        "demand": dict(economy.DEMAND_WEIGHTS),
    }
    # 宗教对需求的系数：界面 ⓘ 念这一份，不在前端写死百分比
    try:
        from .depts_rites import RELIGION_CONFIGS
        view["religion_demand_coeffs"] = [
            {"name": r.get("name", ""), "demand_coeff": r.get("demand_coeff", 0.0)}
            for r in RELIGION_CONFIGS
        ]
    except Exception:
        view["religion_demand_coeffs"] = []
    view["base_carrying"] = raw_carrying
    view["corruption_calc"] = corr_calc
    view["carrying_calc"] = carry_calc
    view["controller"] = controller_of(p)
    view["population"] = population(loc)
    view["population_exact"] = population_exact(loc)
    view["material_production"] = material_production(loc, live)
    view["material_demand"] = material_demand(loc)
    view["supply_demand_ratio"] = supply_demand_ratio(loc, live)
    view["class_material_stock"] = class_material_stock(loc)
    view["local_market_stock"] = loc.get("local_market_stock", 0.0)
    # 【v0.3.5】税收总额用实际征收口径 十税制实时分摊，旧 output×tax_rate 不再上界面
    # 税收只有一个口径：十税制实时分摊 户部账册即真相源
    view["tax_total"] = province_monthly_tax(state, pid, loc, live_index)
    view["support_total"] = support_total(loc)
    # 【v0.3.5】供需分级由后端一处给出，前端照抄
    status = economy.get_supply_status_info(view["supply_demand_ratio"])
    view["supply_status"] = {"name": status[0], "desc": status[1], "attitude_adj": status[2]}
    view["allocated_surcharge_wan"] = float(live.get("liaoxiang_monthly_wan") or 0.0)
    view["tax_matter_loss_total"] = float(live.get("flat_penalty_total") or 0.0)
    view["price_index"] = float(live["price_index"])

    cap_detail = get_province_class_capacity_summary(state, pid, loc, live_index)
    view["classes_info"] = {
        c_name: {
            "base_prod": d["base_prod"], "base_demand": d["base_demand"],
            "pct_buff": d["project_pct_buff"], "flat_buff": d["project_flat_buff"],
            "buff_notes": d["project_buff_notes"], "religion_prod_buff": d["religion_prod_buff"],
            "army_arrears_penalty": d["army_arrears_penalty"],
            "army_arrears_matter": d["army_arrears_matter"],
            "changying_penalty": d["changying_penalty"], "disaster_penalty": d["disaster_penalty"],
            "tax_matter_loss": d["tax_matter_loss"], "total_buff_pct": d["total_buff_pct"],
            "total_penalty_pct": d["total_penalty_pct"], "net_multiplier": d["net_multiplier"],
            "formula_text": d["formula_text"], "capacity": d["capacity"], "demand": d["demand"],
        } for c_name, d in cap_detail.items()
    }
    lx_wan = float(live.get("liaoxiang_monthly_wan") or 0.0)
    mcoef = float(live.get("matter_coef") or 0.0)
    view["liaoxiang"] = {
        "allocated_wan": round(lx_wan, 2),
        "matter_loss": round(lx_wan * mcoef, 2),
        "matter_coef": round(mcoef, 4),
        "price_index": round(float(live["price_index"]), 4),
        "matter_loss_by_class": {k: round(float(v), 2)
                                 for k, v in (live.get("flat_penalty") or {}).items()
                                 if float(v or 0.0) > 0},
    }

    from .distances import get_province_neighbor_distances, find_shortest_path
    view["neighbors_distances"] = get_province_neighbor_distances(pid, state)
    view["path_to_beijing"] = find_shortest_path(pid, "beizhili", state)
    view["modifiers"] = get_province_all_modifiers(state, pid)
    from .armies import MAINT_PER_MAN, PAY_PER_MAN, get_province_armies
    armies_view = get_province_armies(pid, state)
    # 欠饷/维护的构成常数随载荷下发：界面算「人数 × 单价 × 月数」时不在前端写死
    armies_view["pay_per_man"] = PAY_PER_MAN
    armies_view["maint_per_man"] = MAINT_PER_MAN
    view["province_armies"] = armies_view
    return view
