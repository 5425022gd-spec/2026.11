# -*- coding: utf-8 -*-
"""阶级叛乱 【v0.3.6】新机制。

触发
----
某省某阶级态度降到 0 时，每天 1% 概率发起一次阶级叛乱；可多阶级同时判定，
多支叛军同时存在 都进军队名册，owner＝叛军，一次叛乱生成一支军队。

装备池
------
world/rebel_gear_pool.json 里两个池子：民变池 农民/百工/百役/流民共抽、
私兵池 商人/士绅/宗室共抽。一张卡只有三样：

    人数 men ｜ 点数 points ｜ 装备清单 equipment 槽位 → 装备名 + 数量

卡里没有队名、没有介绍、没有精锐、没有阶级、没有归属，也不写年代型号——那些是
抽完卡生成军队时才有的。卡与卡拉开档次：有人多装备差，有人少械好，也有人多且精锐。

点数
----
- 民变：点数 = 该阶级当月产能 万单位/月 ÷ 300；流民不产出，按本省总产出的两成折算。
- 私兵：点数 = max 阶级产能 × 0.1 ÷ 300, 阶级人口 × 固定比例，比例 商人 2%、士绅 3%、宗室 5%。

抽卡
----
按点数从本组池子里随机抽卡，抽到就把这张卡的人与装备并进同一个篮子，点数用完为止；
点数不足一张卡时按剩余点数比例出残批 人数与各装备数量同比例缩放。

生成军队
--------
人数分配：先按抽到的人数，再按四个阶级的  100 − 态度 之比值从各阶级人口实扣，
态度越低出人越多，发起叛乱的阶级名额额外 ×2；私兵只从本阶级出人。
装备：抽到的卡按槽位归并、同名合并；私兵再自本省驻军复制装备 商人 10%~30%、
士绅 20%~40%、宗室 70%~80%后混编。落到军队名册时，melee/ranged/armors/artillery
用 `era_model` 键、horses 用 `grade` 键。
精锐：民变 1~25 随机；私兵随驻军 无驻军取 30。
队名：统一 `{省名}{阶级}叛军` 见 REBEL_NAME_FMT。
"""

import copy
import json
import os
import random
from typing import Any, Dict, List, Optional, Tuple

POOL_FILE = os.path.join("world", "rebel_gear_pool.json")

REBEL_CHANCE_DAILY = 0.01          # 态度归零后，每天 1% 概率起事
PROD_PER_POINT = 300.0             # 民变：每 300 万单位/月产能 = 1 点
LIUMIN_PROD_SHARE = 0.2            # 流民无产出，按本省总产出的两成折算
RETINUE_PROD_SHARE = 0.1           # 私兵：阶级产能 × 0.1
RETINUE_POP_SHARE = {"商人": 0.02, "士绅": 0.03, "宗室": 0.05}
LEVY_CLASSES: Tuple[str, ...] =  "农民", "百工", "百役", "流民"
RETINUE_CLASSES: Tuple[str, ...] =  "商人", "士绅", "宗室"
RETINUE_COPY_RANGE = {"商人": (0.10, 0.30), "士绅": (0.20, 0.40), "宗室": (0.70, 0.80)}
REBEL_NAME_FMT = "{province}{cls}叛军"          # 队名统一：xxx叛军
REBEL_LOYALTY = 88.0
REBEL_CORRUPTION = {"农民": 55.0, "百工": 45.0, "百役": 42.0, "流民": 60.0,
                    "商人": 38.0, "士绅": 35.0, "宗室": 30.0}
REBEL_COMMANDER = {"农民": "乡民首领", "百工": "炉头匠首", "百役": "逃役班头", "流民": "流民渠帅",
                   "商人": "商家东主", "士绅": "乡绅家主", "宗室": "王府宗藩"}
ELITE_RANDOM_RANGE = (1, 25)       # 民变精锐度
RETINUE_ELITE_FALLBACK = 30        # 无驻军可复制时的精锐度
HORSE_SLOT = "horses"              # 名册里马匹用 grade 键，其余用 era_model

_POOL_CACHE: Optional[Dict[str, Any]] = None


def load_gear_pool() -> Dict[str, Any]:
    """读取装备池 进程内缓存。"""
    global _POOL_CACHE
    if _POOL_CACHE is None:
        with open(POOL_FILE, encoding="utf-8") as f:
            _POOL_CACHE = json.load(f)
    return _POOL_CACHE


def pool_cards(kind: str) -> List[Dict[str, Any]]:
    """取某一组的卡池：kind 传「民变」或「私兵」。"""
    return list(load_gear_pool()["pools"][kind])


def pool_kind_of(cls_name: str) -> str:
    """阶级归到哪一组池子：民变池 还是 私兵池。"""
    return "民变" if cls_name in LEVY_CLASSES else "私兵"


def card_men(card: Dict[str, Any]) -> float:
    return float(card["men"])


def card_points(card: Dict[str, Any]) -> float:
    return float(card["points"])


def scale_card(card: Dict[str, Any], ratio: float) -> Dict[str, Any]:
    """残批：按比例缩一张卡的人数与各装备数量 点数不足一张卡时用。"""
    ratio = max(0.0, float(ratio))
    out = {"id": card.get("id"), "men": max(1.0, card_men(card) * ratio),
           "points": card_points(card) * ratio, "equipment": {}}
    for slot, items in (card.get("equipment") or {}).items():
        scaled = []
        for it in items or []:
            n = int(round(float(it.get("count") or 0) * ratio))
            if n > 0:
                scaled.append({"name": it.get("name") or "", "count": n})
        if scaled:
            out["equipment"][slot] = scaled
    return out


def draw_cards(cards: List[Dict[str, Any]], points: float,
               rng: random.Random) -> List[Dict[str, Any]]:
    """按点数抽卡：随机抽，抽到就把这张卡并进篮子，点数用完为止。"""
    drawn: List[Dict[str, Any]] = []
    if points <= 0 or not cards:
        return drawn
    guard = 0
    while points > 1e-6 and guard < 300:
        guard += 1
        card = rng.choice(cards)
        cost = card_points(card)
        if cost <= points:
            drawn.append({"card": card, "men": card_men(card),
                          "equipment": copy.deepcopy(card.get("equipment") or {})})
            points -= cost
        else:
            drawn.append({"card": card, "men": max(1.0, card_men(card) * (points / cost)),
                          "equipment": copy.deepcopy(scale_card(card, points / cost)["equipment"])})
            points = 0.0
    return drawn


def merge_gear(basket: Dict[str, Dict[str, float]],
               equipment: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, float]]:
    """把一份装备清单并进篮子：按槽位归并、同名累加数量。"""
    for slot, items in (equipment or {}).items():
        bucket = basket.setdefault(slot, {})
        for it in items or []:
            name = it.get("name") or it.get("era_model") or it.get("grade") or ""
            if not name:
                continue
            bucket[name] = bucket.get(name, 0.0) + float(it.get("count") or 0.0)
    return basket


def basket_to_equipment(basket: Dict[str, Dict[str, float]]) -> Dict[str, List[Dict[str, Any]]]:
    """篮子 → 军队名册的装备结构 马匹用 grade，其余用 era_model。"""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for slot, items in basket.items():
        key = "grade" if slot == HORSE_SLOT else "era_model"
        rows = []
        for name, cnt in items.items():
            n = int(round(cnt))
            if n > 0:
                rows.append({key: name, "count": n})
        if rows:
            out[slot] = rows
    return out


def _class_pop_exact(c_data: Dict[str, Any]) -> int:
    return int(c_data.get("pop_exact") or round(float(c_data.get("pop") or 0.0) * 10000))


def class_production_wan(state: Any, loc: Dict[str, Any], cls_name: str) -> float:
    """该阶级当月产能 万单位/月，与面板同源。"""
    from .economy import class_production
    c_data = (loc.get("classes") or {}).get(cls_name) or {}
    pop = float(c_data.get("pop") or 0.0)
    return float(class_production(cls_name, pop, loc))


def province_production_wan(loc: Dict[str, Any]) -> float:
    """本省实物总产出 万单位/月。"""
    from .economy import local_production
    return float(local_production(loc))


def rebellion_points(state: Any, loc: Dict[str, Any], cls_name: str) -> float:
    """叛乱点数：民变按阶级产能折点，私兵按产能两成折点与人口比例取大者。"""
    if cls_name in LEVY_CLASSES:
        prod = class_production_wan(state, loc, cls_name)
        if prod <= 0:                                  # 流民不产出：按本省总产出两成
            prod = province_production_wan(loc) * LIUMIN_PROD_SHARE
        return round(prod / PROD_PER_POINT, 3)
    prod_pts = class_production_wan(state, loc, cls_name) * RETINUE_PROD_SHARE / PROD_PER_POINT
    c_data = (loc.get("classes") or {}).get(cls_name) or {}
    pop_pts = _class_pop_exact(c_data) * RETINUE_POP_SHARE.get(cls_name, 0.02) / 1000.0
    return round(max(prod_pts, pop_pts), 3)


def split_men_by_attitude(classes: Dict[str, Any], men_total: float,
                         rebel_cls: str) -> Dict[str, int]:
    """按  100 − 态度 的比值分配人数；发起叛乱的阶级名额 ×2。"""
    weights: Dict[str, float] = {}
    for c_name in LEVY_CLASSES:
        c_data = classes.get(c_name) or {}
        if not c_data:
            continue
        weight = max(0.0, 100.0 - float(c_data.get("attitude") or 0.0))
        if c_name == rebel_cls:
            weight *= 2.0
        if weight > 0:
            weights[c_name] = weight
    if not weights:                        # 异常兜底：全算在起事阶级头上
        return {rebel_cls: int(men_total)}
    tot_w = sum(weights.values())
    out: Dict[str, int] = {}
    remain = int(men_total)
    items = sorted(weights.items(), key=lambda kv: -kv[1])
    for i, (c_name, w) in enumerate(items):
        share = int(round(men_total * w / tot_w))
        if i == len(items) - 1:
            share = remain
        share = max(0, min(share, remain))
        out[c_name] = share
        remain -= share
    return out


def copy_garrison_equipment(state: Any, pid: str, cls_name: str,
                            rng: random.Random) -> Tuple[Dict[str, List[Dict[str, Any]]], float]:
    """私兵自本省驻军复制装备 按阶级比例区间。返回  装备, 实际比例。"""
    from . import armies as A
    share = rng.uniform(*RETINUE_COPY_RANGE.get(cls_name, (0.1, 0.3)))
    units = [u for u in A.get_all_armies(state)
             if u.get("province_id") == pid or u.get("current_location") == pid]
    basket: Dict[str, Dict[str, float]] = {}
    for u in units:
        for slot, items in (u.get("equipment") or {}).items():
            bucket = basket.setdefault(slot, {})
            for it in items or []:
                name = it.get("era_model") or it.get("grade") or ""
                if not name:
                    continue
                bucket[name] = bucket.get(name, 0.0) + float(it.get("count") or 0.0)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for slot, items in basket.items():
        rows = []
        for name, cnt in items.items():
            n = int(round(cnt * share))
            if n > 0:
                rows.append({"name": name, "count": n})
        if rows:
            out[slot] = rows
    return out, round(share, 3)


def _garrison_elite(state: Any, pid: str) -> int:
    from . import armies as A
    units = [u for u in A.get_all_armies(state)
             if u.get("province_id") == pid or u.get("current_location") == pid]
    if not units:
        return RETINUE_ELITE_FALLBACK
    return int(round(sum(float(u.get("elite") or 0.0) for u in units) / len(units)))


def raise_rebellion(state: Any, pid: str, cls_name: str,
                    rng: Optional[random.Random] = None) -> Dict[str, Any]:
    """在某省发起一次该阶级的叛乱：抽卡 → 并人并装备 → 生成一支军队。"""
    from . import armies as A
    rng = rng or random.Random()
    p = state.provinces[pid]
    p_name = p.get("name", pid)
    loc = p.get("local") or {}
    classes = loc.get("classes") or {}
    kind = pool_kind_of(cls_name)

    points = rebellion_points(state, loc, cls_name)
    drawn = draw_cards(pool_cards(kind), points, rng)
    men_total = int(round(sum(d["men"] for d in drawn))) if drawn else 0
    if men_total <= 0:
        return {"ok": False, "reason": "点数不足，未成军", "province": p_name, "cls": cls_name}

    basket: Dict[str, Dict[str, float]] = {}
    for d in drawn:
        merge_gear(basket, d["equipment"])
    copied_share = None
    if kind == "私兵":
        copied, copied_share = copy_garrison_equipment(state, pid, cls_name, rng)
        merge_gear(basket, copied)
        elite = _garrison_elite(state, pid)
    else:
        elite = rng.randint(*ELITE_RANDOM_RANGE)

    if kind == "民变":
        alloc = split_men_by_attitude(classes, men_total, cls_name)
    else:
        alloc = {cls_name: men_total}
    for c_name, n in alloc.items():
        c_data = classes.get(c_name)
        if not c_data or n <= 0:
            continue
        after = max(0, _class_pop_exact(c_data) - int(n))
        c_data["pop_exact"] = after
        c_data["pop"] = round(after / 10000.0, 4)

    unit = _build_unit(state, pid, p_name, cls_name, kind, alloc, men_total,
                       elite, basket_to_equipment(basket), drawn, copied_share)
    data = A.load_armies_data()
    data.setdefault("units", []).append(unit)
    A.save_armies_data(data)

    return {"ok": True, "unit": unit, "men": men_total, "elite": elite,
            "points": points, "province": p_name, "cls": cls_name, "alloc": alloc,
            "copied_share": copied_share,
            "cards": [d["card"].get("id") for d in drawn]}


def _build_unit(state: Any, pid: str, p_name: str, cls_name: str, kind: str,
                alloc: Dict[str, int], men_total: int, elite: int,
                equipment: Dict[str, List[Dict[str, Any]]],
                drawn: List[Dict[str, Any]], copied_share: Optional[float]) -> Dict[str, Any]:
    """抽完卡之后生成军队数据 队名统一 xxx叛军；其余字段按现有名册规范现填。"""
    from . import armies as A
    used_ids = {u.get("id") for u in (A.load_armies_data().get("units") or [])}
    seq = 1
    while ("rebel_%s_%d" % (pid, seq)) in used_ids:
        seq += 1
    unit_id = "rebel_%s_%d" % (pid, seq)

    gear_names: List[str] = []
    for slot, items in equipment.items():
        for it in items:
            nm = it.get("era_model") or it.get("grade") or ""
            if nm and nm not in gear_names:
                gear_names.append(nm)
    lead = "、".join(gear_names[:6]) or "徒手"

    if kind == "民变":
        detail = "、".join("%s %d 人" % (c, n) for c, n in
                          sorted(alloc.items(), key=lambda kv: -kv[1]))
        desc = ("%s%s态度归零，聚众起事，裹挟%s，合兵 %d 人。"
                "器械以%s为主，皆为民间杂械与零星缴获，别无制式可言。"
                % (p_name, cls_name, detail, men_total, lead))
    else:
        desc = ("%s%s离心已久，家奴庄客部曲纠合 %d 人举兵。"
                "自本省驻军处抄掠器械 约占驻军装备的 %.0f%%，与私藏刀枪混用，就中以%s居多。"
                % (p_name, cls_name, men_total, (copied_share or 0.0) * 100, lead))

    return {
        "id": unit_id,
        "name": REBEL_NAME_FMT.format(province=p_name, cls=cls_name),
        "category": "mobile",
        "faction": "阶级叛乱",
        "province_id": pid,
        "current_location": pid,
        "station": "%s 叛乱中" % p_name,
        "commander": REBEL_COMMANDER.get(cls_name, "乱民首领"),
        "count": int(men_total),
        "elite": int(elite),
        "corruption": REBEL_CORRUPTION.get(cls_name, 50.0),
        "loyalty": REBEL_LOYALTY,
        "overall_stance": "进攻",
        "mission": "巡逻",
        "engagement_risk": "低风险交战",
        "retreat_priority": "高撤退优先级",
        "battle_order": "攻打府县，破衙开仓",
        "desc": desc,
        "equipment": equipment,
        "status": "驻防",
        "owner": "叛军",
        "target_location": pid,
        "arrears_wan": 0.0,
        "pay_rate": 0.0,
        "extra_pay": 0.0,
    }


def tick_rebellion_daily(state: Any, rng: Optional[random.Random] = None) -> List[str]:
    """日级判定：态度归零的阶级，每天 1% 概率起事。"""
    rng = rng or random.Random()
    notes: List[str] = []
    for pid, p in list(state.provinces.items()):
        loc = p.get("local") or {}
        classes = loc.get("classes") or {}
        if not classes:
            continue
        for c_name in list(LEVY_CLASSES) + list(RETINUE_CLASSES):
            c_data = classes.get(c_name)
            if not c_data:
                continue
            if float(c_data.get("attitude") or 0.0) > 0.0:
                continue
            if rng.random() >= REBEL_CHANCE_DAILY:
                continue
            res = raise_rebellion(state, pid, c_name, rng)
            if res.get("ok"):
                notes.append("【%s】%s态度归零，%s起事：%s %d 人，精锐 %d，点数 %.2f，抽卡 %s"
                             % (res["province"], c_name,
                                "民变" if c_name in LEVY_CLASSES else "私兵",
                                res["unit"]["name"], res["men"], res["elite"], res["points"],
                                "、".join(str(c) for c in res["cards"])))
            else:
                notes.append("【%s】%s欲起事，然%s" % (p.get("name", pid), c_name, res.get("reason", "")))
    return notes
