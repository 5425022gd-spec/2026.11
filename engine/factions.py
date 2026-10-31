"""党派判定与内阁/司礼监决策模块。

规则：
1. 人物统计库中的人物：依据其 basic.阵营 判定。
2. 未在人物库中的府级以上官员：根据人物库中已有的在中央正在任职的官员的党派比例随机抽取。
3. 府级以下官员：按照当地府官员 所属知府的党派判定。
4. 内阁多数党派与司礼监掌印太监党派决策：
   - 涉事人与内阁同党 -> 触发消极不处理票拟 (draft_unprocessed) 与不处理奖励 (reward_unprocessed)；
   - 涉事人与内阁不同党 -> 触发积极处理票拟 (draft_processed) 与处理奖励 (reward_processed)。
"""

import json
import os
import random
import re
from typing import Any, Dict, Optional, Tuple

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHAR_CARDS_PATH = os.path.join(_BASE, "data", "character_cards.json")

# 党派标准分类
FACTION_YANDANG = "阉党"
FACTION_DONGLIN = "东林党"
FACTION_NEUTRAL = "无党派"


def normalize_faction(raw: str) -> str:
    """将阵营描述规约到三大党派：东林党、阉党、无党派。"""
    if not raw:
        return FACTION_NEUTRAL
    if "阉" in raw or "魏" in raw:
        return FACTION_YANDANG
    if "东林" in raw:
        return FACTION_DONGLIN
    return FACTION_NEUTRAL


def calc_overall_faction_control(state=None) -> Dict[str, Any]:
    """计算全国朝政党派控制比例。
    统计范围与加权规则：
    - 六部、司礼监、内阁现任管理层：每人权重 ×5
    - 其他中央部门 外诸司、二十四衙门其他诸监局司：每人权重 ×1
    - 吏部文册档案内的地方官员：每人权重 ×0.1
    输出总和为 100% 的党派比例分布。
    """
    from .depts import build_dept
    from .depts_libu import get_libu_roster_groups

    score_map = {FACTION_DONGLIN: 0.0, FACTION_YANDANG: 0.0, FACTION_NEUTRAL: 0.0}
    detail_counts = {FACTION_DONGLIN: 0, FACTION_YANDANG: 0, FACTION_NEUTRAL: 0}

    # 1. 核心中央机构  权重 ×5
    core_high_depts = ["吏部", "户部", "礼部", "兵部", "刑部", "工部", "内阁", "司礼监"]
    for dname in core_high_depts:
        d = build_dept(dname, state)
        if d and d.get("staff"):
            for s in d["staff"]:
                f = get_character_faction(s.get("name", ""), title=s.get("title", ""), location=s.get("location", ""))
                score_map[f] = score_map.get(f, 0.0) + 5.0
                detail_counts[f] = detail_counts.get(f, 0) + 1

    # 2. 其他中央部门  权重 ×1
    from .depts_waizhusi import DEPTS as WAIZHUSI_DEPTS
    from .depts_neiting import DEPTS as NEITING_DEPTS
    other_central = [k for k in WAIZHUSI_DEPTS.keys() if k not in core_high_depts] + \
                    [k for k in NEITING_DEPTS.keys() if k not in core_high_depts]
    for dname in other_central:
        d = build_dept(dname, state)
        if d and d.get("staff"):
            for s in d["staff"]:
                f = get_character_faction(s.get("name", ""), title=s.get("title", ""), location=s.get("location", ""))
                score_map[f] = score_map.get(f, 0.0) + 1.0
                detail_counts[f] = detail_counts.get(f, 0) + 1

    # 3. 吏部文册档案地方官员  权重 ×0.1
    roster_groups = get_libu_roster_groups()
    for prov, list_officers in roster_groups.items():
        for o in list_officers:
            f = get_character_faction(o.get("name", ""), title=o.get("title", ""), location=prov)
            score_map[f] = score_map.get(f, 0.0) + 0.1
            detail_counts[f] = detail_counts.get(f, 0) + 1

    total_weighted = sum(score_map.values())
    if total_weighted <= 0:
        # 一个在职的人都没有＝比例无从谈起：如实给空，界面显示取数失败，
        # 不编一组 75/15/10 充数（那会让玩家以为朝局真是这样）。
        return {"ratios": {}, "scores": score_map, "counts": detail_counts}

    ratios = {
        k: round(v / total_weighted * 100.0, 1) for k, v in score_map.items()
    }
    # 规整尾差使总和精准为 100.0%
    diff = round(100.0 - sum(ratios.values()), 1)
    if diff != 0:
        max_k = max(ratios.items(), key=lambda x: x[1])[0]
        ratios[max_k] = round(ratios[max_k] + diff, 1)

    return {
        "ratios": ratios,
        "scores": {k: round(v, 1) for k, v in score_map.items()},
        "counts": detail_counts
    }


def get_central_faction_distribution() -> Dict[str, float]:
    """统计人物统计库中在中央任职官员的党派分布比例。

    读不到人物统计库＝存档/数据有问题，直接报错；库读到了但京官一个都没有，
    就返回空分布 由调用方决定怎么办，不编一组 0.7/0.1/0.2 冒充统计结果。
    """
    with open(_CHAR_CARDS_PATH, "r", encoding="utf-8") as f:
        cards = json.load(f)

    counts: Dict[str, int] = {}
    from .officials import canonical_location, CAPITAL_PROVINCE
    for name, c in cards.items():
        st = c.get("state", {})
        # 在京/中央任职官员：位置只到省份，故按省名判定 京师诸写法归到北直隶
        if canonical_location(st.get("驻地", "")) == CAPITAL_PROVINCE:
            raw_f = c.get("basic", {}).get("阵营", "")
            f_norm = normalize_faction(raw_f)
            counts[f_norm] = counts.get(f_norm, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def sample_random_faction(dist: Optional[Dict[str, float]] = None) -> str:
    """根据中央党派分布加权随机采样党派。

    分布为空（京官一个都没有）时按无党派处理：没有依据就不编偏向。
    """
    if dist is None:
        dist = get_central_faction_distribution()
    if not dist or sum(dist.values()) <= 0:
        return FACTION_NEUTRAL
    r = random.random()
    cumulative = 0.0
    for faction, weight in dist.items():
        cumulative += weight
        if r <= cumulative:
            return faction
    return FACTION_NEUTRAL


_OFFICIALS_FACTION_CACHE = {}

def get_character_faction(name: str, title: str = "", location: str = "") -> str:
    """获取官员/角色的党派。优先查内存缓存与人物卡库，次查 officials.db 持久化，未收录时采样并持久化。"""
    if not name:
        return FACTION_NEUTRAL

    if name in _OFFICIALS_FACTION_CACHE:
        return _OFFICIALS_FACTION_CACHE[name]

    # 1. 优先从人物卡档案中读取 如魏忠贤、崔呈秀、黄立极等
    try:
        with open(_CHAR_CARDS_PATH, "r", encoding="utf-8") as f:
            cards = json.load(f)
    except Exception:
        cards = {}

    if name in cards:
        raw_f = cards[name].get("basic", {}).get ("阵营", "")
        res = normalize_faction(raw_f)
        _OFFICIALS_FACTION_CACHE[name] = res
        return res

    # 2. 从 officials.db 中查询已持久化的党派
    try:
        from .officials import find_official
        offs = find_official(name)
        if offs and offs[0].get("faction"):
            res = normalize_faction(offs[0]["faction"])
            _OFFICIALS_FACTION_CACHE[name] = res
            return res
    except Exception:
        pass

    # 3. 若未在数据库中或尚未分配党派，按规则加权采样并持久化写入 officials.db
    faction = sample_random_faction()
    try:
        from .officials import _conn
        c = _conn()
        # 若存在该官员则更新 faction 字段，避免后续重复刷新变动
        c.execute("UPDATE positions SET faction=? WHERE person=? AND (faction IS NULL OR faction='')", (faction, name))
        c.commit()
        c.close()
    except Exception:
        pass

    _OFFICIALS_FACTION_CACHE[name] = faction
    return faction


def calc_cabinet_faction_distribution(state=None) -> Dict[str, Any]:
    """计算内阁党派控制比例 只认内阁**当前在任**的辅臣，不另抄一份名册。"""
    from .depts import build_dept
    dept = build_dept ("内阁", state if state else None)
    staff = dept.get("staff", []) if dept else []

    counts: Dict[str, int] = {FACTION_DONGLIN: 0, FACTION_YANDANG: 0, FACTION_NEUTRAL: 0}
    for member in staff:
        m_name = member.get("name", "")
        f = get_character_faction(m_name, title=member.get("title", ""))
        counts[f] = counts.get(f, 0) + 1

    total = sum(counts.values())
    if total <= 0:
        # 内阁无人＝比例无从谈起：如实给空，多数党按无党派
        return {"ratios": {}, "counts": counts, "majority": FACTION_NEUTRAL}

    ratios = {k: round(v / total * 100.0, 1) for k, v in counts.items()}
    diff = round(100.0 - sum(ratios.values()), 1)
    if diff != 0:
        max_k = max(ratios.items(), key=lambda x: x[1])[0]
        ratios[max_k] = round(ratios[max_k] + diff, 1)

    majority = max(counts.items(), key=lambda x: x[1])[0]
    return {
        "ratios": ratios,
        "counts": counts,
        "majority": majority
    }


def get_cabinet_majority_faction(state=None) -> str:
    """获取内阁当前多数党派 内阁无人时按无党派。"""
    return calc_cabinet_faction_distribution(state).get("majority") or FACTION_NEUTRAL


def get_silijian_faction(state=None) -> str:
    """获取司礼监掌印太监党派 魏忠贤 -> 阉党。"""
    return FACTION_YANDANG


def extract_target_person(m: Dict[str, Any]) -> Tuple[str, str]:
    """从章奏中提取主要涉事官员 姓名, 职位。"""
    if m.get("accused_name"):
        return m["accused_name"], m.get("accused_title", "")

    # 从标题匹配：如 特纠不职有司事 知府 雷元让
    title = m.get("title", "")
    match = re.search(r"[ (]([^() ]+)[)]", title)
    if match:
        parts = match.group(1).strip().split()
        if len(parts) >= 2:
            return parts[1], parts[0]
        elif len(parts) == 1:
            return parts[0], ""

    # 从正文匹配 {xname}
    content = m.get("content", "")
    match_c = re.search(r"地([^\s (]+) ([^() ]+)", content)
    if match_c:
        return match_c.group(1).strip(), match_c.group(2).strip()

    # 回退到撰写人
    writer = m.get("from", "")
    parts = writer.split()
    if len(parts) >= 2:
        return parts[1], parts[0]
    return writer, ""


def evaluate_memorial_decision(m: Dict[str, Any], state=None) -> Dict[str, Any]:
    """依据涉事人党派与内阁党派构成判定票拟与奖励走向。"""
    target_name, target_title = extract_target_person(m)
    target_faction = get_character_faction(target_name, title=target_title, location=m.get("Location", ""))
    cabinet_faction = get_cabinet_majority_faction(state)
    silijian_faction = get_silijian_faction(state)

    is_same_faction = (target_faction == cabinet_faction)

    # 同党派：内阁偏袒，不处理；不同党派：内阁依规积极处理
    if is_same_faction:
        selected_draft = m.get("draft_unprocessed") or "该官员向来恪勤自守，事涉风闻难征，原疏宜暂留中查核，毋遽更张。"
        selected_reward = m.get("reward_unprocessed") or "消极留中：暂缓查办，地方潜存矛盾，维持现状。"
        decision_type = "unprocessed"
    else:
        selected_draft = m.get("draft_processed") or "据奏事关吏治风纪，著该抚按会同按察司亲诣确勘，核实具奏，以肃官规。"
        selected_reward = m.get("reward_processed") or "积极处理：行饬查勘，吏治稍肃，惩创奸贪。"
        decision_type = "processed"

    return {
        "target_name": target_name,
        "target_title": target_title,
        "target_faction": target_faction,
        "cabinet_faction": cabinet_faction,
        "silijian_faction": silijian_faction,
        "is_same_faction": is_same_faction,
        "decision_type": decision_type,
        "selected_draft": selected_draft,
        "selected_reward": selected_reward,
    }
