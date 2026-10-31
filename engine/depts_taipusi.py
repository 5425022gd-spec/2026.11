# -*- coding: utf-8 -*-
"""太仆寺专业模块：常盈库 马价银与马政马匹生产。

玩家定义的规则：
1. 常盈库自成一本账，不做收支结算 只记自己的流水 state.changying_ledger，
   计入 M2 统计；开局存银 70 万两；
2. 每年公历 1 月 1 日零点，从**地方窖藏**固定抽取 50 万两入常盈库 数额后续可由 buff 修改；
   抽解银按已控制区省份数动态均摊、摊到下次抽取前 每月 1/12，
   再由修正系统按当前银物折算比例 见 economy.LIANG_TO_MATTER_RATIO依物价指数折成各省物质产能扣减；
3. 每月生产上等战马 100、中等战马 200、下等驭马 600 入库；
   马价按马匹目录单价自常盈库扣支，**常盈库不足时本月停止生产**；
4. 马匹库存供军队装备增援 engine/armies.py取用，与工部军械仓储分账。

马匹定义 名称 / 马价 / 月产能由本模块自持 HORSE_MODELS，工部器物目录不再含马匹。
"""

from typing import Any, Dict, List, Tuple

INITIAL_CHANGYING_WAN = 70.0        # 常盈库开局存银 万两
ANNUAL_EXTRACT_WAN = 50.0           # 每年自地方窖藏抽解额 万两
CHANGYING_MOD_NAME = "常盈库积银"    # 摊在各省的独立地区修正名 每年同名覆盖即"刷新"
HORSE_CATEGORY = "horses"
LEDGER_CAP = 100


# ═════════════════════════ 状态读写 ═════════════════════════

def get_changying_treasury(state) -> float:
    """常盈库存银 万两，开局 70 万两由 WorldState 声明。"""
    return float(state.changying_treasury)


# 马匹定义 太仆寺自持：名称 / 马价 两 / 月产能
HORSE_MODELS: List[Dict[str, Any]] = [
    {"name": "上等战马", "category": HORSE_CATEGORY, "unit_cost": 25.0, "monthly_capacity": 100,
     "unit": "匹", "desc": "口外上等蒙古骟马与陕甘健马，供精骑重装突击。"},
    {"name": "中等战马", "category": HORSE_CATEGORY, "unit_cost": 20.0, "monthly_capacity": 200,
     "unit": "匹", "desc": "营骑、斥候哨马与传令骑兵制式乘骑。"},
    {"name": "下等驭马", "category": HORSE_CATEGORY, "unit_cost": 10.0, "monthly_capacity": 600,
     "unit": "匹", "desc": "火炮辎重大车挽马与营房驮运骡马。"},
]


def horse_models() -> List[Dict[str, Any]]:
    """马匹定义 名称 / 马价 / 月产能，由太仆寺自持。"""
    return list(HORSE_MODELS)


def horse_names() -> List[str]:
    return [m["name"] for m in horse_models()]


def get_taipusi_horses(state) -> Dict[str, int]:
    """太仆寺马匹库存 开局 0，靠月产积累，马名齐全由 WorldState 开局声明。"""
    return state.taipusi_horses


def horse_monthly_cost_wan() -> float:
    """本月马价银支出 万两= Σ 月产能 × 单价 两 / 10000。"""
    total_liang = sum(float(m.get("monthly_capacity", 0) or 0) * float(m.get("unit_cost", 0) or 0)
                      for m in horse_models())
    return round(total_liang / 10000.0, 4)


def horse_inventory_value_wan(state) -> float:
    """马匹库存折马价银 万两，供界面统计。"""
    inv = get_taipusi_horses(state)
    total_liang = 0.0
    for m in horse_models():
        total_liang += int(inv.get(m["name"], 0)) * float(m.get("unit_cost", 0) or 0)
    return round(total_liang / 10000.0, 4)


def _record_changying_tx(state, txtype: str, amount: float, item: str, desc: str):
    """记一笔常盈库自己的流水 不写国库账单。"""
    from .time import date_str
    ledger = state.changying_ledger
    ledger.insert(0, {
        "date": date_str(state.date),
        "type": txtype,
        "amount": round(amount, 4),
        "item": item,
        "desc": desc,
    })
    if len(ledger) > LEDGER_CAP:
        state.changying_ledger = ledger[:LEDGER_CAP]


# ═════════════════════════ 已控制区 ═════════════════════════

def controlled_provinces(state) -> List[Tuple[str, str]]:
    """已控制区省份 [ id, 名称]：当前属明朝控制者 看控制权。

    同一份名单由 engine/localdata.py 一处判定，工部、户部与本模块不再各写一套过滤。
    """
    from .localdata import under_ming_control
    return [(pid, p.get("name", pid)) for pid, p in state.provinces.items()
            if under_ming_control(p)]


# ═════════════════════════ 常盈库积银修正 实时派生，不落盘═════════════════════════

def year_extract_wan(state) -> float:
    """本公历年自地方窖藏抽解入常盈库的数额 万两。

    开局为公历 1627 年 10 月，本年度正月朔 1627-01-01已经抽解过，
    故开局即有本年度抽解额 50 万两；此后每年元旦抽取时改写，一年内不变。
    """
    return float(state.changying_year_extract_wan)


def changying_penalties(state) -> Dict[str, Dict[str, Any]]:
    """常盈库积银对已控制区的白银抽解分摊 每年元旦按本年度抽解额重算，一年内不变。

    返回各省每月均摊的抽解银额 万两/月：年抽额 ÷ 12 再按已控制区省份数均摊，
    余数并入最后一省，保证各省合计恰为全国月摊额。
    银两折物质 按银物基准比例 ÷ 物价指数由修正系统统一折算，本模块只管自己的银账。
    本函数只算不写：省份里不存这份修正的副本，面板与产能都实时取。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if state is None:
        return out

    total_wan = year_extract_wan(state)
    monthly_wan = round(total_wan / 12.0, 4)
    provs = controlled_provinces(state)
    if monthly_wan <= 0 or not provs:
        return out

    import math
    n = len(provs)
    per = math.floor(monthly_wan / n * 10000) / 10000.0
    shares = [per] * n
    shares[-1] = round(monthly_wan - per * (n - 1), 4)

    for (pid, _pname), share in zip(provs, shares):
        out[pid] = {
            "name": CHANGYING_MOD_NAME,
            "monthly_extract_wan": float(share),          # 本省每月均摊抽解银 万两/月
            "annual_extract_wan": round(float(share) * 12, 4),
            "total_extract_wan": total_wan,
            "province_count": n,
            "desc": "公历 %d 年正月朔自地方窖藏抽解马价银 %.2f 万两入常盈库，"
                    "摊到下次抽取前每月 %.4f 万两；按银物基准比例、"
                    "再依物价指数折算产能扣减" % (int(getattr(state, "changying_last_year", 0) or 0),
                                            total_wan, float(share)),
        }
    return out


# ═════════════════════════ 年度抽取 公历 1 月 1 日零点═════════════════════════

def tick_changying_annual(state, elapsed_days: int = 0) -> List[str]:
    """公历每年 1 月 1 日零点：抽马价银入常盈库，并刷新全国产能惩罚。

    elapsed_days：本次推进跨过的天数。按天回填判断，避免 "j 5d" 这类跳日
    刚好跳过公历元旦 与 weather 的逐日回填同一做法。
    以 changying_last_year 记公历年份，保证同一公历年只抽一次。
    """
    notes: List[str] = []
    if state is None:
        return notes
    import datetime
    from .calendar import game_to_gregorian

    last_year = int(getattr(state, "changying_last_year", 0) or 0)
    greg_today = game_to_gregorian(state.date)
    hit = None
    for i in range(max(1, int(elapsed_days))):
        g = greg_today - datetime.timedelta(days=i)
        if g.month == 1 and g.day == 1 and g.year != last_year:
            hit = g
            break
    if hit is None:
        return notes

    # 1) 自地方窖藏抽解入常盈库
    want = float(ANNUAL_EXTRACT_WAN)
    avail = float(getattr(state, "local_reserve", 0.0))
    actual = round(min(want, max(0.0, avail)), 4)
    state.local_reserve = round(avail - actual, 4)
    state.changying_treasury = round(get_changying_treasury(state) + actual, 4)
    state.changying_last_year = hit.year
    # 本年度抽解额：全国产能修正由此实时派生 changying_penalties，不向省份写副本
    state.changying_year_extract_wan = actual
    _record_changying_tx(state, "收入", actual, "年抽马价银",
                         "公历%d年正月朔，自地方窖藏抽解马价银 %.2f万两入常盈库" % (hit.year, actual))
    notes.append("【太仆寺】公历%d年正月朔，自地方窖藏抽解马价银 %.2f万两入常盈库 现存 %.2f万两。"
                 % (hit.year, actual, get_changying_treasury(state)))

    # 2) 抽解银摊到下次抽取前：各省每月均摊一份，由修正系统折成物质产能扣减
    pen = changying_penalties(state)
    if pen:
        first = next(iter(pen.values()))
        notes.append("【太仆寺】公历%d年抽解银 %.2f 万两摊入 %d 个已控制区，每省每月 %.4f 万两，"
                     "由修正系统按银物基准比例折成产能扣减。"
                     % (hit.year, first["total_extract_wan"], first["province_count"],
                        first["monthly_extract_wan"]))
    return notes


# ═════════════════════════ 月度孳牧 ═════════════════════════

def tick_taipusi_monthly(state) -> List[str]:
    """太仆寺月度孳牧：按目录月产能入库，马价银自常盈库扣支；不足则本月停产。"""
    notes: List[str] = []
    if state is None:
        return notes
    models = horse_models()
    if not models:
        return notes

    cost_wan = horse_monthly_cost_wan()
    balance = get_changying_treasury(state)
    if cost_wan > 0 and balance < cost_wan:
        notes.append("【太仆寺】常盈库存银 %.2f万两，不敷本月马价 %.2f万两，本月马匹停止孳牧。"
                     % (balance, cost_wan))
        return notes

    inv = get_taipusi_horses(state)
    produced = []
    for m in models:
        qty = int(m.get("monthly_capacity", 0) or 0)
        if qty <= 0:
            continue
        name = m["name"]
        inv[name] = int(inv.get(name, 0)) + qty
        produced.append("%s %d匹" % (name, qty))
    if not produced:
        return notes

    state.changying_treasury = round(balance - cost_wan, 4)
    _record_changying_tx(state, "支出", cost_wan, "马价银",
                         "太仆寺孳牧月支马价银 %.2f万两 %s" % (cost_wan, "，".join(produced)))
    notes.append("【太仆寺】常盈库支发马价银 %.2f万两，孳牧入库：%s 库余 %.2f万两。"
                 % (cost_wan, "，".join(produced), get_changying_treasury(state)))
    return notes


# ═════════════════════════ 前端总览 ═════════════════════════

def get_taipusi_overview(state) -> Dict[str, Any]:
    """太仆寺前端总览数据打包入口 照户部/工部的 overview 结构。"""
    balance = get_changying_treasury(state)
    inv = get_taipusi_horses(state)
    cost_wan = horse_monthly_cost_wan()

    horses = []
    for m in horse_models():
        name = m["name"]
        cnt = int(inv.get(name, 0))
        unit = float(m.get("unit_cost", 0) or 0)
        horses.append({
            "name": name,
            "count": cnt,
            "quota": int(m.get("monthly_capacity", 0) or 0),
            "unit_cost": unit,
            "value_wan": round(cnt * unit / 10000.0, 4),
            "desc": m.get("desc", ""),
        })

    provs = controlled_provinces(state)
    monthly_total_wan = round(year_extract_wan(state) / 12.0, 4)
    per = (monthly_total_wan / len(provs)) if provs else 0.0

    return {
        "changying": {
            "balance": round(balance, 4),
            "annual_extract": round(float(year_extract_wan(state)), 4),   # 【v0.3.5】取实际抽解额 库银不足时低于常量 50
            "annual_extract_nominal": float(ANNUAL_EXTRACT_WAN),
            "extract_monthly_total_wan": monthly_total_wan,
            "extract_monthly_per_province_wan": round(per, 4),
            "penalty_province_count": len(provs),
            "last_extract_greg_year": int(getattr(state, "changying_last_year", 0) or 0),
            "modifier_name": CHANGYING_MOD_NAME,
        },
        "horses": horses,
        "horse_total_value_wan": horse_inventory_value_wan(state),
        "horse_monthly_cost_wan": cost_wan,
        "can_produce": (cost_wan <= 0) or (balance >= cost_wan),
        "ledger": list(getattr(state, "changying_ledger", []) or [])[:40],
    }
