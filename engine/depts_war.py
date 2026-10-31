"""兵部专业模块：武官人事、粮饷筹措 辽饷加派、军队调动管理、省际最短路驿站。

功能规范：
1. 粮饷筹措：管理「辽饷加派」名目。
   - 设定月度辽饷征收额 默认 41.67 万两/月 = 500 万两/年。
   - 每月自民间流通白银 M1 中抽取该数额解缴国库。
   - 按照各省物理物质实际产出比例分摊到各省。
   - 地方修正：每分摊 1 万两，减少该省 商、农、百工、百役 产能 1.0%。
2. 军队调动与枢密调度：
   - 实时同步军队名册 机动部队白杆兵与地方守备镇军。
   - 显示当前位置与目标位置。
   - 提供折叠/选择目标省份后向目标位置调动行军。
3. 驿站传令管理：
   - 采用省际邻接距离图与 Dijkstra 算法计算最短用时路径 基准速度 250 km/day。
   - 任意省份到达北京的时间为沿途各省份间移动时间之和，路线选择总是选择时间总和最少的一条。
   - 驿站维持年费：30 万两/年 月均 2.50 万两/月。
4. 武官人事：
   - 统计全国武将与边帅。
   - 按省份分类 辽东单列，使用展开和折叠；不再有「九边」这一类别。
"""

from typing import Any, Dict, List, Tuple
from .officials import list_military_by_region
from .economy import local_base_production
from .distances import find_shortest_path, PROVINCE_NAMES, ADJACENT_GRAPH
from .armies import get_all_armies, select_units

# 辽饷**开局默认值**：只用于老存档没有该字段时兜底。
# 真相源是税目 depts_hubu.TAX_DEFINITIONS 里的 liaoxiang（月额=税基×税目修正），
# 运行时的月度辽饷一律从税目现算，这里不再作为可改的配置。
LIAOXIANG_CONFIG = {
    "annual_target": 500.0,    # 辽饷每年征收总额 万两 开局值
    "monthly_target": 41.67,   # 辽饷每月征收额 万两 开局值
    "name": "辽饷加派",
}

# 驿站通信基准配置
RELAY_CONFIG = {
    "base_speed_km_per_day": 250,  # 基准速度：250 千米/天
    "annual_cost": 30.0,           # 驿站维持年费 万两
    "monthly_cost": 2.50,          # 驿站维持月费 万两
}


def get_military_officers() -> List[Dict[str, Any]]:
    """武官人事：官职表里 kind=武职 的在任者（类型是数据，不做字面判断）。"""
    from . import positions as _pos
    return _pos.incumbents(kind="武职")


def get_military_officers_grouped() -> Dict[str, List[Dict[str, Any]]]:
    """武官人事 分组：按职位分类（省）分组，辽东单列在前。"""
    from .officials import list_military_by_region
    return list_military_by_region()


def calc_liaoxiang_apportionment(state) -> Dict[str, Any]:
    """各省辽饷分摊额与折物质扣减 **读税目 liaoxiang，不再自己算一遍**。

    口径（玩家定义）：辽饷本指是税目，只是在界面中位于兵部，实际与税目走同一套逻辑。
    真相源＝depts_hubu.TAX_DEFINITIONS 里的 liaoxiang：
      月征额 = 税基(全国物理总产能) × 税目修正，各省按本省基础物理产能占比分摊。
    本函数只把税目已经算好的分摊读出来，给兵部「粮饷筹措」界面用；
    扣减本身由户部税负那条路统一执行，这里**不再重复扣一次**。
    返回形状与并入税目之前完全一致，前端粮饷筹措表不用改。
    """
    from .depts_hubu import liaoxiang_tax_row
    row = liaoxiang_tax_row(state)
    if row is None:
        raise KeyError("税目表里找不到辽饷税目 liaoxiang："
                       "辽饷的唯一真相源是 depts_hubu.TAX_DEFINITIONS")

    # 银 → 物质：与税负同一把尺子 1两折物质比例 ÷ 物价指数
    from .economy import calc_price_index
    matter_coef = 1.0 / max(1e-6, float(calc_price_index(state)))

    apportionment = {}
    for pa in row.get("prov_apportionment") or []:
        pid = pa["province_id"]
        allocated_wan = float(pa.get("monthly_tax") or 0.0)
        matter_loss = round(allocated_wan * matter_coef, 2)
        apportionment[pid] = {
            "province_id": pid,
            "province_name": pa.get("province_name", pid),
            "production": float(pa.get("base_val") or 0.0),
            "ratio": float(pa.get("ratio") or 0.0),
            "allocated_silver_monthly": allocated_wan,
            "allocated_silver_annual": round(allocated_wan * 12, 1),
            "matter_loss": matter_loss,
            "penalty_desc": f"折物质 -{matter_loss:.2f}万单位  商/农/工/役",
        }

    monthly = float(row["monthly_amount"])
    return {
        "monthly_target": monthly,
        "annual_target": round(monthly * 12, 1),
        "total_material_production": round(float(row.get("base_val") or 0.0), 2),
        "apportionment": apportionment,
    }


def get_pay_categories(state) -> List[Dict[str, Any]]:
    """粮饷筹措名目 仅辽饷加派，按照名目分类，使用展开和折叠。"""
    lx = calc_liaoxiang_apportionment(state)
    return [
        {
            "id": "liaoxiang",
            "name": "辽饷加派",
            "monthly": lx["monthly_target"],
            "annual": lx["annual_target"],
            "apportionment": lx.get("apportionment", {})
        }
    ]


def get_relay_stations_info(state: Any = None) -> Dict[str, Any]:
    """驿站管理：各省沿最短邻接路径到达北京的时间与路线。"""
    base_spd = RELAY_CONFIG["base_speed_km_per_day"]

    stations = []
    # 按照明朝省份计算到北京 北直隶的最短路径
    all_pids = [
        "beizhili", "shandong", "shanxi", "henan", "shaanxi", "nanzhili",
        "sichuan", "huguang", "jiangxi", "zhejiang", "fujian", "guangdong",
        "guangxi", "yunnan", "guizhou", "liaodong", "dongfan"
    ]

    for pid in all_pids:
        p_name = PROVINCE_NAMES.get(pid, pid)
        if pid == "beizhili":
            stations.append({
                "province_id": pid,
                "province_name": f"{p_name}  京畿顺天府",
                "distance_km": 0,
                "single_trip_days": 0.5,
                "round_trip_days": 1.0,
                "path_str": "京畿直达",
                "speed_km_day": base_spd,
                "speed_desc": "半日达  京畿内传递"
            })
            continue

        res = find_shortest_path(pid, "beizhili", state, base_speed=base_spd)
        days = res["time_days"]
        round_trip = round(days * 2, 1)
        path_str = " → ".join(res["path_names"])

        stations.append({
            "province_id": pid,
            "province_name": f"{p_name}",
            "distance_km": res["dist_km"],
            "single_trip_days": days,
            "round_trip_days": round_trip,
            "path_str": path_str,
            "speed_km_day": base_spd,
            "speed_desc": f"{days}日可达  途径：{path_str}"
        })

    stations.sort(key=lambda x: x["single_trip_days"])

    return {
        "base_speed": base_spd,
        "monthly_cost": RELAY_CONFIG["monthly_cost"],
        "annual_cost": RELAY_CONFIG["annual_cost"],
        "stations": stations
    }


def get_war_overview(state) -> Dict[str, Any]:
    """兵部汇总视图数据，实时同步军队名册与调动状态。"""
    officers = get_military_officers()
    officers_grouped = get_military_officers_grouped()
    armies_data = get_all_armies(state)

    # 兵部调动表：只列明可调遣的机动部队 归属 owner=ming、编制 category=mobile，
    # 两条属性一起过滤；他国与叛军部队同在一份名册里，但不归明调度
    all_movable_armies = [dict(m) for m in select_units(armies_data, category="mobile", owner="ming")]

    # 可选目标省份列表：**按当前实际控制从 state 现取** 原来是写死的 17 个 id，
    # 与真实控制状况会漂。控制权口径与地方界面同源：localdata.under_ming_control。
    from .localdata import under_ming_control
    dest_provinces = [
        {"id": pid, "name": (p.get("name") or pid)}
        for pid, p in state.provinces.items()
        if under_ming_control(p)
    ]
    dest_provinces.sort(key=lambda x: x["name"])

    return {
        "officers": officers,
        "officers_grouped": officers_grouped,
        "officers_total": len(officers),
        "liaoxiang": calc_liaoxiang_apportionment(state),
        "pay_categories": get_pay_categories(state),
        "relays": get_relay_stations_info(state),
        "armies": all_movable_armies,
        "dest_provinces": dest_provinces,
    }
