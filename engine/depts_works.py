"""工部专业模块：工程建设、器物制造、水利管理。

功能规范：
1. 工程建设：统计全国工程 国家建筑、军事建筑、工程建筑、灾害建筑、矿场五类，核算月度维护费用。
   年维护费不是存档字段，按「造价 × 1%」现算。
2. 器物制造：军器军械规格造价与产地外贸说明台账。
3. 水利管理：统计特设防灾水利工程 灵州黄河石堤、海宁海塘等与维护费用。
"""

from typing import Any, Dict, List, Tuple

# 军械制造目录库与单价 两白银
ARMAMENT_CATALOG: Dict[str, Dict[str, Any]] = {
    # ── 甲胄 ──
    "armor_bumian": {
        "id": "armor_bumian",
        "category": "armors",
        "name": "万历十三年式布面重铁甲",
        "unit_cost": 12.0,
        "monthly_capacity": 500,
        "unit": "领",
        "monthly_cost": 0.60,
        "foreign": False,
        "desc": "青布里衬冷锻重铁叶，防护重箭与劈砍，为九边主力制式战甲。"
    },
    "armor_zhajia": {
        "id": "armor_zhajia",
        "category": "armors",
        "name": "天启元年式冷锻铁叶札甲",
        "unit_cost": 20.0,
        "monthly_capacity": 200,
        "unit": "领",
        "monthly_cost": 0.40,
        "foreign": False,
        "desc": "精炼熟铁冷锻鱼鳞札，骑兵冲阵破甲首选，造价昂贵。"
    },
    "armor_suozi": {
        "id": "armor_suozi",
        "category": "armors",
        "name": "万历三十五年式精铁锁子连环甲",
        "unit_cost": 15.0,
        "monthly_capacity": 300,
        "unit": "领",
        "monthly_cost": 0.45,
        "foreign": False,
        "desc": "细密铁环穿套，轻便坚韧，利于山地步战与近身缠斗。"
    },
    "armor_tengjia": {
        "id": "armor_tengjia",
        "category": "armors",
        "name": "万历元年式柳条齐头软藤甲",
        "unit_cost": 3.0,
        "monthly_capacity": 1000,
        "unit": "副",
        "monthly_cost": 0.30,
        "foreign": False,
        "desc": "浸油轻藤编制，轻便涉水，防刀剑滑刺，造价低廉。"
    },

    # ── 近兵器 ──
    "melee_wo_dao": {
        "id": "melee_wo_dao",
        "category": "melee",
        "name": "嘉靖三十四年式戚家倭刀",
        "unit_cost": 1.8,
        "monthly_capacity": 1200,
        "unit": "口",
        "monthly_cost": 0.22,
        "foreign": False,
        "desc": "双手弧刃长刀，戚少保抗倭名器，破阵劈砍威力极大。"
    },
    "melee_chang_qiang": {
        "id": "melee_chang_qiang",
        "category": "melee",
        "name": "隆庆元年式丈八透甲长枪",
        "unit_cost": 0.8,
        "monthly_capacity": 2500,
        "unit": "杆",
        "monthly_cost": 0.20,
        "foreign": False,
        "desc": "稠木杆配四棱透甲枪头，步兵大阵拒骑兵制式长兵。"
    },
    "melee_zhan_ma": {
        "id": "melee_zhan_ma",
        "category": "melee",
        "name": "洪武二十六年式长柄斩马刀",
        "unit_cost": 2.5,
        "monthly_capacity": 400,
        "unit": "柄",
        "monthly_cost": 0.10,
        "foreign": False,
        "desc": "重柄阔锋，步卒专斩敌骑马足之利器。"
    },
    "melee_yan_ling": {
        "id": "melee_yan_ling",
        "category": "melee",
        "name": "万历四十二年式柳叶透甲雁翎刀",
        "unit_cost": 1.5,
        "monthly_capacity": 1500,
        "unit": "把",
        "monthly_cost": 0.23,
        "foreign": False,
        "desc": "官军制式腰刀，平直带仰弧，利于突刺与劈杀。"
    },

    # ── 远兵器 ──
    "ranged_lumi": {
        "id": "ranged_lumi",
        "category": "ranged",
        "name": "万历二十六年式鲁密铳",
        "unit_cost": 6.5,
        "monthly_capacity": 300,
        "unit": "杆",
        "monthly_cost": 0.20,
        "foreign": False,
        "desc": "鲁密国传入长铳，枪管精长，后附铜照门，百步之内洞穿重甲。"
    },
    "ranged_niaozui": {
        "id": "ranged_niaozui",
        "category": "ranged",
        "name": "嘉靖三十七年式鸟嘴铳",
        "unit_cost": 4.2,
        "monthly_capacity": 600,
        "unit": "杆",
        "monthly_cost": 0.25,
        "foreign": False,
        "desc": "龙头火绳步铳，神机营与边军主战远射火器。"
    },
    "ranged_sanyan": {
        "id": "ranged_sanyan",
        "category": "ranged",
        "name": "嘉靖四十四年式精铁三眼火铳",
        "unit_cost": 3.0,
        "monthly_capacity": 800,
        "unit": "杆",
        "monthly_cost": 0.24,
        "foreign": False,
        "desc": "三管分射，迅疾猛烈，射毕翻转铁铳作铁槌破敌。"
    },
    "ranged_kaiyuan_bow": {
        "id": "ranged_kaiyuan_bow",
        "category": "ranged",
        "name": "永乐二十年式开元柘木硬弓",
        "unit_cost": 2.0,
        "monthly_capacity": 1000,
        "unit": "张",
        "monthly_cost": 0.20,
        "foreign": False,
        "desc": "柘木角梢大弓，射透甲箭，精兵常备。"
    },
    "ranged_flintlock_foreign": {
        "id": "ranged_flintlock_foreign",
        "category": "ranged",
        "name": "吕宋自生火铳",
        "unit_cost": 12.0,
        "monthly_capacity": 150,
        "unit": "杆",
        "monthly_cost": 0.18,
        "foreign": True,
        "desc": "[西法外贸/泰西自生燧发铳] 燧石击发无火绳，风雨不熄，自南洋外贸舶来。"
    },

    # ── 火炮 ──
    "artillery_hongyi_foreign": {
        "id": "artillery_hongyi_foreign",
        "category": "artillery",
        "name": "天启四年造三千斤红夷大炮",
        "unit_cost": 350.0,
        "monthly_capacity": 6,
        "unit": "门",
        "monthly_cost": 0.21,
        "foreign": True,
        "desc": "[澳门铸炮厂/西洋外贸] 二十四磅重型攻守城巨炮，轰城摧垒，宁远大捷之主炮。"
    },
    "artillery_folangji": {
        "id": "artillery_folangji",
        "category": "artillery",
        "name": "嘉靖二年造五百斤二号佛郎机",
        "unit_cost": 65.0,
        "monthly_capacity": 30,
        "unit": "门",
        "monthly_cost": 0.20,
        "foreign": False,
        "desc": "九副子铳速射火炮，射速极快，车营野战与关隘防守利器。"
    },
    "artillery_hudun": {
        "id": "artillery_hudun",
        "category": "artillery",
        "name": "嘉靖二十三年造三十斤虎蹲炮",
        "unit_cost": 15.0,
        "monthly_capacity": 120,
        "unit": "门",
        "monthly_cost": 0.18,
        "foreign": False,
        "desc": "轻型大口径野战霰炮，附铁爪定地，利于山地泥泞野战。"
    },
    "artillery_mielu": {
        "id": "artillery_mielu",
        "category": "artillery",
        "name": "万历三十年造六十斤灭虏炮",
        "unit_cost": 22.0,
        "monthly_capacity": 60,
        "unit": "门",
        "monthly_cost": 0.13,
        "foreign": False,
        "desc": "中型野战步兵平射炮，弹重三斤，兼具破车破马之威。"
    },

}


# ── 工部默认开局军械仓储库存 ──
INITIAL_WORKS_INVENTORY = {
    "万历四十七年式白椴木叶札甲": 3500,
    "万历十三年布面铜钉甲": 3000,
    "天启三年式藤牌锁子甲": 1500,
    "万历二十年式白蜡杆长矛": 6000,
    "嘉靖三十四年式戚家倭刀": 4000,
    "万历二十五年式单刃解首短刀": 8000,
    "永乐二十年式开元柘木硬弓": 4000,
    "万历二十六年式鲁密铳": 2000,
    "万历四十年式自生火弩": 1200,
    "嘉靖二十三年造三十斤虎蹲炮": 50,
    "万历三年造四十五斤旋风小炮": 35,
}


def tick_works_production_monthly(state: Any) -> List[str]:
    """工部军械器物月度制造结算：
    从国库扣除各军械条目的月度制造成本 ARMAMENT_CATALOG 中各项目的 monthly_cost，
    并向工部仓储库存 state.works_inventory按月度产能 monthly_capacity增加对应装备。
    若国库存银不足以全额支持全部军械制造，则按国库可用资金比例缩减生产并入库。
    """
    notes = []
    if state is None:
        return notes

    inventory = get_works_inventory(state)
    active_items = [item for item in ARMAMENT_CATALOG.values() if item.get("monthly_capacity", 0) > 0 and item.get("monthly_cost", 0) > 0]
    total_cost_need = sum(float(item["monthly_cost"]) for item in active_items)

    if total_cost_need <= 0:
        return notes

    curr_treasury = float(getattr(state, "treasury", 0.0))
    actual_cost = min(curr_treasury, total_cost_need)
    fund_ratio = (actual_cost / total_cost_need) if total_cost_need > 0 else 0.0

    state.treasury = round(max(0.0, curr_treasury - actual_cost), 2)

    from .operations import _record_national_tx
    _record_national_tx(state, "支出", actual_cost, "军械制造", f"工部军械营造月银支出 {actual_cost:.2f}万两 预算 {total_cost_need:.2f}万两")

    produced_summary = []
    for item in active_items:
        name = item["name"]
        cap = int(item["monthly_capacity"])
        produced = int(round(cap * fund_ratio)) if fund_ratio < 1.0 else cap
        if produced > 0:
            inventory[name] = inventory.get(name, 0) + produced
            produced_summary.append(f"{name}+{produced}{item.get('unit', '件')}")

    if fund_ratio >= 1.0:
        notes.append(f"工部军械器物制造：国库如额支发 {actual_cost:.2f}万两，已生产并入库各类军器军需 {len(produced_summary)} 项。")
    else:
        notes.append(f"工部军械器物制造：国库实拨 {actual_cost:.2f}万两 缺额 {total_cost_need - actual_cost:.2f}万两，军器制造按 {fund_ratio * 100:.1f}% 缩减投产。")

    return notes


def get_works_inventory(state=None) -> Dict[str, int]:
    """获取工部军械军需仓储库存 state.works_inventory，开局由 WorldState 声明。"""
    if state is not None:
        return state.works_inventory
    return dict(INITIAL_WORKS_INVENTORY)


# ── 工程分类 唯一一处：建筑类型就是这五类 ──
# 界面 地方工程表、工部工程表与账目共用这一套名字。
# 【v0.5.0】原「水利建筑」改为「灾害建筑」：凡作用于气候的工程都归这一类，
# 不再有单独的「水利」类别，工部也不再单列一张「水利管理」表。
PROJECT_CLASSES: List[Tuple[str, str]] = [
     ("国家建筑", "国家建筑"),
     ("军事建筑", "军事建筑"),
     ("工程建筑", "工程建筑"),
     ("灾害建筑", "灾害建筑"),
     ("矿场", "矿场"),
]
PROJECT_CLASS_NAMES: Dict[str, str] = dict(PROJECT_CLASSES)


def project_class(pj: Dict[str, Any]) -> str:
    """工程子类：建筑类型 五类之一就是分类，旧写法按工种兜底。

    作用于气候的旧写法 水利/运河/海塘/堤一律归「灾害建筑」。
    """
    kind = (pj or {}).get("kind") or ""
    if kind in PROJECT_CLASS_NAMES:
        return kind
    if kind in  ("水利", "运河", "海塘", "堤"):
        return "灾害建筑"
    if kind.endswith ("矿" or kind == "盐井"):
        return "矿场"
    if kind in  ("要塞", "边墙"):
        return "军事建筑"
    if kind in  ("宫殿", "陵寝", "城垣"):
        return "国家建筑"
    return "工程建筑"


def annual_maintenance_wan(pj: Dict[str, Any]) -> float:
    """年维护费 = 造价 × 1%。"""
    return round(float((pj or {}).get("cost") or 0.0) * 0.01, 4)


def monthly_maintenance_wan(pj: Dict[str, Any]) -> float:
    """月维护支给 = 年维护费 ÷ 12。"""
    return round(annual_maintenance_wan(pj) / 12.0, 4)


def tick_maintenance_monthly(state) -> List[str]:
    """【v0.3.4】月度维护支给结算：各省运营中工程的年维护费 造价 × 1%÷ 12，
    自国库转入地方银库 与营建月款同一走向。原来只有显示、从不扣款，属漏结算。
    """
    from .localdata import under_ming_control
    notes: List[str] = []
    total = 0.0
    for pid, p in state.provinces.items():
        if not under_ming_control(p):
            continue
        loc = p.get("local") or {}
        for pj in loc.get("projects") or []:
            if str(pj.get("status") or "运营中") != "运营中":     # 未完工的不计维护
                continue
            total += monthly_maintenance_wan(pj)
    total = round(total, 4)
    if total <= 0:
        return notes
    actual = min(float(getattr(state, "treasury", 0.0)), total)
    state.treasury = round(max(0.0, float(state.treasury) - actual), 2)
    state.local_reserve = round(float(getattr(state, "local_reserve", 0.0)) + actual, 2)
    if actual < total:
        notes.append("工部维护：应支 %.4f万两，国库不足，实支 %.4f万两入地方银库。" % (total, actual))
    else:
        notes.append ("工部维护：全国运营中工程月维护支给 %.4f万两，自国库拨入地方银库。" % total)
    return notes


def _plain(text: str) -> str:
    """显示用纯文本：去掉括号 内容保留。"""
    s = str(text or "")
    return s.replace(" ", " ").replace("", "").replace("(", " ").replace(")", "").strip()


def project_effect_text(pj: Dict[str, Any]) -> str:
    """工程的「修正效果」列文本：一律由词条与数值拼出，不写散文。

    - 产能/白银/黄金：走既有词条口径（[阶级]产能、白银、黄金）；
    - 灾害类：读 buff.disaster / buff.disaster_chance，键是灾害种类，
      文案取词条表里的词条名（小涝导致的产能损失、黄汛发生概率……）；
    - 损毁：读损毁程度，按「工程损毁程度」这条词条写。
    """
    from .modifier_terms import MODIFIER_TERMS
    from .projects import damage_of
    buff = pj.get("buff") or {}
    parts: List[str] = []
    for c, v in (buff.get("pct") or {}).items():
        if float(v):
            parts.append("%s产能%+.0f%%" % (c, float(v) * 100))
    for c, v in (buff.get("flat") or {}).items():
        if float(v):
            parts.append("%s产能%+.0f万单位/月" % (c, float(v)))
    if buff.get("silver"):
        parts.append("白银 %g 万两/年" % float(buff["silver"]))
    if buff.get("gold"):
        parts.append("黄金 %g 两/年" % float(buff["gold"]))
    term_by = {(t.get("target"), t.get("kind")): t["term"] for t in MODIFIER_TERMS}
    for field, target in (("disaster", "disaster_penalty"),
                          ("disaster_chance", "disaster_chance")):
        for kind, val in (buff.get(field) or {}).items():
            name = term_by.get((target, kind)) or kind
            parts.append("%s%+.0f%%" % (name, float(val) * 100))
    dmg = damage_of(pj)
    if dmg > 0:
        parts.append("工程损毁程度%+.0f%%" % (dmg * 100))
    if parts:
        return "，".join(parts)
    return _plain(buff.get("desc") or pj.get("kind") or "")


def project_row(p: Dict[str, Any], pj: Dict[str, Any]) -> Dict[str, Any]:
    """一条工程的表格行 —— **工部工程建设表与地方工程表共用这一份构造**。

    列 玩家定义，八列：修正项目 / 修正范围 / 修正效果 / 修正介绍 /
    建筑类型 / 工程状态 / 造价 万两 / 年维护费 万两。
    地方详情不再自己拼行，直接从本表的同一份数据里提取。
    """
    from .distances import PROVINCE_NAMES

    pid = p.get("id", "")
    p_name = p.get("name") or PROVINCE_NAMES.get(pid, pid)
    pj = pj or {}
    buff = pj.get("buff") or {}
    cls = project_class(pj)
    scope = p_name if str(pj.get("scope") or "本省") == "本省" else str(pj["scope"])
    return {
        "item": pj.get("name", ""),
        "scope": scope,
        "effect": project_effect_text(pj),
        "intro": _plain(buff.get("desc") or "") or "工程落成后按其修正生效",
        "kind": cls,
        "kind_name": PROJECT_CLASS_NAMES.get(cls, "其他建筑"),
        "status": pj.get("status", "运营中"),
        "cost": float(pj.get("cost") or 0.0),
        "maintenance_annual": annual_maintenance_wan(pj),
        "maintenance_monthly": monthly_maintenance_wan(pj),
        # 附加字段：前端分组与定位用，不属于八列本身
        "subclass": cls,
        "subclass_name": PROJECT_CLASS_NAMES.get(cls, "其他建筑"),
        "province_id": pid,
        "province_name": p_name,
        "annual_silver": buff.get("silver"),
        "annual_gold": buff.get("gold"),
        "desc": buff.get("desc", ""),
    }


def get_province_project_rows(state, pid: str) -> List[Dict[str, Any]]:
    """本省工程的表格行：与工部工程建设表同一份构造 八列。

    范围写它实际影响的地方 本省工程＝省名，如灵州黄河石堤＝全国；
    有产能增益的写增益，矿场写年产出，其余写工程介绍。
    """
    p = (state.provinces.get(pid) if state and hasattr(state, "provinces") else None) or {}
    loc = p.get("local") or {}
    return [project_row(p, pj) for pj in (loc.get("projects") or [])]


def get_works_overview(state) -> Dict[str, Any]:
    """工部汇总视图数据。

    【v0.5.0】只有一张工程建设表：原「水利管理」表已并入，其类别改称「灾害建筑」。
    每行是八列 见 project_row，地方详情从同一份数据里提取。
    """
    inventory = get_works_inventory(state)
    projects: List[Dict[str, Any]] = []
    total_maint = 0.0

    from .localdata import under_ming_control
    for pid, p in state.provinces.items():
        if not under_ming_control(p):      # 当前属明朝控制的省份 看控制权
            continue
        for pj in ((p.get("local") or {}).get("projects") or []):
            row = project_row(p, pj)
            total_maint += float(row["maintenance_monthly"])
            projects.append(row)

    return {
        "engineering": {
            "total_projects": len(projects),
            "monthly_maintenance": round(total_maint, 2),
            "projects": projects,
        },
        "inventory": inventory,
        "armaments": list(ARMAMENT_CATALOG.values()),
    }
