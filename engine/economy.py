"""新经济系统：物质循环与货币循环独立双引擎。

设计原则 玩家预案：
1. 地方不产出货币，只产出单位物质；产出物质远多于流通货币，物质循环与货币循环相互独立。
2. 物质循环：
   - 阶层生产加权 基础值：
     宗室 0.0、士绅 0.1、商人 10.0 虚拟产能，不计入实际产出、
     农民 1.5、百工 2.0、百役 0.5、流民 0.0。
   - 阶层需求加权 基础值：
     宗室 50.0、士绅 20.0、商人 5.0、
     农民 1.0、百工 1.2、百役 0.8、流民 0.5。
   - 地方工程 Buff 加成规则：
     每个阶级产能 = 人口 × 基础产能 ×  1 + ∑百分比Buff + ∑固定值Buff
     商人产能为虚拟买卖量，不计入全省物理总产出。
   - 地方供需指数 = 本地物质实际产出 / 本地物质总需求。
   - 七大阶级操作表 A~G 涵盖全国/本地买卖与库存调节，结算顺序：商人→士绅→宗室→百工→百役→农民→流民。
   - 结算省份顺序：总产出越高的省份越先从全国市场中结算。
   - 供需影响各阶级态度 严重短缺-5、明显短缺-1、基本平衡+1、轻微过剩+2、明显过剩-2、严重过剩-5。
   - 库存每月损耗5%，市场未卖完物资月末清零；缺口自动由自身库存填补。
   - 人口每月自然增减 增0.3%/死0.2%，比例可被修正调整；连续两月供给低于50%且库存不足则饥荒死亡；供给比小于1的阶级每月转0.01% 每低0.1再多0.02%为流民；百工/百役/农民/士绅中态度连续两月高于同组均值1.2倍者自同组抽补本阶级0.1%人口。
3. 货币循环：
   - 货币储备包含国库、皇帝私库、地方银库、太仆寺常盈库与市场流通 m1。
   - m2 = m1 + 国库 + 皇帝私库 + 地方银库 + 太仆寺常盈库。
   - 绝对流通能力指数 g = m1 / ((1 - f) * G)，标准值为 1.0 g>1 通胀，g<1 通缩。
   - 货币活化度 m1 / m2，金融杠杆倍数 1 / (1 - f)。
   - 短期金融变化率 Δλ 信心与加速度 a；转化量 Δm1 = 0.5 * (1 - x/m2) * x * Δλ。
   - 货币损耗：仓储月损 0.005%，流通月损 0.05%。
   - 地方矿场：云南银矿群 28万两/年、辰州银矿 10万两/年、铜仁银矿 7万两/年、广丰银山银矿 6万两/年、尤溪银屏山银场 4万两/年。
4. 黄金机制：
   - 包含国库黄金 大部分、地方窖藏黄金 少部分、流通黄金 极少。
   - 黄金完全不加入市场计算，仅收录。
   - 流通黄金每月损失 0.01% 0.0001。
   - 云南金矿群产出 4000 两黄金/年 0.4 万两/年，月均注入国库黄金。
"""

import math
from typing import Any, Dict, List, Optional, Tuple

from .modifier_registry import apply_formula

# ── 物质生产基础加权 每人单位物质基础产出──
PROD_WEIGHTS: Dict[str, float] = {
    "宗室": 0.0,
    "士绅": 0.1,
    "商人": 10.0,   # 虚拟产能，不计入实际物质产出
    "农民": 1.5,
    "百工": 2.0,
    "百役": 0.5,
    "流民": 0.0,
}

# ── 物质需求基础加权 每人单位物质基础需求，按阶级消费水平分级──
DEMAND_WEIGHTS: Dict[str, float] = {
    "宗室": 50.0,
    "士绅": 20.0,
    "商人": 5.0,
    "农民": 1.0,
    "百工": 1.2,
    "百役": 0.8,
    "流民": 0.5,
}

# 白银折物质的基准比例：1 两白银 = 1 单位物质 物价指数 P = 1 时。
# 税负、抽解、欠饷等一切"银两造成的物质损失"都按这一把尺子折算：
#     物质损失 单位 = 银两 × LIANG_TO_MATTER_RATIO ÷ 物价指数 P_t
# 银与物的比例 物价指数由本模块按 M/Y 算一次，别处只引用，不重复折算。
LIANG_TO_MATTER_RATIO = 1.0

CLASSES_ORDER = ["商人", "士绅", "宗室", "百工", "百役", "农民", "流民"]

# ── 供需指数分级表 ──
#  上限, 状态名, 描述, 态度变化, 满足时矩阵操作, 不满足时矩阵操作
MATRIX_RULES = [
    (0.80, "严重短缺", "饥荒、暴动、物价飞涨，崩盘", -5,
     {"宗室": "E", "士绅": "E", "商人": "B", "农民": "E", "百工": "E", "百役": "E", "流民": "E"},
     {"宗室": "A", "士绅": "A", "商人": "A", "农民": "E", "百工": "E", "百役": "E", "流民": "E"}),
    (0.95, "明显短缺", "通胀压力大，民生困难", -1,
     {"宗室": "E", "士绅": "E", "商人": "B", "农民": "E", "百工": "E", "百役": "E", "流民": "E"},
     {"宗室": "A", "士绅": "A", "商人": "A", "农民": "E", "百工": "E", "百役": "E", "流民": "E"}),
    (0.99, "轻微短缺", "经济增长加速，投资活跃", 0,
     {"宗室": "E", "士绅": "E", "商人": "A", "农民": "E", "百工": "E", "百役": "E", "流民": "E"},
     {"宗室": "A", "士绅": "E", "商人": "E", "农民": "E", "百工": "E", "百役": "E", "流民": "E"}),
    (1.01, "基本平衡", "稳定但缺乏活力，持平", 1,
     {"宗室": "E", "士绅": "E", "商人": "C", "农民": "E", "百工": "E", "百役": "E", "流民": "E"},
     {"宗室": "A", "士绅": "E", "商人": "E", "农民": "E", "百工": "E", "百役": "E", "流民": "E"}),
    (1.05, "轻微过剩", "市场竞争激烈，消费旺盛", 2,
     {"宗室": "E", "士绅": "E", "商人": "C", "农民": "F", "百工": "F", "百役": "E", "流民": "E"},
     {"宗室": "E", "士绅": "E", "商人": "C", "农民": "F", "百工": "F", "百役": "E", "流民": "E"}),
    (1.20, "明显过剩", "企业亏损，裁员，经济放缓", -2,
     {"宗室": "G", "士绅": "G", "商人": "C", "农民": "F", "百工": "F", "百役": "E", "流民": "E"},
     {"宗室": "G", "士绅": "G", "商人": "C", "农民": "F", "百工": "F", "百役": "E", "流民": "E"}),
    (999999.0, "严重过剩", "大萧条，企业大量倒闭，崩盘", -5,
     {"宗室": "G", "士绅": "G", "商人": "D", "农民": "F", "百工": "F", "百役": "E", "流民": "E"},
     {"宗室": "G", "士绅": "G", "商人": "D", "农民": "F", "百工": "F", "百役": "E", "流民": "E"}),
]


def get_supply_status_info(idx: float):
    """根据供需指数匹配状态信息。"""
    for limit, name, desc, attitude_adj, satisfied_ops, unsatisfied_ops in MATRIX_RULES:
        if idx < limit:
            return name, desc, attitude_adj, satisfied_ops, unsatisfied_ops
    return MATRIX_RULES[-1][1], MATRIX_RULES[-1][2], MATRIX_RULES[-1][3], MATRIX_RULES[-1][4], MATRIX_RULES[-1][5]


# ── 地方工程 Buff 与辽饷减产修正解析 ──

def get_province_class_buffs(loc: Dict[str, Any], cls_name: str) -> Tuple[float, float, List[str]]:
    """获取指定省份对指定阶级的 Buff 汇总。
    返回 (pct_sum, flat_sum, buff_notes)
    - pct_sum: 百分比加成之和  如 +0.10 + 0.20 = 0.30
    - flat_sum: 固定产能加成之和  如 +25.0 万

    只有**运营中**的工程才产生增益：工程一旦损毁/停摆/在建，其效果立即停止
     状态与效果联动，避免"状态已损毁、效果还在发生"这类 bug。
    """
    if not loc:
        return 0.0, 0.0, []
    projs = loc.get("projects") or []
    pct_sum = 0.0
    flat_sum = 0.0
    notes = []

    for pj in projs:
        if str(pj.get("status") or "运营中") != "运营中":
            continue
        p_name = pj.get ("name", "工程")
        # 损毁程度按 (1−损毁) 折算，唯一实现处 engine.projects.effective_buff
        from .projects import effective_buff
        buff = effective_buff(pj)
        pct_map = buff.get("pct") or {}
        flat_map = buff.get("flat") or {}

        if cls_name in pct_map:
            p_val = float(pct_map[cls_name])
            pct_sum += p_val
            notes.append(f"{p_name}(+{int(p_val * 100)}%)")

        if cls_name in flat_map:
            f_val = float(flat_map[cls_name])
            flat_sum += f_val
            notes.append(f"{p_name} +{f_val:.1f}万")

    return round(pct_sum, 4), round(flat_sum, 2), notes


def get_province_disaster_penalty(loc: Dict[str, Any]) -> float:
    """获取地方自然灾害 大旱/大涝/寒潮/热浪导致的减产惩罚比例。"""
    if not loc:
        return 0.0
    w = loc.get("weather") or {}
    return float(w.get("capacity_penalty") or 0.0)


def get_province_plague_penalty(loc: Dict[str, Any]) -> float:
    """获取地方瘟疫 小疫/大疫导致的减产惩罚比例。"""
    if not loc:
        return 0.0
    plague = loc.get("plague") or {}
    if plague and int(plague.get("remain_days", 0)) > 0:
        return float(plague.get("penalty") or 0.0)
    return 0.0


def get_province_yellow_flood_penalty(loc: Dict[str, Any]) -> float:
    """黄汛减产比例（逐月衰减）。公式只有一处：engine.weather.yellow_flood_current_penalty。"""
    from .weather import yellow_flood_current_penalty
    return yellow_flood_current_penalty(loc)


def get_province_earthquake_penalty(loc: Dict[str, Any]) -> float:
    """获取历史地震导致的下月减产惩罚比例。"""
    if not loc:
        return 0.0
    eq = loc.get("earthquake_penalty") or {}
    if eq and int(eq.get("remain_days", 0)) > 0:
        return float(eq.get("penalty") or 0.0)
    return 0.0


# ── 阶级产出与需求计算 ──

def _live_mod_tables(live: Optional[Dict[str, Any]]) -> Tuple[
        Dict[str, float], Dict[str, float], Dict[str, float], Dict[str, float]]:
    """从修正系统的实时汇总里取出 增益表、百分比扣减表、固定增益表、物质绝对扣减表。"""
    if not live:
        return {}, {}, {}, {}
    return (live.get("buff_pct") or {}, live.get("penalty_pct") or {},
            live.get("flat_buff") or {}, live.get("flat_penalty") or {})


def _local_environment_penalty(loc: Dict[str, Any], from_live: bool = False) -> float:
    """地方自持的产能扣减。

    from_live=True 表示调用方已给了 live 修正系统实时汇总：天时 灾害/瘟疫/黄汛/地震
    已登记成产能修正、随 live["penalty_pct"] 一起进来，这里只补承载力超载，免得同一份天时计两次。
    from_live=False 无 live，例如非明属省份或直接调用时，天时照旧在本函数里直接算。
    """
    if not loc:
        return 0.0
    overload = carrying_overload_penalty(loc)
    if from_live:
        return overload
    return (get_province_disaster_penalty(loc) + get_province_plague_penalty(loc)
            + get_province_yellow_flood_penalty(loc) + get_province_earthquake_penalty(loc)
            + overload)


# ── 承载力：自然承载力 N 按月随产需差额变化，超载则压产能 ──
CARRYING_SCALE_L = 3.0        # 压缩尺度 L 与 P−D 同单位，万单位/月；取 2.5～3.5
CARRYING_OVERLOAD_COEF = 500  # 超载惩罚系数：惩罚% = ((N−D)/N)² × 500


def carrying_delta(natural: float, demand: float, prod: float,
                   scale: float = CARRYING_SCALE_L) -> float:
    """承载力月度变化：ΔN = |N−D|/N × sgn(P−D) × Lln(1 + |P−D|/L)。

    N＝自然承载力 存量，与 D/P 同口径，D＝总需求，P＝总产能；
    产能充足 P>D承载力升，产能不足 P<D承载力降。
    """
    if natural <= 0:
        return 0.0
    gap = prod - demand
    if gap == 0:
        return 0.0
    sign = 1.0 if gap > 0 else -1.0
    return (abs(natural - demand) / natural) * sign * scale * math.log(1.0 + abs(gap) / scale)


def carrying_overload_ratio(natural: float, demand: float) -> float:
    """超载率 = (D−N)/N，未超载为 0。"""
    if natural <= 0 or demand <= natural:
        return 0.0
    return (demand - natural) / natural


def carrying_overload_penalty(loc: Dict[str, Any]) -> float:
    """承载力超载造成的产能惩罚 小数。惩罚% = ((N−D)/N)² × 500，仅在 D>N 时施加。"""
    if not loc:
        return 0.0
    ratio = float(loc.get("carrying_overload_ratio") or 0.0)
    if ratio <= 0:
        return 0.0
    return (ratio ** 2) * CARRYING_OVERLOAD_COEF / 100.0


def class_capacity_base(cls_name: str, pop: float, loc: Dict[str, Any] = None,
                        live: Optional[Dict[str, Any]] = None) -> float:
    """阶级基础产能 税前：pop × 基础权重 ×  1 + 增益 + 固定增益。

    不含任何扣减项，故可作为税基与分摊比例的分母，避免"税重→产能降→税基缩小"
    的自我循环。增益取地方工程 loc 自持与部门实时增益 礼部宗教等。
    """
    base_w = PROD_WEIGHTS.get(cls_name, 0.0)
    pct_sum, flat_sum, _ = get_province_class_buffs(loc, cls_name) if loc else (0.0, 0.0, [])
    buff, _pen, flat_buff, _flat = _live_mod_tables(live)
    pct_sum += float(buff.get(cls_name, 0.0))
    flat_sum += float(flat_buff.get(cls_name, 0.0))
    return round(max(0.0, apply_formula(pop * base_w, [pct_sum], [flat_sum])), 2)


def class_capacity(cls_name: str, pop: float, loc: Dict[str, Any] = None,
                   live: Optional[Dict[str, Any]] = None) -> float:
    """阶级实际产能 万单位/月。商人为虚拟买卖产能，其他为实际产能。
    计算公式：pop × 基础产能 ×  1 + 增益 - 百分比扣减 + 固定增益 - 物质绝对扣减。
    四类取数，各为唯一真相源：
      部门增益 礼部宗教、中央修正等——修正系统实时汇总 live["buff_pct"]；
      部门百分比扣减 常盈库积银、欠饷等——live["penalty_pct"]；
      税负的物质绝对扣减 万单位/月——live["flat_penalty"] 税额 × K ÷ 物价指数；
      地方自持 工程增益、灾害瘟疫黄汛地震——取自 loc。
    """
    base_w = PROD_WEIGHTS.get(cls_name, 0.0)
    pct_sum, flat_sum, _ = get_province_class_buffs(loc, cls_name) if loc else (0.0, 0.0, [])
    buff, pen, flat_buff, flat_pen = _live_mod_tables(live)
    pct_sum += float(buff.get(cls_name, 0.0))
    flat_sum += float(flat_buff.get(cls_name, 0.0))
    total_penalty = float(pen.get(cls_name, 0.0)) + _local_environment_penalty(loc, bool(live))
    # 增益与百分比扣减都进公式的乘法项 扣减取负，物质绝对扣减另计
    cap = apply_formula(pop * base_w, [pct_sum, -total_penalty], [flat_sum]) \
        - float(flat_pen.get(cls_name, 0.0))
    return round(max(0.0, cap), 2)


def class_production(cls_name: str, pop: float, loc: Dict[str, Any] = None,
                     live: Optional[Dict[str, Any]] = None) -> float:
    """阶级实际物理物质产出 商人虚拟产能不计入。"""
    if cls_name == "商人":
        return 0.0
    return class_capacity(cls_name, pop, loc, live)


def class_demand(cls_name: str, pop: float, demand_pct: float) -> float:
    """阶级物质需求量 万单位/月。

    【v0.3.4】计算公式：pop × 基础需求 ×  1 + 需求百分比修正。
    需求修正的来源见 modifier_registry.demand_pct 宗教、超控设立的部门修正等，
    值存在省数据 modifier_demand_pct，开局与每月刷新写一次，需求、供需指数与面板同取这一份。
    """
    base_d = DEMAND_WEIGHTS.get(cls_name, 0.0)
    return round(max(0.0, apply_formula(pop * base_d, [float(demand_pct or 0.0)], [])), 2)


def local_production(loc: Dict[str, Any], live: Optional[Dict[str, Any]] = None) -> float:
    """本地实际物理物质产出总量 不含商人虚拟量。"""
    cls_map = loc.get("classes") or {}
    tot = 0.0
    for k, v in cls_map.items():
        pop = float(v.get("pop") or 0.0)
        tot += class_production(k, pop, loc, live)
    return round(tot, 2)


def local_base_production(loc: Dict[str, Any], live: Optional[Dict[str, Any]] = None) -> float:
    """本地基础物理产能 税前，含增益不含扣减，供税基与分摊比例使用。"""
    cls_map = loc.get("classes") or {}
    tot = 0.0
    for k, v in cls_map.items():
        if k == "商人":
            continue
        tot += class_capacity_base(k, float(v.get("pop") or 0.0), loc, live)
    return round(tot, 2)


def local_demand(loc: Dict[str, Any]) -> float:
    """本地物质总需求量 含需求修正 modifier_demand_pct，来源见 modifier_registry.demand_pct。"""
    cls_map = (loc or {}).get("classes") or {}
    dem_pct = float((loc or {}).get("modifier_demand_pct") or 0.0)
    tot = 0.0
    for k, v in cls_map.items():
        pop = float(v.get("pop") or 0.0)
        tot += class_demand(k, pop, dem_pct)
    return round(tot, 2)


def supply_demand_index(loc: Dict[str, Any], live: Optional[Dict[str, Any]] = None) -> float:
    """地方供需指数 = 本地实际产出 / 本地总需求。"""
    dem = local_demand(loc)
    if dem <= 0:
        return 1.0
    prod = local_production(loc, live)
    return round(prod / dem, 4)


# ── 货币系统派生指标与结算 ──

def calc_national_demand(state: Any) -> float:
    """计算全国物质需求总和 真实全国需求。

    口径与户部账册完全一致：只计明属各省 后金、蒙古诸部等境外之地不计入
    大明流通白银所对应的需求规模，故此处数值必与户部「全国需求总和」相等。
    """
    if not state or not hasattr(state, "provinces"):
        return 16980.0
    from .depts_hubu import get_ming_provinces
    tot = 0.0
    for _pid, _p, loc in get_ming_provinces(state):
        tot += local_demand(loc)
    return round(max(1.0, tot), 2)


def calc_liquidity_health(m1: float, financial_rate: float, demand_total: float) -> float:
    """计算金融绝对流通能力指标 g = 杠杆M1 / 上月全国需求总和 = m1 / ((1 - f) * 全国需求总和。
    标准值为 1.0。
    """
    denom = max(1e-5, (1.0 - financial_rate) * demand_total)
    return round(m1 / denom, 4)


def calc_price_index(state: Any) -> float:
    """物价指数 P_t = r_t / r₀，其中 r = 流通白银 / 全国物质需求 即 g 指标。

    r₀ 是固定基准 开局值，存 state.price_index_base，开局 P₀ = 1；
    P_t > 1 表示物价相对开局上涨，P_t < 1 表示下跌；
    注意它是"物价水平"，不是通胀率 通胀率 = P_t/P_{t-1} - 1。

    税负折算成物质绝对量时用它作分母：同样的银两，物价越高买到的物质越少。
    """
    if state is None:
        return 1.0
    demand = calc_national_demand(state)
    r_now = calc_liquidity_health(float(state.m1),
                                  float(state.financial_rate), demand)
    base = float(state.price_index_base or 0.0)
    if base <= 0.0 or r_now <= 0.0:
        state.price_index_base = max(1e-6, r_now)
        return 1.0
    return round(r_now / base, 4)


def calc_leverage(financial_rate: float) -> float:
    """金融杠杆倍数 = 1 / (1 - f)。"""
    denom = max(1e-5, 1.0 - financial_rate)
    return round(1.0 / denom, 2)


def tick_money_monthly(state) -> List[str]:
    """每月 1 日凌晨 0 点货币与黄金系统结算。

    1. 地方银矿与金矿产出自动入库。
    2. Δλ 结合加速度更新：Δλ_new = Δλ + a。
    3. 地方银库向 m1 转化：Δm1 = 0.5 * (1 - x/m2) * x * Δλ。
    4. 货币损耗：仓储月损 0.005%，流通白银月损 0.05%，流通黄金月损 0.01%。
    5. 重新计算 m2 与 g 指标。
    """
    notes = []

    # 1. 矿产月度产出结算 银矿归入国库或地方银库，金矿归入国库黄金；仅限当前明控省份
    monthly_mine_silver = 0.0
    monthly_mine_gold = 0.0
    from .localdata import under_ming_control
    for p in state.provinces.values():
        if not under_ming_control(p):
            continue
        loc = p.get("local") or {}
        for pj in loc.get("projects") or []:
            if str(pj.get("status") or "运营中") != "运营中":   # 未完工/停摆的矿不产银
                continue
            pb = pj.get("buff") or {}
            from .projects import scale as _proj_scale
            _k = _proj_scale(pj)          # 损毁程度折算
            ann_s = float(pb.get("silver") or 0.0) * _k
            if ann_s > 0:
                monthly_mine_silver += ann_s / 12.0
            ann_g = float(pb.get("gold") or 0.0) * _k
            if ann_g > 0:
                monthly_mine_gold += (ann_g / 10000.0) / 12.0  # 4000两 = 0.4万两

    if monthly_mine_silver > 0:
        state.treasury = round(state.treasury + monthly_mine_silver, 2)
        notes.append ("地方官办银矿月度课税解库 %.2f万两" % monthly_mine_silver)
    if monthly_mine_gold > 0:
        state.gold_treasury = round(state.gold_treasury + monthly_mine_gold, 4)
        notes.append("云南金矿群月度采金解缴私库 %.2f两" % (monthly_mine_gold * 10000.0))

    # 2. 兵部辽饷筹措 从流通白银 M1 抽取入国库，并按各省物质产出分摊
    liaoxiang_target = float(state.liaoxiang_monthly)
    if liaoxiang_target > 0 and state.m1 > 0:
        actual_lx = min(state.m1, liaoxiang_target)
        state.m1 = round(state.m1 - actual_lx, 2)
        state.treasury = round(state.treasury + actual_lx, 2)
        notes.append("兵部辽饷筹措：自民间流通银(M1)调拨解缴国库 %.2f万两" % actual_lx)
        # 各省辽饷分摊与减产修正由兵部账册实时计算 calc_liaoxiang_apportionment，
        # 不再向省份写副本，避免"看界面/推进时间"改动地方数据。

    # 3. 驿站维持费 从国库月度常例支给 2.50 万两
    relay_cost = float(state.relay_monthly_cost)
    if relay_cost > 0:
        state.treasury = round(max(0.0, state.treasury - relay_cost), 2)
        notes.append ("兵部驿站传令体系月度维持经费支出 %.2f万两" % relay_cost)

    m1 = float(state.m1)
    treasury = float(state.treasury)
    x = float(state.local_reserve)
    m2 = state.m2

    # 4. 信心演变
    state.confidence_delta = max(-0.9, min(0.9, state.confidence_delta + state.confidence_acc))
    d_lambda = state.confidence_delta

    # 5. 地方银库向 m1 转化
    if m2 > 0 and x > 0:
        ratio = x / m2
        delta_m1 = 0.5 * (1.0 - ratio) * x * d_lambda
        # 转化量不超过地方银库
        if delta_m1 > 0:
            trans = min(x, delta_m1)
            x -= trans
            m1 += trans
            notes.append ("地方银库活化流入市场 %.2f万两" % trans)
        elif delta_m1 < 0:
            # 信心低迷时，市场货币部分回流/沉淀到地方银库
            trans = min(m1, abs(delta_m1))
            m1 -= trans
            x += trans
            notes.append ("市场流动性紧缩回流地方银库 %.2f万两" % trans)

    # 6. 货币损耗 自然损耗
    treasury_loss = treasury * 0.00005
    reserve_loss = x * 0.00005
    m1_loss = m1 * 0.0005
    gold_m1_loss = state.gold_m1 * 0.0001  # 流通黄金损失 0.01%/月

    treasury = max(0.0, treasury - treasury_loss)
    x = max(0.0, x - reserve_loss)
    m1 = max(0.0, m1 - m1_loss)
    state.gold_m1 = round(max(0.0, state.gold_m1 - gold_m1_loss), 4)

    state.treasury = round(treasury, 2)
    state.local_reserve = round(x, 2)
    state.m1 = round(m1, 2)

    national_demand_now = calc_national_demand(state)
    g = calc_liquidity_health(state.m1, state.financial_rate, national_demand_now)
    status_label = "健康" if 0.95 <= g <= 1.05 else  "通胀" if g > 1.05 else "通缩"
    notes.append("货币结算：M1=%.1f万两, 地方银库=%.1f万两, 全国需求=%.1f万单位, 流通健康指数=%.2f %s"
                 % (state.m1, state.local_reserve, national_demand_now, g, status_label))
    return notes


# ── 货币接口扩展预留 ──

def inject_bullion(state, amount: float, category: str = "mining") -> None:
    """注入金银 开采/顺差/战争掠夺/朝贡，默认计入国库或地方银库。"""
    if category in ("mining", "war", "tribute"):
        state.treasury = round(state.treasury + amount, 2)
    else:  # trade 顺差
        state.m1 = round(getattr(state, "m1", 300.0) + amount, 2)


def issue_fiat(state, amount: float) -> None:
    """法币增发 直接增加 M1，会拉高 g 指数导致通胀。"""
    state.m1 = round(getattr(state, "m1", 300.0) + amount, 2)


# ── 人口自然变动率 留接口：可由"修正"刷新，科技与超控都能调──
POP_BIRTH_BASE = 0.003      # 每月各阶级自然增加 0.3% 的人口
POP_DEATH_BASE = 0.002      # 每月各阶级自然死亡 0.2% 的人口


def population_rates(state) -> Dict[str, float]:
    """当月生效的人口自然变动率＝基础值＋人口修正＋科技效果。

    人口修正见 `state.pop_rate_modifiers` 超控/事件写入；
    科技效果见 `engine/tech_effects.py` 已解锁科技合计，两处相加后再夹紧。
    """
    birth, death = POP_BIRTH_BASE, POP_DEATH_BASE
    for m in state.pop_rate_modifiers:
        birth += float(m.get("birth_delta") or 0.0)
        death += float(m.get("death_delta") or 0.0)
    from .tech_effects import total_of
    birth += total_of(state, "population_birth")
    death += total_of(state, "population_death")
    return {"birth": max(-1.0, birth), "death": max(0.0, min(1.0, death))}


def relief_matter_wan(loc: Dict[str, Any]) -> Dict[str, float]:
    """该省各阶级的赈济额 万两/月＝两/10000；按 1 两 = 1 单位物质折成万单位。"""
    out = {}
    for c_name, c in (loc.get("classes") or {}).items():
        sup = float(c.get("support") or 0.0)
        if sup > 0:
            out[c_name] = round(sup / 10000.0, 4)
    return out


def refugee_rate(ratio: float) -> float:
    """供给比低于 1 时每月转流民的比例：基础 0.01%，供给比每低 0.1 再加 0.02%。"""
    if ratio >= 1.0:
        return 0.0
    steps = int((1.0 - ratio) / 0.1 + 1e-9)
    return 0.0001 + steps * 0.0002


def apply_class_shift(classes: Dict[str, Any]) -> List[str]:
    """阶级变化：百工/百役/农民/士绅中态度连续两月高于同组均值 1.2 倍者，
    增加本阶级人口的 0.1%，自同组其他阶级按人口抽补。返回日志。
    """
    notes = []
    group = ["百工", "百役", "农民", "士绅"]
    present = [k for k in group if k in classes]
    atts = [float(classes[k].get("attitude", 50)) for k in present]
    avg_att = (sum(atts) / len(atts)) if atts else 0.0

    gainers = []
    for k in present:
        c_data = classes[k]
        if avg_att > 0 and float(c_data.get("attitude", 50)) >= 1.2 * avg_att:
            c_data["high_att_months"] = int(c_data.get("high_att_months", 0)) + 1
        else:
            c_data["high_att_months"] = 0
        if c_data.get("high_att_months", 0) >= 2:
            gainers.append(k)

    for k in gainers:
        others = [x for x in present if x != k]
        own_exact = int(classes[k].get("pop_exact") or 0)
        move = int(round(own_exact * 0.001))
        pool = sum(int(classes[x].get("pop_exact") or 0) for x in others)
        if pool <= 0 or move <= 0:
            continue
        move = min(move, pool)
        taken = 0
        for idx, x in enumerate(others):
            x_exact = int(classes[x].get("pop_exact") or 0)
            share = move - taken if idx == len(others) - 1 else int(round(move * x_exact / pool))
            share = max(0, min(share, x_exact))
            classes[x]["pop_exact"] = max(0, x_exact - share)
            classes[x]["pop"] = round(classes[x]["pop_exact"] / 10000.0, 4)
            taken += share
        classes[k]["pop_exact"] = own_exact + taken
        classes[k]["pop"] = round(classes[k]["pop_exact"] / 10000.0, 4)
        notes.append("%s态度高于同组均值，自同组抽补 %d人并入本阶级" % (k, taken))
    return notes


# ── 物质循环月度结算 ──

def apply_class_target_modifiers(state) -> List[str]:
    """【v0.3.5】把「部门修正」里 target=attitude 的效果加到各阶级。

    原来这个目标只在界面上显示、全库没有任何应用点 典型「界面有不生效」。
    每月结算一次：按 effects 的 classes 列表与 delta/delta_pct 施加，
    负数即为惩罚 对应超控指令「增加地方各阶级态度」的反向用法。
    """
    from .localdata import under_ming_control
    notes: List[str] = []
    dmods = state.department_modifiers
    if not dmods:
        return notes
    for pid, p in state.provinces.items():
        loc = p.get("local") or {}
        classes = loc.get("classes") or {}
        if not classes:
            continue
        for dmod in dmods.values():
            for eff in (dmod.get("effects") or []):
                target = eff.get("target")
                if target != "attitude":
                    continue
                # 范围：本省修正只作用该省；全国修正作用所有明控省份
                scope_prov = eff.get("province_id") or eff.get("province")
                if scope_prov and scope_prov not in (pid, p.get("name")):
                    continue
                if not scope_prov and not under_ming_control(p):
                    continue
                names = eff.get("classes") or list(classes.keys())
                delta = eff.get("delta")
                delta_pct = eff.get("delta_pct")
                for c_name in names:
                    c_data = classes.get(c_name)
                    if not c_data:
                        continue
                    cur = float(c_data.get(target) or 0.0)
                    if delta_pct is not None:
                        new_val = cur * (1.0 + float(delta_pct))
                    else:
                        new_val = cur + float(delta or 0.0)
                    c_data[target] = round(min(100.0, max(0.0, new_val)), 2)
                notes.append("【%s】%s生效：%s %s"
                             % (p.get("name", pid), dmod.get("name", ""),
                                "态度",
                                "、".join(names)))
    return notes


def tick_material_monthly(state) -> List[str]:
    """每月 1 日凌晨 0 点物质循环结算。

    1. 各省物质产出与需求初始化，按总产出由高到低排序，依次结算。
    2. 初始分配：若供需<1则按需求比例分，若供需>=1则拿满且余量入本地市场。
    3. 阶级按顺序 商→士→宗→工→役→农→流执行 A~G 操作。
    4. 需求缺口由库存补足。
    5. 供需指数判定各阶级态度变动。
    6. 月末损耗 库存 5%与市场未售清零。
    7. 人口自然增减 0.3%增/0.2%死、饥荒死亡、赈济转产能、供给不足转流民、阶级变化。
    """
    notes = []
    target_provs = []
    # 部门地方修正：本次结算开始时向各部门实时读一次，全国共用 不落盘
    from .modifiers import build_local_modifier_index
    from .localdata import under_ming_control
    from .depts_rites import calc_religion_summary
    live_index = build_local_modifier_index(state)
    # 需求修正写进省数据 月度缓存：值取自修正登记处的 demand 目标，
    # 宗教、超控设立的部门修正等都算在内；需求、供需指数与面板同取这一份。
    from .modifier_registry import demand_pct as _demand_pct
    for _pid, _p in state.provinces.items():
        _loc = _p.get("local") or {}
        if _loc.get("classes"):
            _loc["modifier_demand_pct"] = _demand_pct(state, _pid) if under_ming_control(_p) else 0.0
    # 人口机制作用于**所有有阶级数据的地区** 不只明属：满清、蒙古诸部等照样生老病死
    for pid, p in state.provinces.items():
        loc = p.get("local") or {}
        if not (loc.get("classes")):
            continue
        live = live_index.get(pid) if under_ming_control(p) else None
        prod = local_production(loc, live)
        if not under_ming_control(p):
            prod += float(loc.get("local_market_stock") or 0.0)
        target_provs.append((pid, p, loc, prod, live))

    # 结算顺序：总产出越高的省份越先结算
    target_provs.sort(key=lambda item: item[3], reverse=True)

    national_market_stock = getattr(state, "national_market_stock", 0.0)

    for pid, p, loc, prod, live in target_provs:
        p_name = p.get("name", pid)
        is_ming_market = under_ming_control(p)
        classes = loc.get("classes") or {}
        dem = local_demand(loc)
        dem_pct = float(loc.get("modifier_demand_pct") or 0.0)   # 需求修正 登记处口径
        idx = supply_demand_index(loc, live)
        diff = abs(idx - 1.00)
        status_name, status_desc, attitude_adj, sat_ops, unsat_ops = get_supply_status_info(idx)

        local_market_stock = float(loc.get("local_market_stock") or 0.0)

        # 本省既有市场余量在月初并入本省生产分配池，避免只存不用。
        prod += local_market_stock
        local_market_stock = 0.0

        # 1. 初始本地生产分配
        allocs = {}
        gaps = {}
        satisfied = {}
        surplus_by_class = {}

        if idx < 1.0:
            # 供需指数 < 1，按需求比例从总产出分配
            for c_name in CLASSES_ORDER:
                c_data = classes.get(c_name) or {}
                c_pop = float(c_data.get("pop") or 0.0)
                c_dem = class_demand(c_name, c_pop, dem_pct)
                alloc = prod * (c_dem / dem) if dem > 0 else 0.0
                allocs[c_name] = alloc
                gap = max(0.0, c_dem - alloc)
                gaps[c_name] = gap
                satisfied[c_name] = (gap <= 1e-6)
        else:
            # 供需指数 >= 1，拿满需求，多余的进入本地市场。
            surplus = max(0.0, prod - dem)
            local_market_stock += surplus
            physical_caps = {
                c: class_capacity(c, float((classes.get(c) or {}).get("pop") or 0.0), loc, live)
                for c in CLASSES_ORDER if c != "商人"
            }
            physical_cap_total = sum(physical_caps.values())
            surplus_by_class = {
                c: surplus * cap / physical_cap_total
                for c, cap in physical_caps.items() if physical_cap_total > 0
            }
            for c in CLASSES_ORDER:
                surplus_by_class.setdefault(c, 0.0)
            for c_name in CLASSES_ORDER:
                c_data = classes.get(c_name) or {}
                c_pop = float(c_data.get("pop") or 0.0)
                c_dem = class_demand(c_name, c_pop, dem_pct)
                allocs[c_name] = c_dem
                gaps[c_name] = 0.0
                satisfied[c_name] = True

        # 2. 阶级按顺序 商→士→宗→工→役→农→流执行操作
        for c_name in CLASSES_ORDER:
            c_data = classes.get(c_name)
            if not c_data:
                continue
            c_pop = float(c_data.get("pop") or 0.0)
            c_stock = float(c_data.get("stock") or 0.0)
            c_cap = class_capacity(c_name, c_pop, loc, live)
            gap = gaps.get(c_name, 0.0)
            is_sat = satisfied.get(c_name, True)

            # 选择操作代码
            op_dict = sat_ops if is_sat else unsat_ops
            op = op_dict.get(c_name, "G")

            # 农民、百工、百役、流民不可操作全国市场 若配为 A/B/C/D 则降级为 E/F/G
            if c_name in  ("农民", "百工", "百役", "流民"):
                if op in ("A", "B"):
                    op = "E"
                elif op in ("C", "D"):
                    op = "F"
            if not is_ming_market and op == "C":
                op = "E"
            if not is_ming_market and op == "D":
                op = "F"

            # 执行 A ~ G 操作
            if op == "A":
                # 从全国市场购买完全囤积，优先本地，不够买全国
                target = diff * 0.5 * gap
                # 本地买
                buy_local = min(target, local_market_stock)
                local_market_stock -= buy_local
                c_stock += buy_local
                remain = target - buy_local
                # 全国买
                buy_nat = min(remain, national_market_stock) if is_ming_market else 0.0
                national_market_stock -= buy_nat
                c_stock += buy_nat
            elif op == "B":
                # 明属地区可从全国市场买入；任何地区都可调整本地库存。
                target_nat = diff * c_cap
                buy_nat = min(target_nat, national_market_stock) if is_ming_market else 0.0
                national_market_stock -= buy_nat
                local_market_stock += buy_nat
                dump_stock = min(c_stock, diff * 0.5 * c_stock)
                c_stock -= dump_stock
                local_market_stock += dump_stock
            elif op == "C" and is_ming_market:
                # 明属地区本地市场买入后卖到全国市场。
                buy_local = min(local_market_stock, diff * c_cap)
                local_market_stock -= buy_local
                national_market_stock += buy_local
            elif op == "D":
                # 明属地区抛售到全国市场；其他地区只可转入本地市场。
                dump = min(c_stock, diff * c_stock)
                c_stock -= dump
                if is_ming_market:
                    national_market_stock += dump
                else:
                    local_market_stock += dump
            elif op == "E":
                # 仅在本地买入以满足自身短缺
                buy = min(gap, local_market_stock)
                local_market_stock -= buy
                allocs[c_name] = allocs.get(c_name, 0.0) + buy
            elif op == "F":
                # 抛售库存到本地市场
                dump = min(c_stock, diff * 0.5 * c_stock)
                c_stock -= dump
                local_market_stock += dump
            elif op == "G":
                # 囤积：把本阶级当月未分配的剩余产出留在阶级库存。
                surplus = float(surplus_by_class.get(c_name, 0.0))
                hoarded = min(local_market_stock, surplus)
                local_market_stock -= hoarded
                c_stock += hoarded

            c_data["stock"] = round(max(0.0, c_stock), 2)

        # 本地市场库存按省跨月保留；非明属地区不接入明朝全国市场。
        loc["local_market_stock"] = round(local_market_stock, 2)

        # 3. 赈济先补入本月供给，再统一判定态度、饥荒和流民转化。
        relief_want = relief_matter_wan(loc)
        if relief_want:
            relief_total = round(sum(relief_want.values()), 4)
            if float(getattr(state, "treasury", 0.0)) >= relief_total:
                state.treasury = round(float(state.treasury) - relief_total, 2)
                state.local_reserve = round(float(getattr(state, "local_reserve", 0.0)) + relief_total, 2)
                for k, v in relief_want.items():
                    if k in classes:
                        allocs[k] = allocs.get(k, 0.0) + v
                notes.append("【%s】赈济 %.4f万两自国库转入地方银库，等额转化为 %s 的产能。"
                             % (p_name, relief_total, "、".join(relief_want.keys())))
            else:
                notes.append("【%s】国库存银不足，本月赈济 %.4f万两未能发放。" % (p_name, relief_total))

        # 4. 自身库存平抑最终需求缺口
        supply_ratios = {}
        for c_name, c_data in classes.items():
            c_pop = float(c_data.get("pop") or 0.0)
            c_dem = class_demand(c_name, c_pop, dem_pct)
            alloc = allocs.get(c_name, 0.0)
            c_stock = float(c_data.get("stock") or 0.0)

            if alloc < c_dem and c_stock > 0:
                needed = c_dem - alloc
                used = min(needed, c_stock)
                c_stock -= used
                alloc += used
                c_data["stock"] = round(max(0.0, c_stock), 2)

            allocs[c_name] = alloc
            ratio = (alloc / c_dem) if c_dem > 0 else 1.0
            supply_ratios[c_name] = ratio

        # 赈济直接创造的物质已经计入 allocs；此处仅用最终供给率判定。
        # 4. 按各阶级最终供给率影响态度；超控/事件设立的态度修正也在这里按唯一公式应用
        #     这些修正登记在 modifier_registry 的 attitude 目标上，面板显示的就是它。
        from .modifier_registry import contributions as _attribs, OP_MUL as _OP_MUL
        att_rows: Dict[str, Dict[str, list]] = {}
        for _c in _attribs(state, "attitude", pid):
            for _cl in (_c.get("classes") or list(classes.keys())):
                if _cl not in classes:
                    continue
                slot = att_rows.setdefault(_cl, {"mults": [], "adds": []})
                slot["mults" if _c["op"] == _OP_MUL else "adds"].append(_c["value"])
        for c_name, c_data in classes.items():
            class_status = get_supply_status_info(supply_ratios.get(c_name, 1.0))
            class_att_adj = class_status[2]
            cur_att = float(c_data.get("attitude", 60)) + class_att_adj
            slot = att_rows.get(c_name)
            if slot:
                cur_att = apply_formula(cur_att, slot["mults"], slot["adds"])
            if class_att_adj != 0 or slot:
                c_data["attitude"] = max(0, min(100, int(cur_att)))

        # 5. 月度库存损耗 5%
        for c_data in classes.values():
            c_stock = float(c_data.get("stock") or 0.0)
            c_data["stock"] = round(c_stock * 0.95, 2)


        # 6. 人口月度变动：自然增减 0.3% 增 / 0.2% 死→ 饥荒死亡 → 供给不足转流民 → 阶级变化
        rates = population_rates(state)

        # 6.1 自然增加与自然死亡 比例可由"修正"刷新，见 population_rates
        for c_data in classes.values():
            exact = int(c_data.get("pop_exact") or round(float(c_data.get("pop", 0)) * 10000))
            exact = int(round(exact * (1.0 + rates["birth"])))
            exact = int(round(exact * (1.0 - rates["death"])))
            c_data["pop_exact"] = max(0, exact)
            c_data["pop"] = round(c_data["pop_exact"] / 10000.0, 4)

        # 6.2 饥荒死亡判定：连续 2 个月及以上实际供给 < 50% 且库存不足
        for c_name, c_data in classes.items():
            ratio = supply_ratios.get(c_name, 1.0)
            c_stock = float(c_data.get("stock") or 0.0)
            c_pop = float(c_data.get("pop") or 0.0)
            c_dem = class_demand(c_name, c_pop, dem_pct)

            if ratio < 0.5 and c_stock < (c_dem * 0.5 - allocs.get(c_name, 0.0)):
                c_data["famine_months"] = int(c_data.get("famine_months", 0)) + 1
            else:
                c_data["famine_months"] = 0

            if c_data.get("famine_months", 0) >= 2 and ratio < 0.5:
                loss_exact = int(round((0.5 - ratio) * 0.2 * c_data.get("pop_exact", int(c_pop * 10000))))
                cur_exact = c_data.get("pop_exact", int(c_pop * 10000))
                loss_exact = min(cur_exact, loss_exact)
                c_data["pop_exact"] = max(0, cur_exact - loss_exact)
                c_data["pop"] = round(c_data["pop_exact"] / 10000.0, 4)
                notes.append("【%s】%s发生饥荒死亡 %d人" % (p_name, c_name, loss_exact))

        # 6.3 流民转化：供给比 < 1 的阶级每月转 0.01% 为流民，供给比每低 0.1 再多转 0.02%
        if "流民" in classes:
            for c_name, c_data in list(classes.items()):
                if c_name == "流民":
                    continue
                ratio = supply_ratios.get(c_name, 1.0)
                rate = refugee_rate(ratio)
                if rate <= 0:
                    continue
                cur_exact = int(c_data.get("pop_exact") or int(float(c_data.get("pop", 0)) * 10000))
                move = min(cur_exact, int(round(cur_exact * rate)))
                if move <= 0:
                    continue
                c_data["pop_exact"] = max(0, cur_exact - move)
                c_data["pop"] = round(c_data["pop_exact"] / 10000.0, 4)
                r_exact = int(classes["流民"].get("pop_exact") or int(float(classes["流民"].get("pop", 0)) * 10000))
                classes["流民"]["pop_exact"] = r_exact + move
                classes["流民"]["pop"] = round(classes["流民"]["pop_exact"] / 10000.0, 4)
                notes.append("【%s】%s供给不足 %.2f，%d人转为流民" % (p_name, c_name, ratio, move))

        # 6.5 阶级变化：态度显著高于同组者自同组抽补 见 apply_class_shift
        for t in apply_class_shift(classes):
            notes.append("【%s】%s" % (p_name, t))

        # 7. 承载力：ΔN = |N−D|/N × sgn(P−D) × Lln(1 + |P−D|/L)
        #    产能足则承载力升、不足则降；超载 D>N记下超载率，产能惩罚从下月开始生效
        n_now = float(loc.get("base_carrying") or 0.0)
        if n_now > 0:
            delta_n = carrying_delta(n_now, dem, prod)
            if abs(delta_n) >= 0.005:
                loc["base_carrying"] = round(max(0.0, n_now + delta_n), 2)
                loc.pop("carrying", None)          # 承载力只留 base_carrying 一处口径
            ratio = carrying_overload_ratio(n_now, dem)
            loc["carrying_overload_ratio"] = round(ratio, 6)
            if ratio > 0:
                loc["carrying_overload_penalty"] = round(
                    carrying_overload_penalty(loc), 6)
                notes.append("【%s】承载力 %.1f 低于需求 %.1f 超载 %.2f%%，产能惩罚 %.2f%%"
                             % (p_name, n_now, dem, ratio * 100,
                                float(loc["carrying_overload_penalty"]) * 100))
            else:
                loc["carrying_overload_penalty"] = 0.0

    # 全国市场库存按明属市场口径跨月保留。
    state.national_market_stock = round(national_market_stock, 2)
    return notes


def get_gregorian_date(date_dict: Dict[str, Any]):
    """将游戏日期转换为对应的公历日期。"""
    import datetime
    base = datetime.date(1627, 10, 3)
    base_days = 1627 * 360 + 7 * 30 + 24
    cur_days = date_dict["year"] * 360 + (date_dict["month"] - 1) * 30 + (date_dict.get("day", 1) - 1)
    return base + datetime.timedelta(days=cur_days - base_days)


def tick_hubu_taxes_monthly(state) -> List[str]:
    """户部赋税月度征收结算：
    1. 刷新各省税收分摊与阶级减产惩罚。
    2. 按公历月份判断本月征收的税种：
       - 公历每月1日：钞关税、番舶抽分 留空、杂税。
       - 公历1月1日：丁银、坊厢银、班匠银、门摊银、盐课。
       - 公历6月1日：夏税。
       - 公历9月1日：秋粮。
    3. 按地方银库(local_reserve)与流通货币(m1)当前比例等比抽取解缴国库。
    """
    from .depts_hubu import calc_hubu_taxes, calc_hubu_basic_stats, TAX_DEFINITIONS
    notes = []

    # 1. 刷新分摊与减产惩罚
    tax_info = calc_hubu_taxes(state)
    stats = calc_hubu_basic_stats(state)
    c_pops = stats["class_pops"]
    c_caps = stats["class_caps"]
    tot_pop = stats["total_actual_pop"]
    tot_phys_cap = stats["total_physical_capacity"]

    greg = get_gregorian_date(state.date)
    g_month = greg.month
    g_day = greg.day   # 【v0.3.5】年税判期要精确到 1 月 1 日

    collected_taxes = []
    total_tax_to_collect = 0.0

    for tdef in TAX_DEFINITIONS:
        tid = tdef["id"]
        sched = tdef["schedule"]
        rate = float(tdef["rate"])

        due = False
        if sched == "monthly_1st":
            due = True
        elif sched == "annual_jan1" and (g_month, g_day) == (1, 1):
            due = True
        elif sched == "annual_jun1" and g_month == 6:
            due = True
        elif sched == "annual_sep1" and g_month == 9:
            due = True

        if not due:
            continue

        if tid == "dingyin":
            amt = c_pops.get ("农民", 0.0 * rate)
        elif tid == "fangxiang":
            amt = (c_pops.get ("商人", 0.0 + c_pops.get ("百工", 0.0 + c_pops.get ("百役", 0.0) * rate)))
        elif tid == "banjiang":
            amt = c_pops.get ("百工", 0.0 * rate)
        elif tid == "mentan":
            amt = c_pops.get ("商人", 0.0 * rate)
        elif tid in ("xiashui", "qiuliang"):
            amt = c_caps.get ("农民", 0.0 * rate)
        elif tid == "yanke":
            amt = tot_pop * rate
        elif tid == "chaoguan":
            amt = c_caps.get ("商人", 0.0 * rate)
        elif tid == "fanbo":
            amt = 0.0
        elif tid == "zashui":
            amt = tot_phys_cap * rate
        elif tid == "juanna":
            amt = c_pops.get ("士绅", 0.0 * rate)
        else:
            amt = 0.0

        amt = round(amt, 2)
        if amt > 0:
            total_tax_to_collect += amt
            collected_taxes.append (f"{tdef['name']} {amt:.2f}万两")

    if total_tax_to_collect > 0:
        res = float(getattr(state, "local_reserve", 20000.0))
        m1 = float(getattr(state, "m1", 10000.0))
        tot_money = res + m1
        if tot_money > 0:
            r_ratio = res / tot_money
            m_ratio = m1 / tot_money
        else:
            r_ratio = 0.5
            m_ratio = 0.5

        actual_tax = min(tot_money, total_tax_to_collect) if tot_money > 0 else 0.0
        deduct_res = round(actual_tax * r_ratio, 2)
        deduct_m1 = round(actual_tax * m_ratio, 2)

        # ── 腐败衍生效果结算 需求4──
        # 1. 地方腐败：在所有户部税收结算时，每个地方结算时，总是扣除【地方腐败%】的白银不进入国库，继续留存在地方窖藏
        from .depts_libu import calc_central_corruption, calc_local_corruption
        local_corr_info = calc_local_corruption(state)
        local_corr_rate = max(0.0, min(1.0, float(local_corr_info.get("value", 50.0)) / 100.0))
        local_retained = round(actual_tax * local_corr_rate, 2)
        tax_after_local = actual_tax - local_retained

        # 2. 中央腐败：户部税收在地方腐败结算后，再扣除【中央腐败%】到地方窖藏
        central_corr_info = calc_central_corruption(state)
        central_corr_rate = max(0.0, min(1.0, float(central_corr_info.get("value", 82.5)) / 100.0))
        central_retained = round(tax_after_local * central_corr_rate, 2)
        final_tax_to_treasury = round(max(0.0, tax_after_local - central_retained), 2)

        # 扣除民间税负 地方银库与M1扣除实际征税额
        state.local_reserve = round(max(0.0, state.local_reserve - deduct_res), 2)
        state.m1 = round(max(0.0, state.m1 - deduct_m1), 2)

        # 被地方腐败和中央腐败扣留的税银，回流并留存在地方银库窖藏
        total_corr_retained = round(local_retained + central_retained, 2)
        state.local_reserve = round(state.local_reserve + total_corr_retained, 2)

        # 最终解缴国库
        state.treasury = round(state.treasury + final_tax_to_treasury, 2)

        notes.append("户部赋税征收 公历%d月：应征 %.2f万两，经地方腐败(%.1f%%)留存 %.2f万两、中央腐败(%.1f%%)截留 %.2f万两回流地方窖藏，实解国库 %.2f万两 明细：%s"
                     % (g_month, actual_tax, local_corr_rate * 100, local_retained, central_corr_rate * 100, central_retained, final_tax_to_treasury, "、".join(collected_taxes)))

        # 征税后重新计算税收分摊
        calc_hubu_taxes(state)

    return notes


def tick_rites_monthly(state) -> List[str]:
    """礼部月度与年度结算：
    1. 对外贸易差额结算 每月1日：
       - 出口收汇 - 进口付汇 = 贸易差额。
       - 顺差直接加入地方银库窖藏(local_reserve)，逆差从地方银库窖藏扣除。
    2. 年度结算 每年公历1月1日：
       - 藩属国朝贡银入国库。
       - 地方教育人口与高等教育人口增加。
    """
    from .depts_rites import calc_trade_summary, DIPLOMATIC_COUNTRIES, calc_education_summary
    notes = []

    # 0. 教育人口按人口自然死亡比例每月流失 与阶级人口同一套死亡比例
    rates = population_rates(state)
    if rates["death"] > 0:
        state.local_educated_pop = round(
            float(getattr(state, "local_educated_pop", 185.0)) * (1.0 - rates["death"]), 4)
        state.higher_educated_pop = round(
            float(getattr(state, "higher_educated_pop", 15.0)) * (1.0 - rates["death"]), 4)

    # 1. 贸易结算
    trade = calc_trade_summary(state)
    net_trade = trade["net_trade_monthly"]
    if net_trade > 0:
        state.local_reserve = round(state.local_reserve + net_trade, 2)
        notes.append("礼部贸易结算：本月出口收汇 %.2f万两，进口付汇 %.2f万两，贸易顺差 %.2f万两直接入地方银库窖藏。"
                     % (trade["total_export_monthly"], trade["total_import_monthly"], net_trade))
    elif net_trade < 0:
        deduct = min(float(state.local_reserve), abs(net_trade))
        state.local_reserve = round(max(0.0, state.local_reserve - deduct), 2)
        notes.append("礼部贸易结算：本月出口收汇 %.2f万两，进口付汇 %.2f万两，贸易逆差 %.2f万两自地方银库窖藏扣除。"
                     % (trade["total_export_monthly"], trade["total_import_monthly"], abs(net_trade)))

    # 2. 年度结算 公历 1 月 1 日
    # 【v0.3.5】原来判 `greg.month == 1`：公历 1 月的每一次月朔都算一次，
    # 而 30 天月朔会有一年两次落在 1 月 如 1629-01-01 与 1629-01-31，年结被重复执行。
    # 改为只在公历 1 月 1 日当天结算 与太仆寺常盈库同一判法。
    greg = get_gregorian_date(state.date)
    if (greg.month, greg.day) == (1, 1):
        # 藩属朝贡
        tribute_tot = sum(float(c.get("tribute_annual", 0.0)) for c in DIPLOMATIC_COUNTRIES if c.get("relation") == "藩属")
        if tribute_tot > 0:
            state.treasury = round(state.treasury + tribute_tot, 2)
            tribute_names = [f"{c['name']} {c['tribute_annual']:.1f}万两" for c in DIPLOMATIC_COUNTRIES if c.get("relation") == "藩属" and c.get("tribute_annual", 0) > 0]
            notes.append("礼部岁首朝贡：藩属国朝贡银共计 %.2f万两解缴国库 明细：%s"
                         % (tribute_tot, "、".join(tribute_names)))

        # 教育人口增加
        edu = calc_education_summary(state)
        loc_growth = edu["local_education"]["annual_growth_expected"]
        high_growth = edu["higher_education"]["annual_growth_expected"]
        state.local_educated_pop = round(getattr(state, "local_educated_pop", 185.0) + loc_growth, 2)
        state.higher_educated_pop = round(getattr(state, "higher_educated_pop", 15.0) + high_growth, 2)
        notes.append("礼部岁首科举教育：地方新增受教育人口 %.2f万人，新增受高等教育人口 %.2f万人。"
                     % (loc_growth, high_growth))
        # 受高等教育人口占比过高 → 刷新「结构性失业」修正
        from .depts_rites import refresh_structural_unemployment
        unemp_note = refresh_structural_unemployment(state)
        if unemp_note:
            notes.append(unemp_note)

    # 3. 灾异祭祀次月1日刷新
    from .depts_rites import tick_sacrifices_monthly
    notes += tick_sacrifices_monthly(state)

    # 4. 科举考试：开考日到了就算开过一场 下次开考以这次为基准
    from .depts_rites import tick_exams
    notes += tick_exams(state)

    return notes


def tick_economy_monthly(state) -> List[str]:
    """月度经济统一结算调度 物质循环 + 赋税征收 + 贸易与礼部结算 + 兵部军饷发放 + 货币循环。"""
    notes = []

    # 季度初：势力 AI 本季决策 季度判定与 weather 同一处。
    # 注：欠饷不再季度清零 —— 它是「军饷筹措」主线的目标，只在真正付清时减少。
    from .weather import season_of
    if state.date.get("month") in (3, 6, 9, 12):
        from .faction_ai import ensure_quarter_plan
        for fa in ensure_quarter_plan(state):
            notes.append(fa)

    # 【v0.3.5】部门修正里的态度目标先落地 见 apply_class_target_modifiers
    notes += apply_class_target_modifiers(state)
    notes += tick_material_monthly(state)
    notes += tick_hubu_taxes_monthly(state)
    notes += tick_rites_monthly(state)

    # 兵部月度军饷发放 国库不足时产生欠饷与产能惩罚
    from .armies import tick_military_expenses_monthly
    notes += tick_military_expenses_monthly(state)

    # 工部月度军械器物制造 花费国库银两增加仓储库存
    from .depts_works import tick_works_production_monthly, tick_maintenance_monthly
    notes += tick_works_production_monthly(state)

    # 【v0.3.4】工部月度维护支给 年维护费 = 造价 × 1%，原来只在界面显示、从不扣款
    notes += tick_maintenance_monthly(state)

    # 太仆寺月度孳牧
    from .depts_taipusi import tick_taipusi_monthly
    notes += tick_taipusi_monthly(state)

    notes += tick_money_monthly(state)
    from .operations import tick_local_modifiers_monthly
    notes += tick_local_modifiers_monthly(state)
    return notes
