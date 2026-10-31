"""大明王朝 1627 地方地区修正与 Buff 综合规整系统 (Province Modifiers System)。

本模块对所有影响地方省份的 Buff 与修正进行统一归整、核算与打表，涵盖：
1. 军队与防务修正：
   - 辽饷加派分摊减产修正 兵部，影响 商/农/工/役
   - 驻军欠饷减产修正 地方镇军 + 进驻机动军团，每欠1万两扣减全阶级产能1.0%
2. 六部政纪、法律与税收修正：
   - 吏部行政纪律 党争工具+2%、吏律实亡+10%、京察瘫痪+5%腐败与-10%承载力
   - 刑部现行法律 《大明律》+5%、《问刑条例》+10%腐败
   - 户部十大税制分摊减产修正 丁银、坊厢、班匠、门摊、夏税、秋粮、盐课、钞关、杂税等
   - 礼部宗教全局加成 儒家+6.0%、佛教-2.0%产能/-2.0%需求、民间宗教+1.5%等
   - 工部地方工程增益、特设防灾水利减免 海宁海塘-70%洪涝、灵州石堤-20%黄汛、矿税金银采收
3. 气象灾害与异象修正：
   - 季度极端天气灾害 大旱/小旱、大涝/小涝、寒潮、热浪、台风等减产
   - 疠疫流行 大疫-25%、小疫-10%
   - 黄汛决口 逐月衰减减产
   - 历史地震 下月减产
4. 交通与省际通达修正：
   - 省际移动修正倍率 基准1.00，驰道加速/战乱减速
   - 邻接通达里程、时效与通达京师最优驿路
5. 供求平衡与阶级态度变动修正：
   - 地方供求指数 产出/需求与阶级态度月度调整
"""

from typing import Any, Dict, List, Optional, Tuple

# 阶级词表与解析只有一处定义 engine/operations.py：超控指令解析器所在地
from .operations import classes_in as _classes_of


def _empty_class_map() -> Dict[str, float]:
    from .economy import CLASSES_ORDER
    return {c: 0.0 for c in CLASSES_ORDER}


def _capacity_shares(loc: Optional[Dict[str, Any]], classes: List[str]) -> Dict[str, float]:
    """按各阶级基础产能占比分摊 用于把一笔税负摊到承担它的各阶级身上。"""
    from .economy import class_capacity_base
    caps = {}
    for c in classes:
        pop = float(((loc or {}).get("classes") or {}).get(c, {}).get("pop") or 0.0)
        caps[c] = class_capacity_base(c, pop, loc)
    total = sum(caps.values())
    if total <= 0:
        return {}
    return {c: v / total for c, v in caps.items() if v > 0}


def under_ming_control(prov: Optional[Dict[str, Any]]) -> bool:
    """当前属于明朝控制的省份：只看**控制权** local.controller ＝ 明，不看核心。

    唯一实现放在 localdata 省份数据那一层，这里只是本模块的调用别名，
    免得同一件事在两处各写一遍判定。
    """
    from .localdata import under_ming_control as _impl
    return _impl(prov)


# ═══════════════════════════════════════════════════════════
# 部门地方修正 实时汇总 唯一取数入口：不落盘、不复写
# ═══════════════════════════════════════════════════════════

def build_local_modifier_index(state: Any) -> Dict[str, Dict[str, Any]]:
    """一次性向各部门实时读取各省地方修正，返回 {省id: 修正汇总}。

    各部门自持其账：户部十税制分摊、兵部辽饷与欠饷、礼部宗教、太仆寺常盈库。
    本函数只读不算账——不向省份写任何副本，故「打开界面」不会改动游戏数据，
    面板与产能同取这一份结果，二者必然一致。
    """
    from .economy import CLASSES_ORDER
    from .depts_hubu import get_ming_provinces, calc_hubu_taxes
    from .depts_war import calc_liaoxiang_apportionment
    from .depts_rites import calc_religion_summary
    from .depts_taipusi import changying_penalties
    from .armies import get_all_armies, select_units

    index: Dict[str, Dict[str, Any]] = {}

    # ── 各部门实时总表 各取一次，供全部省份分摊──
    tax_table = calc_hubu_taxes(state)
    lx_table = calc_liaoxiang_apportionment(state)
    rel_summary = calc_religion_summary(state)
    changying = changying_penalties(state)

    tax_breakdown = {pb.get("province_id"): pb for pb in tax_table.get("prov_breakdown", [])}
    tax_rows_by_prov: Dict[str, List[Dict[str, Any]]] = {}
    # ── 银 → 物质：一次折算，四路银课共用同一把尺子 ──
    # 基准比例：1 两白银 = 1 单位物质 economy.LIANG_TO_MATTER_RATIO；
    # 物价指数 P_t 固定基准为开局 r₀ = 流通白银/全国物质需求，开局 P₀=1作分母，
    # 物价越高，同样银两折出的物质越少。银与物的比例已由货币系统按 M/Y 算过一次，
    # 此处只引用，不再重复折算。
    from .economy import calc_price_index, LIANG_TO_MATTER_RATIO
    price_index = calc_price_index(state)
    matter_coef = LIANG_TO_MATTER_RATIO / max(1e-6, price_index)   # 万单位物质 / 万两白银

    for t in tax_table.get("taxes", []):
        for pa in t.get("prov_apportionment", []):
            monthly_tax = float(pa.get("monthly_tax") or 0.0)
            if monthly_tax <= 0:
                continue
            tax_rows_by_prov.setdefault(pa.get("province_id"), []).append({
                "name": t.get("name"),
                "monthly_tax": monthly_tax,
                "affected_classes": pa.get("affected_classes", ""),
                "schedule_desc": t.get("schedule_desc", ""),
                "matter_loss": round(monthly_tax * matter_coef, 2),
                # 本税目在本省由各阶级分别承担的月额 万两/月，逐笔折算不重复计
                "class_burden": dict(pa.get("class_burden") or {}),
            })

    # ── 兵部欠饷 自持名册，按省归并；镇军 + 进驻机动部队，均按属性过滤──
    arrears_by_prov: Dict[str, Dict[str, Any]] = {}
    roster = get_all_armies(state)
    for u in select_units(roster, owner="ming"):
        wan = float(u.get("arrears_wan") or 0.0)
        if wan <= 0:
            continue
        is_garrison = u.get("category") == "garrison"
        pid = u.get("province_id") if is_garrison else (u.get("current_location") or u.get("province_id"))
        e = arrears_by_prov.setdefault(pid or "", {"wan": 0.0, "items": []})
        e["wan"] += wan
        e["items"].append({
            "type": "镇军" if is_garrison else "机动",
            "name": u.get ("name", "地方镇军" if is_garrison else "机动部队"),
            "arrears_wan": wan,
        })

    # 产能类中央修正 超控部门修正、教育加成、科技效果、宗教不再在这里拼装：
    # 它们都由 engine/modifier_registry 统一收集 见下面各 pid 的 contributions 循环。

    for pid, p, loc in get_ming_provinces(state):
        pname = p.get("name", pid)
        items: List[Dict[str, Any]] = []
        buff = _empty_class_map()
        pen = _empty_class_map()
        flat_buff = _empty_class_map()   # 加法类产能修正 万元/月 口径
        flat = _empty_class_map()          # 税负折算出的物质绝对扣减 万单位/月

        # 1) 辽饷：已并入税目 liaoxiang，扣减由下面「户部 十税制分摊」那条路统一执行。
        #    原来这里还按兵部名义再扣一遍，会与税负重复计一次 —— 现在只有一条路。

        # 2) 户部 十税制分摊 白银 → 物质绝对量；按税目×阶级的承担额逐笔折算，不重复计
        for row in tax_rows_by_prov.get(pid, []):
            cls_list = _classes_of(row["affected_classes"]) or list(CLASSES_ORDER)
            tax_amounts = {}
            for c in cls_list:
                burden = float((row.get("class_burden") or {}).get(c, 0.0))
                if burden > 0:
                    amt = burden * matter_coef
                    tax_amounts[c] = round(amt, 2)
                    flat[c] = round(flat[c] + amt, 2)
            items.append({
                "name": row["name"],
                "dept": "户部",
                "kind": "penalty",
                "effect_pct": 0.0,
                "matter_loss": row["matter_loss"],
                "class_amounts": tax_amounts,
                "detail": "本省每月摊派 %.2f 万两，%s结清一次，按各阶级承担比例折成物质从库存里扣除"
                          % (row["monthly_tax"], row.get("schedule_desc", "")),
                "classes": cls_list,
            })

        # 3) 太仆寺 常盈库积银 年抽解银摊到下次抽取前 → 折物质，按阶级均分
        cy = changying.get(pid)
        if cy and float(cy.get("monthly_extract_wan") or 0.0) > 0:
            loss = round(float(cy["monthly_extract_wan"]) * matter_coef, 2)
            per_cls = round(loss / len(CLASSES_ORDER), 2)
            for c in CLASSES_ORDER:
                flat[c] = round(flat[c] + per_cls, 2)
            items.append({
                "name": cy.get ("name", "常盈库积银"),
                "dept": "太仆寺",
                "kind": "penalty",
                "effect_pct": 0.0,
                "matter_loss": loss,
                "class_amounts": {c: per_cls for c in CLASSES_ORDER},
                "detail": "本省每月均摊抽解银 %.4f 万两，折成物质从库存里扣除，%s"
                          % (float(cy["monthly_extract_wan"]), cy.get("desc", "")),
                "classes": list(CLASSES_ORDER),
            })

        # 4) 兵部 驻军欠饷 欠发军饷折物质，按阶级均分
        arr = arrears_by_prov.get(pid)
        if arr and arr["wan"] > 0:
            loss = round(float(arr["wan"]) * matter_coef, 2)
            per_cls = round(loss / len(CLASSES_ORDER), 2)
            for c in CLASSES_ORDER:
                flat[c] = round(flat[c] + per_cls, 2)
            items.append({
                "name": "驻军欠饷减产",
                "dept": "兵部",
                "kind": "penalty",
                "effect_pct": 0.0,
                "matter_loss": loss,
                "class_amounts": {c: per_cls for c in CLASSES_ORDER},
                "detail": "本省欠饷 %.2f 万两，折成物质从库存里扣除，欠饷各营为 %s"
                          % (float(arr["wan"]),
                             "、".join("%s %.2f 万两" % (i["name"], i["arrears_wan"])
                                       for i in arr["items"][:4])),
                "classes": list(CLASSES_ORDER),
            })

        # 5) 修正（产能类）：**所有来源一视同仁**——宗教、超控部门修正、科技、教育加成、
        #    地方独立修正全部由修正登记处收集，只做算术，不在这里生成任何「某类修正」的行。
        #    建筑是唯一例外，走工程数据与 economy.get_province_class_buffs，不在这里。
        from .modifier_registry import contributions as _contribs, OP_MUL as _OP_MUL
        for _c in _contribs(state, "capacity", pid):
            cls_list = [x for x in (_c.get("classes") or list(CLASSES_ORDER)) if x in CLASSES_ORDER]
            if _c["op"] != _OP_MUL:
                # 加法类产能修正：并进固定增益 万元/单位口径，与工程 flat 同处相加
                for c in cls_list:
                    flat_buff[c] = round(flat_buff.get(c, 0.0) + _c["value"], 4)
                continue
            for c in cls_list:
                if _c["value"] > 0:
                    buff[c] = round(buff[c] + _c["value"], 4)
                else:
                    pen[c] = round(pen[c] - _c["value"], 4)

        # 6) 中央修正与 7) 地方独立修正的**算术**已在上面那次 contributions 循环里做完，
        #    这里不再重算一遍 避免同一份修正计两次。

        index[pid] = {
            "province_id": pid,
            "province_name": pname,
            "controlled": True,
            "items": items,
            "buff_pct": buff,
            "penalty_pct": pen,
            "flat_buff": flat_buff,
            "flat_penalty": flat,
            "flat_penalty_total": round(sum(flat.values()), 2),
            "tax_breakdown": tax_breakdown.get(pid, {}),
            "price_index": price_index,
            "matter_coef": round(matter_coef, 4),
            "liaoxiang_pct": 0.0,
            # 本省辽饷月摊额 从税目 liaoxiang 的各省分摊里读（真相源是税目，不是兵部自持的一份）
            "liaoxiang_monthly_wan": float(
                ((lx_table.get("apportionment") or {}).get(pid) or {}).get("allocated_silver_monthly") or 0.0),
            "arrears_wan": float(arr["wan"]) if arr else 0.0,
            "arrears_items": list(arr["items"]) if arr else [],
            "changying_monthly_wan": float(cy.get("monthly_extract_wan") or 0.0) if cy else 0.0,
        }

    return index


def province_monthly_tax(state, pid: str, loc: Optional[Dict[str, Any]] = None,
                         index: Optional[Dict[str, Dict[str, Any]]] = None) -> float:
    """本省每月税额 万两/月——唯一出处：户部十税制账册 get_province_dept_modifiers 的 taxation。

    原来面板上还挂着一条旧口径 provinces.json 的 output × tax_rate，两条路能对不上；
    现在谁要这个数都调这里。
    """
    loc = loc if loc is not None else ((state.provinces.get(pid) or {}).get("local") or {})
    dept = get_province_dept_modifiers(state, pid, loc, index)
    return round(float((dept.get("taxation") or {}).get("monthly_tax_total") or 0.0), 2)


def get_province_local_modifiers(state: Any, pid: str,
                                 index: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """单省部门地方修正 面板与产能共用；index 可传入以避免重复全量计算。"""
    if index is None:
        index = build_local_modifier_index(state)
    entry = index.get(pid)
    if entry:
        return entry
    empty = _empty_class_map()
    return {"province_id": pid, "province_name": pid, "controlled": False,
            "items": [], "buff_pct": empty, "penalty_pct": dict(empty),
            "flat_buff": dict(empty),
            "flat_penalty": dict(empty), "flat_penalty_total": 0.0, "tax_breakdown": {},
            "price_index": 1.0, "matter_coef": 0.0,
            "liaoxiang_pct": 0.0, "liaoxiang_monthly_wan": 0.0, "arrears_wan": 0.0,
            "arrears_items": [], "changying_monthly_wan": 0.0}


def get_province_military_modifiers(state: Any, pid: str, loc: Optional[Dict[str, Any]] = None,
                                    index: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """1. 军队与防务修正：辽饷分摊与驻军欠饷。"""
    if loc is None and state and hasattr(state, "provinces") and pid in state.provinces:
        loc = state.provinces[pid].get("local") or {}
    loc = loc or {}

    p = state.provinces.get(pid, {}) if (state and hasattr(state, "provinces")) else {}
    is_controlled = under_ming_control(p)
    live = get_province_local_modifiers(state, pid, index) if state else None

    # 1) 辽饷加派分摊 仅限当前明控省份，实时取兵部账
    from .depts_war import calc_liaoxiang_apportionment
    allocated_wan = 0.0
    liaoxiang_penalty_pct = 0.0
    lx_ratio = 0.0
    lx_annual = 0.0

    if state and is_controlled:
        app_info = calc_liaoxiang_apportionment(state)["apportionment"].get(pid, {})
        if app_info:
            allocated_wan = float(app_info["allocated_silver_monthly"])
            liaoxiang_penalty_pct = float(app_info.get("penalty_pct", 0.0))
            lx_ratio = float(app_info["ratio"])
            lx_annual = float(app_info["allocated_silver_annual"])

    # 2) 镇军欠饷 兵部自持名册，非控制区无明军卫所镇军
    gar_arrears = 0.0
    gar_pen_pct = 0.0

    # 3) 驻防机动部队欠饷
    mobile_stationed = []
    mobile_arrears = 0.0
    if is_controlled and live:
        for it in live.get("arrears_items", []):
            wan = float(it.get("arrears_wan") or 0.0)
            if it.get("type") == "镇军":
                gar_arrears += wan
            else:
                mobile_arrears += wan
        gar_pen_pct = round(min(1.00, gar_arrears) * 0.01, 4)
        from .armies import load_armies_data, select_units
        for m in select_units(load_armies_data()["units"], category="mobile", owner="ming"):
            cur_loc = m.get("current_location", m.get("province_id", ""))
            if cur_loc != pid and not (not cur_loc and m.get("province_id") == pid):
                continue
            m_arr = float(m.get("arrears_wan") or 0.0)
            mobile_stationed.append({
                "id": m.get("id"),
                "name": m.get ("name", "机动部队"),
                "status": m.get("status", ""),
                "count": m.get("count", 0),
                "commander": m.get("commander", ""),
                "arrears_wan": m_arr,
                "penalty_pct": round(m_arr * 0.01, 4),
                "station": m.get("station", f"{p.get('name', pid)}营防")
            })

    total_arrears = round(gar_arrears + mobile_arrears, 2)
    # 银 → 物质：与税负同一把尺子 1两 = LIANG_TO_MATTER_RATIO 单位物质 ÷ 物价指数
    from .economy import calc_price_index, LIANG_TO_MATTER_RATIO
    price_index = calc_price_index(state) if state else 1.0
    matter_coef = LIANG_TO_MATTER_RATIO / max(1e-6, price_index)
    lx_matter = round(allocated_wan * matter_coef, 2)
    arrears_matter = round(total_arrears * matter_coef, 2)

    return {
        "liaoxiang": {
            "name": "辽饷加派分摊",
            "allocated_silver_monthly": allocated_wan,
            "allocated_silver_annual": lx_annual,
            "production_ratio": lx_ratio,
            "production_ratio_pct": f"{lx_ratio * 100:.2f}%",
            "matter_loss": lx_matter,
            "penalty_pct": 0.0,
            "penalty_desc": f"每月折物质 -{lx_matter:.2f} 万单位  商/农/工/役" if lx_matter > 0 else "无分摊减产",
            "rule": "月度分摊银额 × 1两折物质比例 ÷ 物价指数，扣减本省 商、农、百工、百役 产能",
            "affected_classes": ["商人", "农民", "百工", "百役"],
            "active": allocated_wan > 0,
        },
        "army_arrears": {
            "name": "驻军欠饷减产",
            "garrison_arrears_wan": gar_arrears,
            "garrison_penalty_pct": gar_pen_pct,
            "mobile_stationed": mobile_stationed,
            "mobile_arrears_wan": mobile_arrears,
            "total_arrears_wan": total_arrears,
            "matter_loss": arrears_matter,
            "penalty_pct": 0.0,
            "penalty_desc": f"欠饷 {total_arrears:.2f} 万两，每月折物质 -{arrears_matter:.2f} 万单位  全阶级"
                            if total_arrears > 0 else "军饷充足 无欠饷减产",
            "rule": "所欠军饷 万两× 1两折物质比例 ÷ 物价指数，扣减所在地全阶级产能",
            "affected_classes": ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"],
            "active": total_arrears > 0,
        },
        "total_military_penalty_pct": {c: 0.0 for c in
                                       ("商人", "农民", "百工", "百役", "士绅", "宗室", "流民")},
    }


def get_province_dept_modifiers(state: Any, pid: str, loc: Optional[Dict[str, Any]] = None,
                                index: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """2. 六部政纪、法律、税收与礼仪修正 户部/礼部实时取自部门账，不读省份副本。"""
    if loc is None and state and hasattr(state, "provinces") and pid in state.provinces:
        loc = state.provinces[pid].get("local") or {}
    loc = loc or {}

    p = state.provinces.get(pid, {}) if (state and hasattr(state, "provinces")) else {}
    is_controlled = under_ming_control(p)
    live = get_province_local_modifiers(state, pid, index) if state else None

    # 1) 吏部与刑部：腐败与承载力
    from .depts_libu import calc_local_corruption, calc_local_carrying, ADMIN_DISCIPLINE_BUFFS
    from .depts_xingbu import ACTIVE_LAWS

    corr_calc = calc_local_corruption(state, loc, pid)
    raw_carrying = float(loc.get("base_carrying") or 0.0)
    carrying_calc = calc_local_carrying(raw_carrying, state, loc, pid)

    discipline_items = []
    if is_controlled:
        for b in ADMIN_DISCIPLINE_BUFFS:
            discipline_items.append({
                "name": b["name"],
                "tag": b.get("tag", "政纪"),
                "desc": b.get("desc", ""),
                "local_corruption_pct": b.get("local_corruption_pct", 0.0),
                "carrying_pct": b.get("carrying_pct", 0.0),
                "effect_desc": f"地方腐败 {b['local_desc']} 承载力 {b['carrying_desc']}"
            })

    law_items = []
    if is_controlled:
        for l in ACTIVE_LAWS:
            law_items.append({
                "name": l["name"],
                "category": l.get ("category", "法律"),
                "desc": l.get("desc", ""),
                "local_corruption_pct": l.get("local_corruption_pct", 0.0),
                "effect_desc": f"地方腐败 +{l.get('local_corruption_pct', 0.0) * 100:.0f}%"
            })

    # 2) 户部：十税制分摊与减产 实时取户部账册
    hubu_taxes = []
    class_tax_burdens = {}
    class_tax_penalties = {}
    total_monthly_tax = 0.0
    total_annual_tax = 0.0

    if state and is_controlled:
        from .depts_hubu import calc_hubu_taxes
        tax_table = calc_hubu_taxes(state)
        for t in tax_table["taxes"]:
            for pa in t["prov_apportionment"]:
                if pa["province_id"] != pid:
                    continue
                m_tax = float(pa["monthly_tax"])
                a_tax = float(pa["annual_tax"])
                total_monthly_tax += m_tax
                total_annual_tax += a_tax
                hubu_taxes.append({
                    "id": t["id"],
                    "name": t["name"],
                    "category": t["category"],
                    "schedule_desc": t["schedule_desc"],
                    "monthly_tax": m_tax,
                    "annual_tax": a_tax,
                    "affected_classes": pa.get("affected_classes", ""),
                    "penalty_pct": float(pa.get("penalty_pct", 0.0)),
                    "penalty_desc": pa.get("penalty_desc", ""),
                    "modifier_text": pa.get("modifier_text", "")
                })
        pb = (live or {})["tax_breakdown"]
        class_tax_penalties = dict(pb.get("penalties") or {})
        class_tax_burdens = dict(pb.get("class_tax_burdens") or {})

    # 3) 礼部：宗教全局加成 仅限明朝控制区
    from .depts_rites import calc_religion_summary, RELIGION_CONFIGS
    rel_summary = calc_religion_summary(state) if is_controlled else {}
    religion_rows = []
    current_inf = state.religion_influences if state else {}

    if is_controlled:
        for r in RELIGION_CONFIGS:
            r_name = r["name"]
            inf = float(current_inf.get(r_name, r["default_inf"]))
            p_pct = round(inf * r["prod_coeff"], 4)
            d_pct = round(inf * r["demand_coeff"], 4)
            pr_pct = round(inf * r["prestige_coeff"], 4)

            effects = []
            if p_pct != 0.0:
                effects.append (f"全阶级产能 {p_pct * 100:+.1f}%")
            if d_pct != 0.0:
                effects.append (f"全阶级需求 {d_pct * 100:+.1f}%")
            if pr_pct != 0.0:
                effects.append (f"皇室威望加成 {pr_pct * 100:+.1f}%")

            religion_rows.append({
                "name": r_name,
                "influence": inf,
                "prod_pct": p_pct,
                "demand_pct": d_pct,
                "prestige_pct": pr_pct,
                "effect_desc": " ".join(effects) if effects else "常恒基准"
            })

    net_rel_prod_pct = round(rel_summary.get("total_prod_pct", 0.0), 4) if is_controlled else 0.0
    net_rel_demand_pct = round(rel_summary.get("total_demand_pct", 0.0), 4) if is_controlled else 0.0

    # 引入部门修正 地方腐败：全国所有地方腐败 或 本省指定腐败
    department_items = []
    if is_controlled and state and state.department_modifiers:
        for dmod in state.department_modifiers.values():
            for eff in (dmod.get("effects") or []):   # 【v0.3.7】脏数据 effects 为 None不得让取数崩
                t = eff.get("target")
                if t == "all_local_corruption" or (t == "prov_corruption" and eff.get("province_id") == pid):
                    department_items.append({
                        "name": dmod["name"],
                        "dept": dmod.get ("dept", "中枢"),
                        "desc": eff.get("desc", ""),
                        "effect_desc": eff.get("desc", "")
                    })

    return {
        "corruption": {
            # 统一形状：base / mult_sum / add_sum / value / formula / calculation_text
            "base": corr_calc["base"],
            "mult_sum": corr_calc["mult_sum"],
            "add_sum": corr_calc["add_sum"],
            "value": corr_calc["value"],
            "formula": corr_calc["formula"],
            "calculation_text": corr_calc["calculation_text"],
            "discipline_items": discipline_items,
            "law_items": law_items,
            "department_items": department_items,
        },
        "carrying": {
            "base": carrying_calc["base"],
            "mult_sum": carrying_calc["mult_sum"],
            "add_sum": carrying_calc["add_sum"],
            "value": carrying_calc["value"],
            "formula": carrying_calc["formula"],
            "calculation_text": carrying_calc["calculation_text"],
        },
        "taxation": {
            "monthly_tax_total": round(total_monthly_tax, 2),
            "annual_tax_total": round(total_annual_tax, 1),
            "taxes": hubu_taxes,
            "class_tax_burdens": class_tax_burdens,
            "class_tax_penalties": class_tax_penalties,
        },
        "religion": {
            "religions": religion_rows,
            "net_prod_pct": net_rel_prod_pct,
            "net_prod_pct_display": f"{'+' if net_rel_prod_pct > 0 else ''}{net_rel_prod_pct * 100:.1f}%",
            "net_demand_pct": net_rel_demand_pct,
            "net_demand_pct_display": f"{'+' if net_rel_demand_pct > 0 else ''}{net_rel_demand_pct * 100:.1f}%",
            "summary_desc": f"宗教综合增益：全阶级产能 {net_rel_prod_pct * 100:+.1f}%，总需求 {net_rel_demand_pct * 100:+.1f}%"
        }
    }


def get_province_weather_modifiers(state: Any, pid: str, loc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """3. 气象灾害、瘟疫、黄汛与地震修正。"""
    if loc is None and state and hasattr(state, "provinces") and pid in state.provinces:
        loc = state.provinces[pid].get("local") or {}
    loc = loc or {}

    w = loc.get("weather") or {}
    plague = loc.get("plague") or {}
    yf = loc.get("yellow_flood") or {}
    eq = loc.get("earthquake_penalty") or {}

    # 1) 极端气象灾害
    disaster_name = w.get("disaster")
    disaster_remain = int(w.get("disaster_remain") or 0)
    disaster_pen = float(w.get("capacity_penalty") or 0.0)
    has_disaster = bool(disaster_name and disaster_remain > 0 and disaster_pen > 0)

    # 2) 瘟疫
    plague_name = plague.get("name")
    plague_remain = int(plague.get("remain_days") or 0)
    plague_pen = float(plague.get("penalty") or 0.0) if plague_remain > 0 else 0.0
    has_plague = bool(plague_name and plague_remain > 0 and plague_pen > 0)

    # 3) 黄汛（衰减公式只有一处：engine.weather.yellow_flood_current_penalty）
    from .weather import yellow_flood_current_penalty
    yf_name = yf.get("name")
    yf_remain = int(yf.get("remain_days") or 0)
    yf_init_pen = float(yf.get("initial_penalty") or 0.0)
    cur_yf_pen = yellow_flood_current_penalty(loc)
    has_yf = bool(yf_name and yf_remain > 0 and cur_yf_pen > 0)

    # 4) 历史地震
    eq_name = eq.get ("name", "大地震")
    eq_remain = int(eq.get("remain_days") or 0)
    eq_pen = float(eq.get("penalty") or 0.0) if eq_remain > 0 else 0.0
    has_eq = bool(eq_remain > 0 and eq_pen > 0)

    total_disaster_pen = round(disaster_pen + plague_pen + cur_yf_pen + eq_pen, 4)

    return {
        "weather_condition": w.get ("condition", "晴"),
        "temperature": w.get("temp", 0),
        "disaster": {
            "active": has_disaster,
            "name": disaster_name if has_disaster else "风调雨顺",
            "remain_days": disaster_remain,
            "penalty_pct": disaster_pen,
            "desc": f"【{disaster_name}】持续剩余 {disaster_remain} 日，产能 -{disaster_pen * 100:.1f}%" if has_disaster else "风调雨顺 无极端气象减产"
        },
        "plague": {
            "active": has_plague,
            "name": plague_name if has_plague else "无疫情",
            "remain_days": plague_remain,
            "penalty_pct": plague_pen,
            "desc": f"【{plague_name}】流行中，剩余 {plague_remain} 日，全阶级额外减产 -{plague_pen * 100:.1f}%" if has_plague else "无疫病流行"
        },
        "yellow_flood": {
            "active": has_yf,
            "name": yf_name if has_yf else "河道安澜",
            "remain_days": yf_remain,
            "initial_penalty_pct": yf_init_pen,
            "current_penalty_pct": round(cur_yf_pen, 4),
            "decay_per_month": float(yf.get("decay_per_month") or 0.0),
            "month_idx": int(yf.get("month_idx") or 0),
            "desc": f"【{yf_name}】决溢，剩余 {yf_remain} 日，当月减产 -{cur_yf_pen * 100:.1f}% 每月衰减 {float(yf.get('decay_per_month') or 0.0) * 100:.0f}%" if has_yf else "黄河安澜 无决口泛滥"
        },
        "earthquake": {
            "active": has_eq,
            "name": eq_name if has_eq else "地脉安固",
            "remain_days": eq_remain,
            "penalty_pct": eq_pen,
            "desc": f"【{eq_name}】灾异，剩余 {eq_remain} 日，全省产能 -{eq_pen * 100:.1f}%" if has_eq else "地脉安固 无地震受灾"
        },
        "total_disaster_penalty_pct": total_disaster_pen,
        "affected_classes": "全阶级",
    }


def get_province_project_modifiers(state: Any, pid: str, loc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """4. 地方工程、特设防灾水利与矿场 Buff。"""
    if loc is None and state and hasattr(state, "provinces") and pid in state.provinces:
        loc = state.provinces[pid].get("local") or {}
    loc = loc or {}

    projs = loc.get("projects") or []
    from .economy import CLASSES_ORDER

    class_pct_buffs_default = list(CLASSES_ORDER)   # 归零占位去掉：增益一律向 economy 取

    project_cards = []
    total_silver = 0.0
    total_gold = 0.0

    for pj in projs:
        name = pj.get ("name", "工程")
        kind = pj.get ("kind", "工程")
        status = pj.get ("status", "运营中")
        scope = pj.get ("scope", "本省")
        from .projects import effective_buff, damage_of
        buff = effective_buff(pj)          # 按 (1−损毁程度) 折算后的数值
        raw_buff = pj.get("buff") or {}
        pct_map = buff.get("pct") or {}
        flat_map = buff.get("flat") or {}
        desc = raw_buff.get("desc", "")

        ann_silver = float(buff.get("silver") or 0.0)
        ann_gold = float(buff.get("gold") or 0.0)
        total_silver += ann_silver
        total_gold += ann_gold

        # 【v0.3.10】工程增益的解析只此一处来源：economy.get_province_class_buffs
        buff_strs = [f"{c}+{int(float(v) * 100)}%" for c, v in pct_map.items()]
        buff_strs += [f"{c}+{float(v):.1f}万" for c, v in flat_map.items()]

        project_cards.append({
            "name": name,
            "kind": kind,
            "scope": scope,
            "status": status,
            "desc": desc,
            "damage": damage_of(pj),
            "annual_silver": ann_silver if ann_silver > 0 else None,
            "annual_gold": ann_gold if ann_gold > 0 else None,
            "buff_summary": "，".join(buff_strs) if buff_strs else  desc or "暂无产能修正"
        })

    # 灾害减免：数值与文案都来自修正系统与词条表，不再写散文
    from .modifier_registry import contributions
    from .modifier_terms import MODIFIER_TERMS
    mitigations = []
    for _t in MODIFIER_TERMS:
        _tgt = _t.get("target")
        if _tgt not in ("disaster_penalty", "disaster_chance"):
            continue
        _rows = [r for r in contributions(state, _tgt, pid, kind=_t.get("kind"))
                 if r.get("from") == "工程"] if state else []
        if not _rows:
            continue
        _val = 0.0
        for r in _rows:
            if r.get("op") == "乘法":
                _val += float(r.get("value") or 0.0)
        if not _val:
            continue
        mitigations.append({
            "project": _t["term"],
            "term": _t["term"],
            "effect": "%s%+.0f%%" % (_t["term"], _val * 100),
            "value_pct": round(_val, 6),
            "scope": "本省" if _t.get("area") != "全国" else "全国",
        })

    # 各阶级的百分比/固定增益：调 economy 的唯一实现 本函数不再自己算一遍
    from .economy import get_province_class_buffs
    class_pct_buffs = {}
    class_flat_buffs = {}
    class_buff_notes = {}
    for c in class_pct_buffs_default:
        pct, flat, notes = get_province_class_buffs(loc, c)
        class_pct_buffs[c] = pct
        class_flat_buffs[c] = flat
        class_buff_notes[c] = notes

    return {
        "class_pct_buffs": class_pct_buffs,
        "class_flat_buffs": class_flat_buffs,
        "class_buff_notes": class_buff_notes,
        "project_cards": project_cards,
        "total_annual_silver": round(total_silver, 1),
        "total_annual_gold": round(total_gold, 1),
        "disaster_mitigations": mitigations,
    }


def get_province_movement_modifiers(state: Any, pid: str) -> Dict[str, Any]:
    """5. 交通驿路与省际移动修正。"""
    from .distances import get_province_neighbor_distances, find_shortest_path
    nb_dists = get_province_neighbor_distances(pid, state) if state else []
    path_bj = find_shortest_path(pid, "beizhili", state) if state else {}
    return {
        "neighbors": nb_dists,
        "path_to_beijing": path_bj,
        "base_speed_km_day": 250,
    }


def get_province_supply_demand_modifiers(state: Any, pid: str, loc: Optional[Dict[str, Any]] = None,
                                         index: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """6. 供求平衡与民心民忠变动修正。"""
    if loc is None and state and hasattr(state, "provinces") and pid in state.provinces:
        loc = state.provinces[pid].get("local") or {}
    loc = loc or {}

    from .localdata import supply_demand_ratio, material_production, material_demand
    from .economy import get_supply_status_info

    live = get_province_local_modifiers(state, pid, index) if state else None
    ratio = supply_demand_ratio(loc, live)
    prod = material_production(loc, live)
    dem = material_demand(loc)
    name, desc, attitude_adj, _, _ = get_supply_status_info(ratio)

    return {
        "ratio": ratio,
        "production": prod,
        "demand": dem,
        "status_name": name,
        "status_desc": desc,
        "attitude_drift": attitude_adj,
        "drift_text": f"各阶级态度每月 {attitude_adj:+d} 点 ({name})"
    }


def get_province_class_capacity_summary(state: Any, pid: str, loc: Optional[Dict[str, Any]] = None,
                                        index: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """计算各阶级综合产能修正明细打表 部门项实时取自各部门账，与产能公式同源。"""
    if loc is None and state and hasattr(state, "provinces") and pid in state.provinces:
        loc = state.provinces[pid].get("local") or {}
    loc = loc or {}

    cls_map = loc.get("classes") or {}
    from .economy import PROD_WEIGHTS, DEMAND_WEIGHTS, CLASSES_ORDER, class_capacity, class_demand

    live = get_province_local_modifiers(state, pid, index)
    dept_mods = get_province_dept_modifiers(state, pid, loc, index)
    wea_mods = get_province_weather_modifiers(state, pid, loc)
    proj_mods = get_province_project_modifiers(state, pid, loc)

    liaoxiang_pen = float(live.get("liaoxiang_pct") or 0.0)
    arrears_pen = 0.0            # 欠饷已改为物质绝对扣减 与税负同口径，计入 flat_penalty
    changying_pen = 0.0          # 常盈库积银同上
    disaster_pen = wea_mods["total_disaster_penalty_pct"]
    rel_prod_buff = dept_mods["religion"]["net_prod_pct"]
    live_buff = live.get("buff_pct") or {}
    live_pen = live.get("penalty_pct") or {}
    flat_pen = live.get("flat_penalty") or {}

    res = {}
    for c_name in CLASSES_ORDER:
        c_data = cls_map.get(c_name) or {}
        pop = float(c_data.get("pop") or 0.0)
        pop_exact = int(c_data.get("pop_exact") or round(pop * 10000))

        base_p = PROD_WEIGHTS.get(c_name, 0.0)
        base_d = DEMAND_WEIGHTS.get(c_name, 0.0)

        proj_pct = proj_mods["class_pct_buffs"].get(c_name, 0.0)
        proj_flat = proj_mods["class_flat_buffs"].get(c_name, 0.0)
        proj_notes = proj_mods["class_buff_notes"].get(c_name, [])

        lx_p = 0.0                     # 辽饷已改为物质绝对扣减 见下方 flat_pen
        tax_p = 0.0                    # 十税制同上
        c_flat_pen = float(flat_pen.get(c_name, 0.0))

        # 汇总正负修正 与 class_capacity 同一取数
        total_buff_pct = round(proj_pct + float(live_buff.get(c_name, 0.0)), 4)
        # 天时 灾害/瘟疫/黄汛/地震已登记成产能修正，随 live_pen 一起进来，这里不再加一遍
        total_penalty_pct = round(arrears_pen + changying_pen + float(live_pen.get(c_name, 0.0)), 4)
        net_mult = max(0.0, 1.0 + total_buff_pct - total_penalty_pct)

        cap = class_capacity(c_name, pop, loc, live)
        dem = class_demand(c_name, pop, float(loc.get("modifier_demand_pct") or 0.0))

        # 构建公式解说
        formula_parts = [f"人口{pop:.1f}万 × 基础{base_p}"]
        mult_parts = ["1.0"]
        if proj_pct > 0:
            mult_parts.append (f"工程+{proj_pct * 100:.1f}%")
        live_only_buff = float(live_buff.get(c_name, 0.0)) - rel_prod_buff
        if rel_prod_buff != 0:
            mult_parts.append (f"宗教{rel_prod_buff * 100:+.1f}%")
        if live_only_buff > 0:
            mult_parts.append (f"部门+{live_only_buff * 100:.1f}%")
        elif live_only_buff < 0:
            mult_parts.append (f"部门{live_only_buff * 100:.1f}%")
        if arrears_pen > 0:
            mult_parts.append (f"欠饷-{arrears_pen * 100:.2f}%")
        if changying_pen > 0:
            mult_parts.append (f"常盈库积银-{changying_pen * 100:.4f}%")
        if disaster_pen > 0:
            mult_parts.append (f"灾害-{disaster_pen * 100:.1f}%")

        formula_text = f"{formula_parts[0]} × ({' '.join(mult_parts)})"
        if proj_flat > 0:
            formula_text += f" + 固定{proj_flat:.1f}万"
        if c_flat_pen > 0:
            formula_text += f" − 税负折物质{c_flat_pen:.2f}万单位"
        formula_text += f" = {cap:.1f} 万/月"

        res[c_name] = {
            "class_name": c_name,
            "pop": pop,
            "pop_exact": pop_exact,
            "base_prod": base_p,
            "base_demand": base_d,
            "project_pct_buff": proj_pct,
            "project_flat_buff": proj_flat,
            "project_buff_notes": proj_notes,
            "religion_prod_buff": rel_prod_buff,
            "liaoxiang_penalty": lx_p,
            "tax_penalty": tax_p,
            "tax_matter_loss": round(c_flat_pen, 2),
            "army_arrears_penalty": arrears_pen,
            "army_arrears_matter": round(float(live.get("arrears_wan") or 0.0)
                                          * float(live.get("matter_coef") or 0.0), 2),
            "changying_penalty": changying_pen,
            "disaster_penalty": disaster_pen,
            "total_buff_pct": total_buff_pct,
            "total_penalty_pct": total_penalty_pct,
            "net_multiplier": round(net_mult, 4),
            "capacity": cap,
            "demand": dem,
            "formula_text": formula_text,
            "is_merchant": c_name == "商人"
        }

    return res


def _edu_mod_rows(state, target: str) -> List[Dict[str, Any]]:
    """教育修正行：从统一修正里按词条 target 取（受教育人口年增 / 受高等教育人口年增各一栏）。"""
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


def merge_modifier_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一张修正表里，一个修正项目只许一行——合并按项目保序进行。

    修正行本来是按「修正项目 × 范围」各写一行的 例如 党争工具 有中央那一笔
    「中央腐败+20%」和全国那一笔「地区腐败+2%」，打表时按项目合成一行：
    范围用「、」去重连接，效果用「；」连接，介绍取第一处非空。
    全站只有这一处合并，别处不得再写一遍。
    """
    order: List[str] = []
    merged: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        item = str(r.get("item") or "")
        cur = merged.get(item)
        if cur is None:
            merged[item] = dict(r)
            order.append(item)
            continue
        for key, sep in (("scope", "、"), ("effect", "；")):
            parts: List[str] = []
            for v in (cur.get(key), r.get(key)):
                for piece in str(v or "").split(sep):
                    if piece.strip() and piece not in parts:
                        parts.append(piece)
            cur[key] = sep.join(parts)
        # 合并行要留住双方都有的字段：id 这类标识不能因为先来的那一行没有就被丢掉
        # （例：法律条文带 id、行政纪律不带，同表合并后仍要能按 id 引用带 id 的那一项）
        for k, v in r.items():
            if k in ("scope", "effect"):
                continue
            if not str(cur.get(k) or "").strip() and str(v or "").strip():
                cur[k] = v
    return [merged[i] for i in order]


def discipline_rows() -> List[Dict[str, str]]:
    """吏部行政纪律的修正行：中央项 中央腐败与全国项 地区腐败、人口自然承载力各一行。

    六部面板打出来的表、中央修正表、各省地区修正表都从这一处取，
    保证界面上看得见的和实际生效的是同一份修正。
    同一张表里同名项目由 merge_modifier_rows() 合成一行 面板那边会合。
    """
    from .depts_libu import ADMIN_DISCIPLINE_BUFFS

    rows: List[Dict[str, str]] = []
    for b in ADMIN_DISCIPLINE_BUFFS:
        name = b.get("name", "")
        intro = b.get("desc", "")
        if float(b.get("central_corruption_pct") or 0.0) != 0.0:
            rows.append({"item": name, "scope": "中央",
                         "effect": "腐败程度%s" % b.get("central_desc", ""), "intro": intro})
        if float(b.get("local_corruption_pct") or 0.0) != 0.0:
            rows.append({"item": name, "scope": "全国",
                         "effect": "地区腐败程度%s" % b.get("local_desc", ""), "intro": intro})
        if float(b.get("carrying_pct") or 0.0) != 0.0:
            rows.append({"item": name, "scope": "全国",
                         "effect": "人口自然承载力%s" % b.get("carrying_desc", ""), "intro": intro})
    return rows


def effect_text(eff: Dict[str, Any]) -> str:
    """修正效果那一栏的文案：词条名 + 变化幅度，由词条表与数值现算（全站唯一一处）。"""
    from .modifier_terms import MODIFIER_TERMS, PROVINCE_PLACEHOLDER

    target = eff.get("target")
    term = ""
    for t in MODIFIER_TERMS:
        if t["target"] != target:
            continue
        if PROVINCE_PLACEHOLDER in t["term"]:
            p_name = eff.get("province_name") or ""
            if not p_name:
                continue
            term = t["term"].replace(PROVINCE_PLACEHOLDER, p_name)
        else:
            term = t["term"]
        break
    if not term:
        return eff.get("desc", "")
    if target in ("silver_flow", "gold_flow"):
        return "%s%g万两/年" % (term, float(eff.get("annual_wan") or 0.0))
    if eff.get("delta_pct") is not None:
        return "%s%+.2f%%" % (term, float(eff.get("delta_pct") or 0.0) * 100.0)
    delta = float(eff.get("delta") or 0.0)
    if eff.get("is_pct"):
        return "%s%+.2f%%" % (term, delta)
    return "%s%+g点" % (term, delta)


def department_modifier_rows(state: Any, location: str) -> List[Dict[str, Any]]:
    """某个界面里的修正行：修正只有一个物种，按显示位置筛出来就是该界面的修正表。"""
    rows: List[Dict[str, Any]] = []
    for dmod in (getattr(state, "department_modifiers", None) or {}).values():
        loc = str(dmod.get("location") or dmod.get("dept") or "")
        if loc != location:
            continue
        effs = [e for e in (dmod.get("effects") or []) if e]
        if not effs:
            continue
        rows.append({
            "item": dmod.get("name", ""),
            "scope": dmod.get("scope") or "全国",
            "effect": "；".join(effect_text(e) for e in effs),
            "intro": dmod.get("intro") or dmod.get("content_str") or dmod.get("summary") or "",
        })
    return rows


def law_rows() -> List[Dict[str, str]]:
    """刑部现行法律的修正行：中央项 中央腐败与全国项 地区腐败各一行。"""
    from .depts_xingbu import ACTIVE_LAWS

    rows: List[Dict[str, str]] = []
    for l in ACTIVE_LAWS:
        name = l.get("name", "")
        intro = l.get("desc", "")
        cp = float(l.get("central_corruption_pct") or 0.0)
        if cp != 0.0:
            rows.append({"id": l.get("id", ""), "item": name, "scope": "中央",
                         "effect": "中央腐败程度%+.2f%%" % (cp * 100), "intro": intro})
        lp = float(l.get("local_corruption_pct") or 0.0)
        if lp != 0.0:
            rows.append({"id": l.get("id", ""), "item": name, "scope": "全国",
                         "effect": "全国所有地方腐败程度%+.2f%%" % (lp * 100), "intro": intro})
    return rows


def discipline_law_rows() -> List[Dict[str, str]]:
    """行政纪律 + 现行法律的全部修正行：中央表取中央那一半，地方表取全国那一半。"""
    return discipline_rows() + law_rows()


def get_central_modifier_rows(state: Any) -> List[Dict[str, Any]]:
    """中央范围 scope == "中央"的修正条目：修正项目 / 修正范围 / 修正效果。

    与地区修正表同一套结构与同一处范围判定 operations._SCOPE_OF_TARGET，
    只是取中央那一半——供主界面上方各数据项的【ⓘ】动态显示 中央腐败、皇室威望、替代率 f。
    范围＝全国 的条目不在这里：全国 = 当前明属控制的省份，它们写在各省的地区修正表里。
    """
    from .operations import _SCOPE_OF_TARGET

    rows: List[Dict[str, Any]] = []

    def _central_effect(t: str, eff: Dict[str, Any]) -> str:
        delta = float(eff.get("delta") or 0.0)
        is_pct = bool(eff.get("is_pct"))
        sign = f"{delta:+.0f}%" if is_pct else f"{delta:+.1f}点"
        if t == "central_corruption":
            return f"腐败程度{sign}"
        if t == "prestige":
            return f"皇室威望{sign}"
        if t == "financial_rate":
            return f"替代率f{float(eff.get('delta') or 0.0) * 100:+.1f}%"
        return eff.get("desc", "")

    rows.extend(r for r in discipline_law_rows() if r["scope"] == "中央")
    for dmod in state.department_modifiers.values():
        for eff in (dmod.get("effects") or []):   # 【v0.3.7】脏数据 effects 为 None不得让取数崩
            t = eff.get("target")
            if t not in _SCOPE_OF_TARGET or _SCOPE_OF_TARGET[t] != "中央":
                continue
            rows.append({"item": dmod.get("name", ""), "scope": "中央",
                         "effect": _central_effect(t, eff),
                         "intro": dmod.get("content_str") or dmod.get("summary") or "超控设立的部门修正"})

    # 礼部科举的教育人口修正：两套各管一栏，都进中央修正表 修正都要看得见
    for key, label in (("edu_growth", "受教育人口年增"), ("higher_edu_growth", "受高等教育人口年增")):
        for m in _edu_mod_rows(state, key):
            pct = float(m.get("pct_mod") or 0.0)
            flat = float(m.get("flat_mod") or 0.0)
            if pct == 0.0 and flat == 0.0:
                continue
            parts = []
            if pct:
                parts.append("%s %+.1f%%" % (label, pct * 100))
            if flat:
                parts.append ("年增加减 %+.2f 万" % flat)
            rows.append({"item": m.get("name", ""), "scope": "中央",
                         "effect": "，".join(parts),
                         "intro": m.get("desc", "")})
    # 同一张表里一个修正项目只许一行 中央表也要收口
    return merge_modifier_rows(rows)


def get_province_all_modifiers(state: Any, pid: str) -> Dict[str, Any]:
    """统一获取指定省份的全部规整地区修正数据 综合打包。"""
    loc = None
    p_name = pid
    if state and hasattr(state, "provinces") and pid in state.provinces:
        p = state.provinces[pid]
        p_name = p.get("name", pid)
        loc = p.get("local") or {}

    index = build_local_modifier_index(state) if state else {}
    live = get_province_local_modifiers(state, pid, index)

    mil = get_province_military_modifiers(state, pid, loc, index)
    dept = get_province_dept_modifiers(state, pid, loc, index)
    weather = get_province_weather_modifiers(state, pid, loc)
    proj = get_province_project_modifiers(state, pid, loc)
    movement = get_province_movement_modifiers(state, pid)
    supply_demand = get_province_supply_demand_modifiers(state, pid, loc, index)
    classes_summary = get_province_class_capacity_summary(state, pid, loc, index)

    # 汇总标签条
    summary_badges = []
    if mil["liaoxiang"]["active"]:
        summary_badges.append({"label": f"辽饷折物质 -{mil['liaoxiang']['matter_loss']:.2f} 万单位/月", "type": "negative"})
    if mil["army_arrears"]["active"]:
        summary_badges.append({"label": f"欠饷折物质 -{mil['army_arrears']['matter_loss']:.2f} 万单位/月", "type": "negative"})
    if float(live.get("changying_monthly_wan") or 0.0) > 0:
        summary_badges.append({"label": f"常盈库积银 -{float(live['changying_monthly_wan']):.4f}万两/月折物质", "type": "negative"})
    if dept["corruption"]["value"] > 50:
        summary_badges.append({"label": f"吏治腐败 {dept['corruption']['value']:.1f}", "type": "warning"})
    if dept["religion"]["net_prod_pct"] != 0:
        summary_badges.append({"label": f"宗教产能 {dept['religion']['net_prod_pct_display']}", "type": "positive"})
    if weather["total_disaster_penalty_pct"] > 0:
        summary_badges.append({"label": f"灾害减产 -{weather['total_disaster_penalty_pct']*100:.1f}%", "type": "negative"})
    for m in proj["disaster_mitigations"]:
        summary_badges.append({"label": f"防灾减免: {m['project']}", "type": "positive"})

    # 地区修正表：修正项目 / 修正范围 / 修正效果，一条一行
    # 范围＝中央 的条目不进本表 它们在主界面各数据项的【ⓘ】里，见 get_central_modifier_rows；
    # 范围＝全国 的条目**写进地方表** —— 全国 = 当前明属控制的省份，故只写进明属控制省的名单，
    # 外邦省份 盛京、海西、嫩科尔沁等没有明的政纪法律，一条都不写。
    from .operations import _SCOPE_OF_TARGET
    from .economy import CLASSES_ORDER

    rows: List[Dict[str, Any]] = []
    # 全国 = 当前属于明朝控制的省份：看**控制权** local.controller，不看核心 local.core
    from . import localdata as _ld
    p_controlled = _ld.under_ming_control(p) if p else False

    def _clauses_amount(amounts: Dict[str, float]) -> str:
        return "，".join(f"{c}产能-{v:,.2f}万单位"
                         for c, v in sorted(amounts.items(), key=lambda kv: -kv[1]) if v > 0)

    def _clauses_pct(pcts: Dict[str, float]) -> str:
        return "，".join(f"{c}产能{v * 100:+.2f}%"
                         for c, v in pcts.items() if v != 0.0)

    # 1) 吏部行政纪律 + 2) 刑部现行法律：只要范围＝全国的那一半，逐省写进明属控制省。
    #    行由 discipline_law_rows() 一处生成，六部面板打出来的表就是这一份。
    if p_controlled:
        rows.extend(r for r in discipline_law_rows() if r["scope"] == "全国")

    # 3) 超控设立的部门修正：范围由 target 判定；中央不进本表；别省的不进本表
    for dmod in state.department_modifiers.values():
        for eff in (dmod.get("effects") or []):   # 【v0.3.7】脏数据 effects 为 None不得让取数崩
            t = eff.get("target")
            if t not in _SCOPE_OF_TARGET:
                continue
            scope = _SCOPE_OF_TARGET[t]
            if scope is None:                       # prov_corruption → 该省
                scope = eff.get("province_name", p_name)
            if scope == "中央":
                continue
            if scope == "全国" and not p_controlled:   # 全国 = 明属控制省
                continue
            if scope not in  ("全国", p_name):
                continue
            delta = float(eff.get("delta") or 0.0)
            is_pct = bool(eff.get("is_pct"))
            sign = f"{delta:+.0f}%" if is_pct else f"{delta:+.1f}点"
            cls_txt = "、".join(eff.get("classes") or [])
            if t == "capacity":
                effect = f"{cls_txt}产能{float(eff.get('delta_pct') or 0.0) * 100:+.2f}%"
            elif t in ("all_local_corruption", "prov_corruption"):
                effect = f"地区腐败程度{sign}"
            elif t == "attitude":
                effect = f"{cls_txt}态度{sign}"
            elif t == "prestige":
                effect = f"皇室威望{sign}"
            elif t == "financial_rate":
                effect = f"替代率f{float(eff.get('delta') or 0.0) * 100:+.1f}%"
            else:
                effect = eff.get("desc", "")
            rows.append({"item": dmod.get("name", ""),
                         "scope": scope or dmod.get("scope") or "", "effect": effect,
                         "intro": dmod.get("intro") or dmod.get("content_str") or dmod.get("summary") or ""})

    # 4) 超控设立的独立地区修正
    for cm in (loc.get("custom_modifiers") or []):
        pct = float(cm.get("effect_pct") or 0.0)
        cls = _classes_of("、".join(cm.get("affected_classes") or [])) or list(CLASSES_ORDER)
        rows.append({"item": cm.get("name", ""), "scope": p_name or cm.get("scope") or "",
                     "effect": _clauses_pct({c: pct for c in cls}),
                     "intro": cm.get("intro") or cm.get("note", "")})

    # 5) 部门实时项：辽饷/欠饷/十税/常盈库＝本省；宗教＝全国 明属控制省才写
    for it in live.get("items", []):
        amounts = it.get("class_amounts") or {}
        pcts = it.get("class_pct") or {}
        if it.get("effect_text"):
            # 展示型条目：效果文字由数据源现算 如宗教的需求与威望，不进产能计算
            effect = it["effect_text"]
        elif amounts:
            effect = _clauses_amount(amounts)
        elif pcts:
            effect = _clauses_pct(pcts)
        else:
            continue
        is_national = it.get("scope") == "全国" or it.get("dept") == "礼部"
        if is_national and not p_controlled:
            continue
        rows.append({"item": it["name"],
                     "scope": "全国" if is_national else p_name,
                     "effect": effect,
                     "intro": it.get("detail", "")})

    # 6) 天时 灾害/瘟疫/黄汛/地震
    for key in ("disaster", "plague", "yellow_flood", "earthquake"):
        item = weather.get(key) or {}
        if not item.get("active"):
            continue
        pen = float(item.get("penalty_pct") or item.get("current_penalty_pct") or 0.0)
        rows.append({"item": item.get("name", ""), "scope": p_name,
                     "effect": _clauses_pct({c: -pen for c in CLASSES_ORDER}),
                     "intro": "天时不顺，全省各阶级产能下降，收成受损"})

    # 工程修正行 地方工程：修正项目 / 修正范围 / 修正效果，按五类子栏分组
    from .depts_works import get_province_project_rows
    project_rows = get_province_project_rows(state, pid)

    return {
        "province_id": pid,
        "province_name": p_name,
        "summary_badges": summary_badges,
        "modifier_rows": merge_modifier_rows(rows),
        "project_rows": project_rows,
        "department_items": live.get("items", []),
        "military": mil,
        "departments": dept,
        "weather_disaster": weather,
        "projects_and_mines": proj,
        "movement": movement,
        "supply_demand": supply_demand,
        "classes_summary": classes_summary,
    }
