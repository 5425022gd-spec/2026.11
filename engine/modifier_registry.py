"""修正的唯一登记处与唯一公式。

口径 玩家定义，本模块是唯一实现处：

1. **一个修正** = 名称 + 效果列表 + 介绍。它是个独立对象：
   - `from` 只说明它挂在哪个界面 显示位置，**不决定它影响谁**；
   - 每条效果各自打一个系统 `target`，互不相干；
   - 每个被影响的系统，都要在自己的面板里把影响它的修正连数值一起列出来
      样板：政治点数面板列「基础值 2.00 / 自由女神像 +2% / 稳定度 +8% / 大萧条 -1.00」。
2. **唯一公式** 全站只有这一处实现：

       值 = 基础值 ×  1 + Σ乘法修正 + Σ加法修正

   例：基础值 1，乘法 +1% +2% +3%，加法 +1 +2 +3 → 1 × (1 + 0.06) + 6 = 7.06。
3. **数据在 `world/modifiers.json`** 静态修正，运行时登记的修正 超控部门修正、科技效果、
   天时灾害、宗教、主线的地区修正等走各自的 provider 登记进同一个收集口
   `contributions()`，**不自己算值**。
4. **建筑 工程是特殊修正，单独处理**：它的数据仍在 `world/provinces.json` 的 `buff` 里，
   按 `pct` / `flat` / `silver` / `gold` / 气候字段各自生效，不进本文件、也不走 `contributions()`。
"""

import json
import os
from typing import Any, Callable, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODIFIERS_FILE = os.path.join(_BASE, "world", "modifiers.json")

# 两种运算 别的写法一律报错，不做兼容
OP_MUL = "乘法"
OP_ADD = "加法"
OPS = (OP_MUL, OP_ADD)

FORMULA_TEXT = "值 = 基础值 ×  1 + Σ乘法修正 + Σ加法修正"

# 全部可被修正的目标 **唯一词表，本模块独占**。
# tech_effects 从本模块取这张表 科技效果与修正用同一套 target 名字。
# mechanism＝这个数值**为什么会动** 的一段话 界面 ⓘ 的「机制」那一批就念它，
# 与「修正」breakdown、「计算方式」formula 合成同一个数值说明，界面不再自己编。
TARGETS: Dict[str, Dict[str, str]] = {
    "financial_rate": {"name": "金融替代率", "unit": "%",
                       "where": "state.financial_rate recompute_financial_rate",
                       "mechanism": "钱庄票号越发达、汇票周转越快，替代率越高；白银短缺与钱法败坏会压低它。"},
    "capacity": {"name": "全国所有阶级产能", "unit": "%",
                 "where": "economy.class_capacity 建筑部分单独处理",
                 "mechanism": "各阶级按人口与技术水平出基础产能，朝廷政令、宗教与超控修正按唯一公式增减；建筑与工程单独处理，不进这张表。"},
    "carrying_pct": {"name": "全国承载力", "unit": "%",
                     "where": "depts_libu.calc_local_carrying",
                     "mechanism": "土地与水利决定基础承载力，腐败、灾异、驻军欠饷与朝廷政纪修正按唯一公式增减。"
                                  "月度演化：ΔN = |N−D| / N × sgn(P−D) × 3.0 × ln(1 + |P−D| / 3.0)，"
                                  "其中 N 为本省承载力、D 为本省物质总需求、P 为本省物质产出。"
                                  "需求高于承载力时记下超载率，次月按超载率的平方乘五百扣减产能。"},
    "prestige": {"name": "皇室威望", "unit": "%", "where": "depts_rites.calc_prestige",
                 "mechanism": "祭祀、战功与恩赏累积威望，灾异、败仗与失政折损；各教影响按影响力折算成一条乘法修正。"},
    "central_corruption": {"name": "中央腐败程度", "unit": "%",
                           "where": "depts_libu.calc_central_corruption",
                           "mechanism": "京官贪墨与党争推高中央腐败，考成、京察与律法整肃压低它；超控设立的部门修正直接进这张表。"},
    "local_corruption": {"name": "地区腐败程度", "unit": "%",
                         "where": "depts_libu.calc_local_corruption",
                         "mechanism": "地方腐败由本省吏治与税负起算，朝廷六部政纪修正只作用于明属控制省；非明控省份用原始值，不适用这套修正。"},
    "population_birth": {"name": "人口出生率", "unit": "%", "where": "economy.population_rates",
                         "mechanism": "粮食充裕、天下太平则出生率上浮，灾异、疫病与战乱压低下浮。"},
    "population_death": {"name": "人口死亡率", "unit": "%", "where": "economy.population_rates",
                         "mechanism": "灾异、疫病、战乱与饥荒推高死亡率，医政与赈济压低它。"},
    "edu_growth": {"name": "受教育人口年增", "unit": "%",
                   "where": "depts_rites.calc_education_summary",
                   "mechanism": "书院与科举投入越多，受教育人口增长越快；结构性失业会把占比压回去。"},
    "higher_edu_growth": {"name": "受高等教育人口年增", "unit": "%",
                          "where": "depts_rites.calc_education_summary",
                          "mechanism": "高等教育受科举名额与名师影响，增长慢于普通教育；结构性失业会削减它。"},
    "demand": {"name": "全国各阶级需求", "unit": "%",
               "where": "economy.class_demand / local_demand",
               "mechanism": "需求按人口与生活水平推算；物价、贸易状态与超控修正按唯一公式增减。"},
    "attitude": {"name": "各阶级态度", "unit": "点",
                 "where": "economy 的月度态度结算 按阶级应用",
                 "mechanism": "态度按月结算：税负、赏赐、灾异与政令各按点数增减，区间为负一百到正一百。"},
    # ── 灾害四量：全是数字，都能被修正加减 ──
    "disaster_chance": {"name": "灾害发生概率", "unit": "%",
                        "where": "weather 的黄汛与极端灾害判定",
                        "mechanism": "每种灾害有自己的基础发生概率，工程减灾、超控与科技按唯一公式增减；"
                                     "算出来的概率就是判定用的概率。"},
    "disaster_penalty": {"name": "灾害减产幅度", "unit": "%",
                         "where": "weather 的灾害减产与 modifier_registry._from_weather",
                         "mechanism": "灾害的初始减产比例按唯一公式算：工程减灾是它的减项，"
                                      "超控与科技也是；算完再登记成产能的乘法修正。"},
    "disaster_death": {"name": "灾害死亡人数", "unit": "%",
                       "where": "weather 的灾害死亡判定",
                       "mechanism": "死亡人数以人口×减产×种类系数算出基础值，再按唯一公式增减；"
                                    "赈济、医药一类修正可以压低它。"},
    "damage_degree": {"name": "工程损毁程度", "unit": "%",
                      "where": "weather 的大黄汛损毁、engine.projects 的生效折算",
                      "mechanism": "大黄汛时每处黄河工程的基础损毁为全毁（100%），"
                                   "加固类修正按唯一公式把它减下来；工程生效一律按 (1−损毁程度) 折算。"},
}

# 由「部门列表」承载的静态修正 这两个列表会被超控指令在运行时增删，
# 所以它们的数据虽是 world/modifiers.json 播的种，运行时的真相在列表里。
LIST_SOURCES = ("吏部 · 行政纪律", "刑部 · 现行法律")

_CACHE: Dict[str, Any] = {}


# ═══════════════════════════════════════════════════════════
# 唯一公式
# ═══════════════════════════════════════════════════════════

def apply_formula(base: float, mults: List[float], adds: List[float]) -> float:
    """唯一公式：基础值 ×  1 + Σ乘法 + Σ加法。"""
    return float(base) * (1.0 + sum(float(m) for m in mults)) + sum(float(a) for a in adds)


def mechanism_of(target: str) -> str:
    """该 target 的机制说明 唯一出处 TARGETS 的 mechanism；未登记的 target 直接报错。"""
    if target not in TARGETS:
        raise KeyError("未登记的修正 target：%s 可用：%s" % (target, "、".join(TARGETS)))
    return TARGETS[target]["mechanism"]


# ═══════════════════════════════════════════════════════════
# 静态修正：world/modifiers.json
# ═══════════════════════════════════════════════════════════

def load_static() -> List[Dict[str, Any]]:
    """读静态修正表 读一次缓存；结构写错直接报错，不静默跳过。"""
    if "static" in _CACHE:
        return _CACHE["static"]
    with open(MODIFIERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    mods = data.get("modifiers")
    if not isinstance(mods, list) or not mods:
        raise KeyError ("world/modifiers.json 缺 modifiers 列表")
    for m in mods:
        for key in ("id", "name", "intro", "effects"):
            if key not in m:
                raise KeyError("world/modifiers.json 的修正 %s 缺字段 %s" % (m.get("id", "?"), key))
        for eff in m["effects"]:
            if eff.get("target") not in TARGETS:
                raise KeyError("修正 %s 的效果 target 未登记：%s" % (m["id"], eff.get("target")))
            if eff.get("op") not in OPS:
                raise KeyError("修正 %s 的 op 只能是 %s，实际 %s" % (m["id"], "/".join(OPS), eff.get("op")))
    _CACHE["static"] = mods
    return mods


def static_of(target: str, source: Optional[str] = None) -> List[Dict[str, Any]]:
    """静态修正里打这个 target 的那些，可按 from 过滤，如「吏部 · 行政纪律」。"""
    out = []
    for m in load_static():
        if source is not None and m.get("from") != source:
            continue
        if any(e["target"] == target for e in m["effects"]):
            out.append(m)
    return out


def effects_of(mod: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
    """一个修正打某个 target 的效果 可能多条。"""
    return [e for e in (mod.get("effects") or []) if e.get("target") == target]


# 旧字段名 →  target, 面板小字列名
_LEGACY_KEYS = {
    "central_corruption_pct": ("central_corruption", "central_desc"),
    "local_corruption_pct": ("local_corruption", "local_desc"),
    "carrying_pct": ("carrying_pct", "carrying_desc"),
}


def initial_buffs(source: str) -> List[Dict[str, Any]]:
    """把 JSON 里的静态修正投影成「部门列表」现用的字段形状。

    ⚠ 这是**过渡用的兼容投影**，唯一一处：`depts_libu.ADMIN_DISCIPLINE_BUFFS` 与
    `depts_xingbu.ACTIVE_LAWS` 这两个列表会被超控指令在运行时增删 operations.py，
    所以在它们的消费点全部改成按 target 读效果之前，先用这里把初始值喂进去。
    数据源仍然只有 world/modifiers.json 一处。
    """
    out = []
    for m in load_static():
        if m.get("from") != source:
            continue
        item = {
            "id": m["id"],
            "name": m["name"],
            "desc": m["intro"],
            "tag": m.get("tag", ""),
            "source": m.get("from", ""),
        }
        if "category" in m:
            item["category"] = m["category"]
        if "effect_desc" in m:
            item["effect_desc"] = m["effect_desc"]
        for legacy, (target, desc_key) in _LEGACY_KEYS.items():
            item[legacy] = 0.0
            for eff in effects_of(m, target):
                if eff["op"] != OP_MUL:
                    raise KeyError("部门列表只支持乘法效果，%s 的 %s 是%s"
                                   % (m["id"], target, eff["op"]))
                item[legacy] += float(eff["value"])
        # 面板上那几列小字 +20% / -10% / -
        for legacy, (target, desc_key) in _LEGACY_KEYS.items():
            v = item.get(legacy) or 0.0
            item[desc_key] = ("%+.0f%%" % (v * 100)) if v else "-"
        out.append(item)
    return out


# ═══════════════════════════════════════════════════════════
# 运行时修正：各来源登记进同一个收集口
# ═══════════════════════════════════════════════════════════

def _all_classes() -> List[str]:
    from .economy import CLASSES_ORDER
    return list(CLASSES_ORDER)


def _contribution(mod: Dict[str, Any], eff: Dict[str, Any], desc: str = "",
                  classes: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "id": mod.get("id", ""),
        "name": mod.get("name", ""),
        "from": mod.get("from", ""),
        "target": eff["target"],
        "op": eff["op"],
        "value": float(eff["value"]),
        "intro": mod.get("intro", ""),
        "desc": desc,
        "classes": list(classes) if classes else [],
    }


def _from_static(state: Any, target: str, province_id: Optional[str], out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """静态修正 world/modifiers.json。由部门列表承载的那两组不在这里出，
    以免与「部门列表 provider」重复计一次。"""
    for m in static_of(target):
        if m.get("from") in LIST_SOURCES:
            continue
        for eff in effects_of(m, target):
            out.append(_contribution(m, eff))


# 部门列表的旧字段名 →  target, 是否加法
_LIST_FIELDS = {
    "central_corruption_pct": ("central_corruption", False),
    "local_corruption_pct": ("local_corruption", False),
    "carrying_pct": ("carrying_pct", False),
    "central_corruption_flat": ("central_corruption", True),
    "local_corruption_flat": ("local_corruption", True),
    "carrying_flat": ("carrying_pct", True),
}


def _from_department_lists(state: Any, target: str, province_id: Optional[str],
                           out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """两个可被超控增删的部门列表：吏部行政纪律、刑部现行法律。

    列表里的初始值来自 world/modifiers.json，运行时由超控指令增删；
    这里把它按 target 折算成统一贡献 面板与实算因此同源。
    """
    from .depts_libu import ADMIN_DISCIPLINE_BUFFS
    from .depts_xingbu import ACTIVE_LAWS
    for item in list(ADMIN_DISCIPLINE_BUFFS) + list(ACTIVE_LAWS):
        for field, (tgt, is_add) in _LIST_FIELDS.items():
            if tgt != target:
                continue
            v = float(item.get(field) or 0.0)
            if v == 0.0:
                continue
            out.append({
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "from": item.get("source", "") or "部门列表",
                "target": target,
                "op": OP_ADD if is_add else OP_MUL,
                "value": v,
                "intro": item.get("desc", ""),
                "desc": "",
                "classes": [],
            })


def _from_department_modifiers(state: Any, target: str, province_id: Optional[str],
                               out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """超控指令设立的部门修正 运行时增删；效果自带 target/is_pct/delta。"""
    if state is None:
        return
    for dmod in (getattr(state, "department_modifiers", None) or {}).values():
        for eff in (dmod.get("effects") or []):
            et = eff.get("target")
            # 地方腐败在超控里分「全国地方」与「本省」两种写法，都归到 local_corruption
            if target == "local_corruption":
                if et == "all_local_corruption":
                    pass
                elif et == "prov_corruption" and province_id and eff.get("province_id") == province_id:
                    pass
                else:
                    continue
            elif et != target:
                continue
            # 两种历史写法都认：delta_pct 直接是小数与 is_pct + delta 百分数
            if eff.get("delta_pct") is not None:
                op, value = OP_MUL, float(eff.get("delta_pct") or 0.0)
            elif eff.get("is_pct"):
                op, value = OP_MUL, float(eff.get("delta") or 0.0) / 100.0
            else:
                op, value = OP_ADD, float(eff.get("delta") or 0.0)
            out.append({
                "id": dmod.get("id", ""),
                "name": dmod.get("name", ""),
                "from": dmod.get("dept", "") or "超控设立",
                "target": target,
                "op": op,
                "value": value,
                "intro": dmod.get("content_str") or dmod.get("summary") or "超控设立的部门修正",
                "desc": eff.get("desc", ""),
                # 产能与态度按效果自带的阶级生效；其余目标不按阶级分
                "classes": (list(eff.get("classes") or []) or _all_classes()
                            if target in ("attitude", "capacity") else []),
            })


def _from_tech(state: Any, target: str, province_id: Optional[str],
               out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """已解锁科技的效果 数据在 world/tech_effects.json，聚合在 tech_effects.active_totals。"""
    if state is None:
        return
    from .tech_effects import active_totals
    total = float((active_totals(state) or {}).get(target) or 0.0)
    if total == 0.0:
        return
    out.append({
        "id": "tech_effects",
        "name": "科技效果",
        "from": "科技",
        "target": target,
        "op": OP_MUL,
        "value": total,
        "intro": "已解锁科技对该系统的影响合计",
        "desc": "",
        "classes": _all_classes() if target == "capacity" else [],
    })


def _from_religion(state: Any, target: str, province_id: Optional[str],
                   out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """宗教三效果：产能 / 需求 / 威望。**一教一条**，不合成「宗教影响」这种大数。"""
    if state is None:
        return
    key = {"capacity": "prod_pct",
           "demand": "demand_pct",
           "prestige": "prestige_pct"}.get(target)
    if key is None:
        return
    from .depts_rites import calc_religion_summary
    info = calc_religion_summary(state)
    for row in (info.get("religions") or info.get("rows") or []):
        v = float(row.get(key) or 0.0)
        if v == 0.0:
            continue
        inf = float(row.get("influence") or 0.0)
        coeff = float(((row.get("coeffs") or {}).get(key.replace("_pct", "")) or 0.0))
        out.append({
            "id": "religion_%s" % row.get("name", ""),
            "name": "%s影响" % row.get("name", "宗教"),
            "from": "礼部 宗教",
            "target": target,
            "op": OP_MUL,
            "value": v,
            "intro": "影响力 %.1f × %.1f%%/点 = %+.1f%%" % (inf, coeff * 100, v * 100),
            "desc": "",
            "classes": _all_classes() if target in ("capacity", "demand") else [],
        })


# 由 state 上的裸字段直接充当的加减修正 不是"修正对象"，但同走一个公式与明细
_STATE_ADD_FIELDS = {
    "prestige": (("prestige_bonus", 1.0, "恩赏增益"), ("prestige_penalty", -1.0, "威望折损")),
}


def _from_state_fields(state: Any, target: str, province_id: Optional[str],
                       out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    if state is None:
        return
    for field, sign, label in _STATE_ADD_FIELDS.get(target, ()):
        v = float(getattr(state, field, 0.0) or 0.0)
        if v:
            out.append({
                "id": field,
                "name": label,
                "from": "朝廷",
                "target": target,
                "op": OP_ADD,
                "value": sign * v,
                "intro": "由朝廷赏罚直接增减的固定数值",
                "desc": "",
            })




def _from_education(state: Any, target: str, province_id: Optional[str],
                    out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """教育加成 礼部科举：全国各地区各阶级产能的提高，不是超控设立的修正。"""
    if state is None or target != "capacity":
        return
    from .depts_rites import education_bonus_pct
    v = float(education_bonus_pct(state) or 0.0)
    if v == 0.0:
        return
    out.append({
        "id": "education_bonus",
        "name": "教育加成",
        "from": "礼部 科举",
        "target": target,
        "op": OP_MUL,
        "value": v,
        "intro": "教育基本加成：全国各地区各阶级生产力增加",
        "desc": "",
        "classes": _all_classes(),
    })


def _from_weather(state: Any, target: str, province_id: Optional[str],
                  out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """天时：灾害、瘟疫、黄汛、地震 本省自持，数值现算，取自 modifiers 的同一份汇总。"""
    if state is None or not province_id or target != "capacity":
        return
    from .modifiers import get_province_weather_modifiers
    w = get_province_weather_modifiers(state, province_id)
    for key, label in ( ("disaster", "天时灾害"), ("plague", "瘟疫"), ("yellow_flood", "黄汛"),
                        ("earthquake", "地震")):
        item = w.get(key) or {}
        if not item.get("active"):
            continue
        pen = float(item.get("penalty_pct") or item.get("current_penalty_pct") or 0.0)
        if pen <= 0:
            continue
        out.append({
            "id": "weather_%s" % key,
            "name": item.get("name") or label,
            "from": "天时",
            "target": target,
            "op": OP_MUL,
            "value": -pen,
            "intro": item.get("desc", ""),
            "desc": "",
            "classes": _all_classes(),
        })



def _from_projects(state: Any, target: str, province_id: Optional[str],
                   out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """工程（建筑）对灾害数值的减免，走同一个收集口与同一条公式。

    数值写在工程 buff 的 `disaster`（减产）与 `disaster_chance`（发生概率）里，
    键就是灾害种类（flood_minor / flood_major / yellow_flood / yellow_flood_major …），
    与词条表里的 `kind` 一一对应。工程按 (1−损毁程度) 折算后作为乘法修正参与计算，
    不另开「先乘一个系数」的旁路。
    """
    if state is None or not kind:
        return
    field = {"disaster_penalty": "disaster",
             "disaster_chance": "disaster_chance"}.get(target)
    if not field:
        return
    from . import projects as _pj
    from .modifier_terms import MODIFIER_TERMS
    term = next((t for t in MODIFIER_TERMS
                 if t.get("target") == target and t.get("kind") == kind), {})
    term_name = term.get("term", "")
    # 词条自己声明作用面：本省＝只算本省工程；全国＝算全国工程（黄河是一条水系）
    if term.get("area") == "全国":
        province_ids = list((getattr(state, "provinces", {}) or {}).keys())
    else:
        if not province_id:
            return
        province_ids = [province_id]
    for pid in province_ids:
        p = (getattr(state, "provinces", {}) or {}).get(pid) or {}
        for pj in (p.get("local") or {}).get("projects") or []:
            if str(pj.get("status") or "运营中") != "运营中":
                continue
            buff = pj.get("buff") or {}
            raw = float((buff.get(field) or {}).get(kind) or 0.0)
            if not raw:
                continue
            eff = round(raw * _pj.scale(pj), 6)
            if not eff:
                continue
            out.append({
                "id": "project_%s_%s" % (pid, pj.get("name", "")),
                "name": term_name or pj.get("name", ""),
                "from": "工程",
                "target": target,
                "op": OP_MUL,
                "value": eff,
                "intro": buff.get("desc", ""),
                "desc": pj.get("name", ""),
                "classes": _all_classes(),
            })


def _from_province(state: Any, target: str, province_id: Optional[str],
                   out: List[Dict[str, Any]], kind: Optional[str] = None) -> None:
    """本省自持的修正：超控/事件在地方刷的「独立地区修正」 loc.custom_modifiers。

    工程 建筑是特殊修正，不在这里出——它由工程数据与 economy.get_province_class_buffs 处理。
    """
    if state is None or not province_id:
        return
    loc = ((getattr(state, "provinces", {}) or {}).get(province_id) or {}).get("local") or {}
    for cm in (loc.get("custom_modifiers") or []):
        effects = cm.get("effects") or []
        if not effects:
            # 旧数据与事件刷的条目只有 effect_pct：仍按产能处理
            if target != "capacity":
                continue
            v = float(cm.get("effect_pct") or 0.0)
            if v == 0.0:
                continue
            classes = [c for c in (cm.get("affected_classes") or []) if c in _all_classes()]
            out.append({
                "id": cm.get("id", "") or cm.get("name", ""),
                "name": cm.get("name", ""),
                "from": "地方",
                "target": target,
                "op": OP_MUL,
                "value": v,
                "intro": cm.get("note", "") or "在地方刷新的限时修正",
                "desc": "",
                "classes": classes or _all_classes(),
            })
            continue
        # 统一修正：显示位置不决定它影响谁，按 effects 里的 target 逐条生效
        for eff in effects:
            et = eff.get("target")
            if target == "local_corruption":
                if et == "all_local_corruption":
                    pass
                elif et == "prov_corruption" and eff.get("province_id") == province_id:
                    pass
                else:
                    continue
            elif et != target:
                continue
            if eff.get("delta_pct") is not None:
                op, value = OP_MUL, float(eff.get("delta_pct") or 0.0)
            elif eff.get("is_pct"):
                op, value = OP_MUL, float(eff.get("delta") or 0.0) / 100.0
            else:
                op, value = OP_ADD, float(eff.get("delta") or 0.0)
            classes = [c for c in (eff.get("classes") or []) if c in _all_classes()]
            out.append({
                "id": cm.get("id", "") or cm.get("name", ""),
                "name": cm.get("name", ""),
                "from": "地方",
                "target": target,
                "op": op,
                "value": value,
                "intro": cm.get("content_str") or cm.get("note") or "超控设立的地区修正",
                "desc": eff.get("desc", ""),
                "classes": classes if target in ("attitude", "capacity") else [],
            })


# 各来源登记处 唯一一处；加来源就在这里加一行
PROVIDERS: List[Callable[..., None]] = [
    _from_static,
    _from_department_lists,
    _from_department_modifiers,
    _from_tech,
    _from_religion,
    _from_state_fields,
    _from_province,
    _from_projects,
    _from_weather,
    _from_education,
]


def contributions(state: Any, target: str, province_id: Optional[str] = None,
                  kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """某个 target 上全部生效中的修正贡献 谁、什么运算、多少、介绍。

    `kind` 只用于「按灾害种类分开算」的 target（disaster_penalty / disaster_chance）：
    工程减免、词条都按种类各算各的，别的 provider 不理会这个参数。
    """
    if target not in TARGETS:
        raise KeyError("未登记的修正 target：%s 可用：%s" % (target, "、".join(TARGETS)))
    out: List[Dict[str, Any]] = []
    for provider in PROVIDERS:
        provider(state, target, province_id, out, kind=kind)
    return out


# ═══════════════════════════════════════════════════════════
# 取值与明细 面板要显示的就是 breakdown
# ═══════════════════════════════════════════════════════════

def legacy_rows(r: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把 breakdown 折成面板现用的四列形状 name / pct / pct_str / flat / desc。"""
    out = []
    for b in r.get("breakdown") or []:
        is_mul = b["op"] == OP_MUL
        out.append({
            "name": b["name"],
            "pct": b["value"] if is_mul else 0.0,
            "pct_str": ("%+.1f%%" % (b["value"] * 100)) if is_mul else "+0%",
            "flat": 0.0 if is_mul else b["value"],
            "desc": b.get("desc") or b.get("intro") or "",
        })
    return out


def value(state: Any, target: str, base: float,
          province_id: Optional[str] = None,
          kind: Optional[str] = None) -> Dict[str, Any]:
    """按唯一公式算一个值，并给出贡献明细 供面板把「谁加了多少」列出来。

    返回：base / mult_sum / add_sum / value / formula / breakdown
    breakdown 每条 = {name, op, value, value_str, from, intro}
    """
    rows = contributions(state, target, province_id, kind=kind)
    mults = [r["value"] for r in rows if r["op"] == OP_MUL]
    adds = [r["value"] for r in rows if r["op"] == OP_ADD]
    val = apply_formula(base, mults, adds)
    meta = TARGETS.get(target) or {}
    name = meta.get("name", target)
    return {
        "target": target,
        "name": name,
        "base": float(base),
        "mult_sum": round(sum(mults), 6),
        "add_sum": round(sum(adds), 6),
        "value": val,
        "formula": FORMULA_TEXT,
        # 机制：这个数值为什么会动 界面 ⓘ 的「机制」那一批直接念它
        "mechanism": meta.get("mechanism", ""),
        "calculation_text": "%g × (1 + %+.2f%%) %s = %g" % (
            base, sum(mults) * 100,
            ("%+.2f" % sum(adds)) if adds else "", val),
        "breakdown": [{
            "name": r["name"],
            "op": r["op"],
            "value": r["value"],
            "value_str": ("%+.1f%%" % (r["value"] * 100)) if r["op"] == OP_MUL else ("%+.1f" % r["value"]),
            "from": r["from"],
            "intro": r["intro"],
            "desc": r["desc"],
        } for r in rows],
    }


def demand_pct(state: Any, province_id: Optional[str] = None) -> float:
    """某省的需求修正合计 唯一出处：基础值取 0，故返回值就是 Σ乘法。

    宗教、超控设立的部门修正等都从 contributions("demand") 来；
    省数据里的 modifier_demand_pct 只是这份结果的月度缓存，供需求计算直接取用。
    """
    return round(value(state, "demand", 0.0, province_id=province_id)["mult_sum"], 4)
