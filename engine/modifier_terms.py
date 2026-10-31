# -*- coding: utf-8 -*-
"""修正词条表 唯一真相源。

口径 玩家定义：
- **词条** = 「修正效果」那一栏里的**字段名**（如「中央腐败程度」）；
  **变化幅度** = 该词条的取值（如 +20%），同一词条可以有两种数值规则（加法与乘法）。
- 字段名不同就是两个词条：「中央腐败程度」与「地区腐败程度」是两个词条。
- 合法词条**有限**，而且**只认登记的那一种写法**：表里没有的写法
  （如「国库+100」「受教育人口每年增加」「威望」）一律判**词条撰写错误**并拒绝，
  不许静默丢弃，也不许再维护一份「别名」。
- 作用面分三种：全国 / 指定地区（写法带省名）/ 指定阶级（写法带阶级名）。

这张表同时供四处使用，别处不许再抄第二份：
1. 超控修正的解析校验 engine.operations.parse_department_modifier_content；
2. 给 AI 的超控指令帮助文本；
3. 接口 GET /api/settings/terms；
4. 设置面板里的「修正词条」表。
"""

from typing import Any, Dict, List, Optional, Tuple

# 阶级与作用面 与 economy.CLASSES_ORDER 同源，避免两处各写一份
def _classes() -> List[str]:
    from .economy import CLASSES_ORDER
    return list(CLASSES_ORDER)


PROVINCE_PLACEHOLDER = "[省名]"
CLASS_PLACEHOLDER = "[阶级]"

# ── 修正的显示位置：界面的名字。「中央」是修正范围的取值，不是界面 ──
MODIFIER_LOCATIONS: List[Dict[str, str]] = [
    {"location": "吏部", "intro": "吏部界面：人事与行政纪律一类的修正"},
    {"location": "户部", "intro": "户部界面：赋税、国库与市面一类的修正"},
    {"location": "礼部", "intro": "礼部界面：外交、宗教、科举教育一类的修正"},
    {"location": "兵部", "intro": "兵部界面：粮饷、驿站与武职一类的修正"},
    {"location": "刑部", "intro": "刑部界面：律令与刑名一类的修正"},
    {"location": "工部", "intro": "工部界面：器物、工程与营造一类的修正"},
    {"location": "内阁", "intro": "内阁界面：票拟与中枢政务一类的修正"},
    {"location": "司礼监", "intro": "司礼监界面：内廷与批红一类的修正"},
    {"location": "都察院", "intro": "都察院界面：监察与风纪一类的修正"},
    {"location": "通政司", "intro": "通政司界面：章奏与传递一类的修正"},
    {"location": "省名", "intro": "写某个省名（如 陕西）：该省地方界面里的修正"},
]

# ── 词条表 ──────────────────────────────────────────────────
# term        词条名，也是**唯一合法写法**（带 [省名] / [阶级] 的表示必须带前缀）
# scope       作用面：全国 / 指定地区 / 指定阶级
# target      打到哪个系统（与 modifier_registry.TARGETS 一致）
# value_rules 允许的数值规则
# unit        单位
# example     写法示例
# intro       这一条是什么意思
MODIFIER_TERMS: List[Dict[str, Any]] = [
    {
        "term": "替代率 f",
        "scope": "全国", "target": "financial_rate", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "替代率 f+5%",
        "intro": "金融替代率：钱庄票号与汇票对现银的替代流转效率。",
    },
    {
        "term": "皇室威望",
        "scope": "全国", "target": "prestige", "value_rules": ["+N", "+N%"],
        "unit": "点", "example": "皇室威望+10",
        "intro": "皇室威望：祭祀、战功与恩赏累积，灾异、败仗与失政折损。",
    },
    {
        "term": "中央腐败程度",
        "scope": "全国", "target": "central_corruption", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "中央腐败程度-10%",
        "intro": "中央腐败程度：京官贪墨与党争推高，考成、京察与律法整肃压低。",
    },
    {
        "term": "全国所有地方腐败程度",
        "scope": "全国", "target": "all_local_corruption", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "全国所有地方腐败程度-5%",
        "intro": "一次性作用于全国各省的地方腐败程度。",
    },
    {
        "term": PROVINCE_PLACEHOLDER + "地区腐败程度",
        "scope": "指定地区", "target": "prov_corruption", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "陕西地区腐败程度+6%",
        "intro": "指定省份的地方腐败程度，写法必须带省名：如 陕西地区腐败程度+6%。",
    },
    {
        "term": "全国所有阶级产能",
        "scope": "全国", "target": "capacity", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "全国所有阶级产能+3%",
        "intro": "全国七个阶级的产能一起增减。",
    },
    {
        "term": CLASS_PLACEHOLDER + "产能",
        "scope": "指定阶级", "target": "capacity", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "农民产能+10%",
        "intro": "指定阶级的产能，写法必须带阶级名：宗室、士绅、商人、农民、百工、百役、流民。",
    },
    {
        "term": "全国所有阶级态度",
        "scope": "全国", "target": "attitude", "value_rules": ["+N", "+N%"],
        "unit": "点", "example": "全国所有阶级态度+4",
        "intro": "全国七个阶级的态度一起增减，区间为负一百到正一百。",
    },
    {
        "term": CLASS_PLACEHOLDER + "态度",
        "scope": "指定阶级", "target": "attitude", "value_rules": ["+N", "+N%"],
        "unit": "点", "example": "农民态度+4",
        "intro": "指定阶级的态度，写法必须带阶级名。",
    },
    {
        "term": "受教育人口年增",
        "scope": "全国", "target": "edu_growth", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "受教育人口年增-12.0%",
        "intro": "全国受教育人口的年度增长率。写成「受教育人口每年增加」是撰写错误。",
    },
    {
        "term": "受高等教育人口年增",
        "scope": "全国", "target": "higher_edu_growth", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "受高等教育人口年增+8.0%",
        "intro": "全国受高等教育人口的年度增长率。",
    },
    {
        "term": "人口出生率",
        "scope": "全国", "target": "population_birth", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "人口出生率+5%",
        "intro": "全国人口出生率：粮食充裕、天下太平则上浮。",
    },
    {
        "term": "人口死亡率",
        "scope": "全国", "target": "population_death", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "人口死亡率-5%",
        "intro": "全国人口死亡率：灾异、疫病与战乱推高，医政与赈济压低。",
    },
    {
        "term": "全国承载力",
        "scope": "全国", "target": "carrying_pct", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "全国承载力-10%",
        "intro": "全国人口自然承载力：土地与水利定基础，腐败、灾异与欠饷按修正增减。",
    },
    {
        "term": "全国各阶级需求",
        "scope": "全国", "target": "demand", "value_rules": ["+N", "+N%"],
        "unit": "%", "example": "全国各阶级需求-5%",
        "intro": "全国七个阶级的总需求。",
    },
    {
        "term": "白银",
        "scope": "全国", "target": "silver_flow", "value_rules": ["N万两/年"],
        "unit": "万两/年", "example": "白银6万两/年",
        "intro": "凭空生成的银两，默认记入国库：写 6万两/年 即每月入账 0.5 万两。只收流量写法。",
    },
    {
        "term": "黄金",
        "scope": "全国", "target": "gold_flow", "value_rules": ["N万两/年"],
        "unit": "万两/年", "example": "黄金1万两/年",
        "intro": "凭空生成的黄金，记入私库：写 1万两/年 即每月入账 1/12 万两。只收流量写法。",
    },
    # ── 灾害四类数值：每种灾害、每个量各一条，方向只由数值正负表示 ──
    {
        "term": "小涝导致的产能损失",
        "area": "本省", "scope": "全国", "target": "disaster_penalty", "kind": "flood_minor",
        "value_rules": ["-N%"], "unit": "%", "example": "小涝导致的产能损失-70%",
        "intro": "小涝发生时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "大涝导致的产能损失",
        "area": "本省", "scope": "全国", "target": "disaster_penalty", "kind": "flood_major",
        "value_rules": ["-N%"], "unit": "%", "example": "大涝导致的产能损失-70%",
        "intro": "大涝发生时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "黄汛导致的产能损失",
        "area": "全国", "scope": "全国", "target": "disaster_penalty", "kind": "yellow_flood",
        "value_rules": ["-N%"], "unit": "%", "example": "黄汛导致的产能损失-20%",
        "intro": "黄汛发生时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "大黄汛导致的产能损失",
        "area": "全国", "scope": "全国", "target": "disaster_penalty", "kind": "yellow_flood_major",
        "value_rules": ["-N%"], "unit": "%", "example": "大黄汛导致的产能损失-20%",
        "intro": "大黄汛发生时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "黄汛发生概率",
        "area": "全国", "scope": "全国", "target": "disaster_chance", "kind": "yellow_flood",
        "value_rules": ["-N%", "+N%"], "unit": "%", "example": "黄汛发生概率-20%",
        "intro": "黄汛判定的基础概率（70%）按此增减；负数表示更不容易发生。",
    },
    {
        "term": "大黄汛发生概率",
        "area": "全国", "scope": "全国", "target": "disaster_chance", "kind": "yellow_flood_major",
        "value_rules": ["-N%", "+N%"], "unit": "%", "example": "大黄汛发生概率-10%",
        "intro": "黄汛之后追加判定大黄汛的基础概率（10%）按此增减。",
    },
    {
        "term": "旱灾导致的产能损失",
        "area": "本省", "scope": "全国", "target": "disaster_penalty", "kind": "drought",
        "value_rules": ["-N%"], "unit": "%", "example": "旱灾导致的产能损失-30%",
        "intro": "旱灾发生时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "热浪导致的产能损失",
        "area": "本省", "scope": "全国", "target": "disaster_penalty", "kind": "heat",
        "value_rules": ["-N%"], "unit": "%", "example": "热浪导致的产能损失-30%",
        "intro": "热浪发生时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "寒潮导致的产能损失",
        "area": "本省", "scope": "全国", "target": "disaster_penalty", "kind": "cold",
        "value_rules": ["-N%"], "unit": "%", "example": "寒潮导致的产能损失-30%",
        "intro": "寒潮发生时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "瘟疫导致的产能损失",
        "area": "本省", "scope": "全国", "target": "disaster_penalty", "kind": "plague",
        "value_rules": ["-N%"], "unit": "%", "example": "瘟疫导致的产能损失-40%",
        "intro": "瘟疫流行时全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "地震导致的产能损失",
        "area": "本省", "scope": "全国", "target": "disaster_penalty", "kind": "earthquake",
        "value_rules": ["-N%"], "unit": "%", "example": "地震导致的产能损失-40%",
        "intro": "地震发生后全省各阶级产能的损失比例；负数表示减轻。",
    },
    {
        "term": "灾害死亡人数",
        "area": "全国", "scope": "全国", "target": "disaster_death",
        "value_rules": ["-N%", "+N%"], "unit": "%", "example": "灾害死亡人数-30%",
        "intro": "灾害死亡人数的基础值按此增减；赈济、医药一类修正写负数。",
    },
    {
        "term": "工程损毁程度",
        "area": "全国", "scope": "全国", "target": "damage_degree",
        "value_rules": ["-N%", "+N%"], "unit": "%", "example": "工程损毁程度-40%",
        "intro": "大黄汛时工程的基础损毁为全毁（100%），加固一类修正写负数把它减下来。",
    },
]


def _norm(text: str) -> str:
    """写法归一：只抹掉空白（「替代率 f」与「替代率f」算同一种写法）。"""
    return "".join(str(text or "").split())


def term_index() -> Dict[str, Dict[str, Any]]:
    """写法 → 词条条目。写法重复登记直接报错，绝不允许两处同义写法打架。"""
    idx: Dict[str, Dict[str, Any]] = {}
    for t in MODIFIER_TERMS:
        key = _norm(t["term"])
        if key in idx:
            raise KeyError("词条写法重复登记：%s" % t["term"])
        idx[key] = t
    return idx


def literal_terms() -> List[Dict[str, Any]]:
    """不带前缀的词条，按字数从长到短排，解析时优先匹配长的。"""
    pairs = [(t["term"], t) for t in MODIFIER_TERMS
             if PROVINCE_PLACEHOLDER not in t["term"] and CLASS_PLACEHOLDER not in t["term"]]
    pairs.sort(key=lambda x: -len(x[0]))
    return [{"alias": a, "entry": t} for a, t in pairs]


def scoped_terms(kind: str) -> List[Dict[str, Any]]:
    """带前缀的词条：[省名]地区腐败程度 / [阶级]产能。kind 取 province 或 class。"""
    ph = PROVINCE_PLACEHOLDER if kind == "province" else CLASS_PLACEHOLDER
    out = []
    for t in MODIFIER_TERMS:
        term = t["term"]
        if ph in term:
            out.append({"alias": term, "prefix": term.split(ph)[0],
                        "suffix": term.split(ph)[1], "entry": t})
    out.sort(key=lambda x: -len(x["suffix"]))
    return out


def terms_payload() -> List[Dict[str, Any]]:
    """接口与界面用的一份数据：词条表只给「词条名 + 取值规则」两件事。"""
    return [{"term": t["term"], "value_rules": list(t["value_rules"])} for t in MODIFIER_TERMS]


def locations_payload() -> List[Dict[str, str]]:
    """修正可选的显示位置（界面名）。「中央」是修正范围的取值，不是界面。"""
    return [dict(x) for x in MODIFIER_LOCATIONS]


def _target_name(target: str) -> str:
    from .modifier_registry import TARGETS
    if target in TARGETS:
        return TARGETS[target]["name"]
    if target == "all_local_corruption":
        return "全国所有地方腐败程度"
    if target == "prov_corruption":
        return "指定地区腐败程度"
    return target


def find_literal(text: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """在一段文本里找最长的字面词条写法，返回 (词条, 命中的写法)。"""
    flat = _norm(text)
    best = None
    for item in literal_terms():
        if _norm(item["alias"]) in flat:
            if best is None or len(item["alias"]) > len(best[1]):
                best = (item["entry"], item["alias"])
    return best


def find_scoped(text: str, kind: str, names: List[Tuple[str, str]]) -> Optional[Tuple[Dict[str, Any], str, str]]:
    """带前缀的写法匹配。

    names 是 (标识, 显示名) 列表，如 [("shaanxi","陕西")] 或阶级名。
    返回 (词条, 命中的写法, 前缀标识)。
    """
    flat = _norm(text)
    for item in scoped_terms(kind):
        for ident, name in names:
            for pre in {ident, name}:
                if not pre:
                    continue
                if _norm(pre + item["prefix"] + item["suffix"]) in flat or \
                   _norm(item["prefix"] + pre + item["suffix"]) in flat:
                    return item["entry"], item["alias"], ident
    return None


def classes_of(text: str) -> List[str]:
    """文本里出现的阶级名。"""
    return [c for c in _classes() if c in text]
