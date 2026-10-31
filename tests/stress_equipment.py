"""装备补充（engine/armies.py 装备增援逻辑）压力测试。

这是「跑极端假设情况，看系统能否正常返回」型的测试，不是随机生成压量。
覆盖：脏数据、边界值、分账、收敛性、幂等，以及太仆寺马匹与工部军械的分流。

规则背景：装备增援每日从库存按编制缺口补给 —— 机动部队优先，同级平均分配；
军械取工部仓储（state.works_inventory），马匹取太仆寺常盈库（state.taipusi_horses）。
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

from engine.session import GameSession
from engine.armies import load_armies_data, save_armies_data, tick_armies_daily

FAILS = []


def ok(cond, label, extra=""):
    if cond:
        print("PASS  " + label)
    else:
        FAILS.append(label)
        print("FAIL  " + label + ("  -> " + str(extra) if extra else ""))


def mk_army(aid, cat, rows, category="mobile", owner="ming"):
    """合成一支驻防中的部队（驻防态不触发行军逻辑）；属性显式，过滤靠属性。"""
    return {"id": aid, "name": aid, "category": category, "owner": owner,
            "status": "驻防", "equipment": {cat: rows}}


def replenish(state, units, garrisons=None):
    """用合成军册跑一次每日推进，返回推进后的军册（跑完还原真实军册）。"""
    saved = copy.deepcopy(load_armies_data())
    synth = {"units": list(units) + list(garrisons or [])}
    try:
        save_armies_data(synth)
        tick_armies_daily(state, 1)
        return copy.deepcopy(load_armies_data())
    finally:
        save_armies_data(saved)


def set_inv(state, works=None, horses=None):
    state.works_inventory = dict(works or {})
    state.taipusi_horses = dict(horses or {})


def rows_of(data, aid, cat):
    for m in data.get("units", []):
        if m.get("id") == aid:
            return (m.get("equipment") or {}).get(cat) or []
    return []


def main():
    s = GameSession()
    st = s.state

    SPEAR = "万历二十年式白蜡杆长矛"
    HORSE = "上等战马"

    # ── 1) 库存为 0：有缺口也不增援 ──
    set_inv(st, works={SPEAR: 0})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 0, "quota": 100}])])
    r = rows_of(d, "a1", "melee")[0]
    ok(r["count"] == 0, "库存为 0 时不增援", r)

    # ── 2) 库存为负（脏数据）：不得把部队填成负、也不得更负 ──
    set_inv(st, works={SPEAR: -5})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 0, "quota": 100}])])
    r = rows_of(d, "a1", "melee")[0]
    ok(r["count"] == 0, "库存为负时不增援", r)
    ok(load_armies_data() is not None, "库存为负时不崩")

    # ── 3) 巨大库存：一次配满且不超发 ──
    set_inv(st, works={SPEAR: 10 ** 9})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 5, "quota": 100}])])
    r = rows_of(d, "a1", "melee")[0]
    ok(r["count"] == 100, "缺口 95 应补满到 100", r)

    # ── 4) 超编（quota < count）：不回扣 ──
    set_inv(st, works={SPEAR: 500})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 300, "quota": 50}])])
    r = rows_of(d, "a1", "melee")[0]
    ok(r["count"] == 300, "超编部队不得被回扣", r)

    # ── 5) quota == count：无动作 ──
    set_inv(st, works={SPEAR: 500})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 100, "quota": 100}])])
    r = rows_of(d, "a1", "melee")[0]
    ok(r["count"] == 100, "编制已满时不动", r)

    # ── 6) 缺 quota 字段：按 count 兜底 → 无缺口 ──
    set_inv(st, works={SPEAR: 500})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 100}])])
    r = rows_of(d, "a1", "melee")[0]
    ok(r["count"] == 100, "缺 quota 时以 count 兜底", r)

    # ── 7) 缺 count 字段：视为 0，按编制补 ──
    set_inv(st, works={SPEAR: 500})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "quota": 80}])])
    r = rows_of("".join([]) or d, "a1", "melee")[0]
    ok(r["count"] == 80, "缺 count 时应按编制补满", r)

    # ── 8) 负 quota：不增援 ──
    set_inv(st, works={SPEAR: 500})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 10, "quota": -20}])])
    r = rows_of(d, "a1", "melee")[0]
    ok(r["count"] == 10, "负编制不得增援", r)

    # ── 9) 接收方多于库存：总量不得超发，且不出现负数 ──
    set_inv(st, works={SPEAR: 2})
    armies = [mk_army("a%d" % i, "melee", [{"era_model": SPEAR, "count": 0, "quota": 100}])
              for i in range(3)]
    d = replenish(st, armies)
    got = [rows_of(d, a["id"], "melee")[0]["count"] for a in armies]
    ok(sum(got) <= 2, "发放总量不得超过库存", got)
    ok(all(g >= 0 for g in got), "不得出现负数量", got)
    ok(sum(got) == 2, "库存应被发尽（2 件）", got)

    # ── 10) 无接收方（有货无人缺）：库存不动 ──
    set_inv(st, works={SPEAR: 777})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": "无人用的怪兵器", "count": 0, "quota": 10}])])
    ok(st.works_inventory.get(SPEAR) == 777, "无人申领时库存不变", st.works_inventory)

    # ── 11) equipment 为空 / None：不崩 ──
    set_inv(st, works={SPEAR: 100})
    try:
        d = replenish(st, [
            {"id": "e1", "name": "e1", "status": "驻防", "equipment": {}},
            {"id": "e2", "name": "e2", "status": "驻防", "equipment": None},
            {"id": "e3", "name": "e3", "status": "驻防"},
        ])
        ok(True, "equipment 为空/None/缺失时不崩")
    except Exception as e:  # noqa: BLE001
        ok(False, "equipment 为空/None/缺失时不崩", repr(e))

    # ── 12) 分账：军械取工部库、马匹取太仆寺库，互不串账 ──
    set_inv(st, works={SPEAR: 50}, horses={HORSE: 10})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 0, "quota": 50}]),
                       mk_army("a2", "horses", [{"grade": HORSE, "count": 0, "quota": 10}])])
    ok(rows_of(d, "a1", "melee")[0]["count"] == 50, "军械缺口由工部库补满")
    ok(rows_of(d, "a2", "horses")[0]["count"] == 10, "马匹缺口由太仆寺库补满")
    ok(st.works_inventory.get(SPEAR) == 0, "工部库军械已扣净", st.works_inventory)
    ok(st.taipusi_horses.get(HORSE) == 0, "太仆寺库马匹已扣净", st.taipusi_horses)
    ok(HORSE not in st.works_inventory, "工部库不得出现马匹", st.works_inventory)
    ok(SPEAR not in st.taipusi_horses, "太仆寺库不得出现军械", st.taipusi_horses)

    # ── 13) 极端编制（10^9）：库存不被发成负数 ──
    set_inv(st, works={SPEAR: 10})
    d = replenish(st, [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 0, "quota": 10 ** 9}])])
    ok(rows_of(d, "a1", "melee")[0]["count"] == 10, "极端编制下只发满库存", rows_of(d, "a1", "melee")[0])
    ok(st.works_inventory.get(SPEAR) == 0, "库存扣到 0 不为负", st.works_inventory)

    # ── 14) 收敛性：把每轮结果带回下一轮，缺口应单调收敛、库存不为负 ──
    set_inv(st, works={SPEAR: 1000})
    armies = [mk_army("a%d" % i, "melee", [{"era_model": SPEAR, "count": 0, "quota": 300}])
              for i in range(4)]
    prev, mono = 0, True
    for _step in range(1, 10):
        d = replenish(st, copy.deepcopy(armies))
        armies = copy.deepcopy(d["units"])
        total = sum(a["equipment"]["melee"][0]["count"] for a in armies)
        if total < prev:
            mono = False
        prev = total
    ok(mono, "反复推进时已补数量单调不减")
    ok(prev == 1000, "库存 1000 应恰好发尽（总编制 1200）", prev)
    d = replenish(st, copy.deepcopy(armies))
    ok(sum(a["equipment"]["melee"][0]["count"] for a in d["units"]) == prev,
       "库存耗尽后再推进不再变化（幂等）", prev)

    # 缺口不均：1 / 1 / 1000，库存 1002，应在有限轮内配满且不超发
    set_inv(st, works={SPEAR: 1002})
    armies = [mk_army("b1", "melee", [{"era_model": SPEAR, "count": 0, "quota": 1}]),
              mk_army("b2", "melee", [{"era_model": SPEAR, "count": 0, "quota": 1}]),
              mk_army("b3", "melee", [{"era_model": SPEAR, "count": 0, "quota": 1000}])]
    rounds, prev = 0, -1
    while rounds < 12:
        rounds += 1
        d = replenish(st, copy.deepcopy(armies))
        armies = copy.deepcopy(d["units"])
        total = sum(a["equipment"]["melee"][0]["count"] for a in armies)
        if total == prev:
            break
        prev = total
    ok(prev == 1002, "缺口不均时应在有限轮内配满（应为 1002）", prev)
    ok(rounds <= 5, "收敛轮数不应过多", rounds)
    ok(st.works_inventory.get(SPEAR, 0) >= 0, "库存不得为负", st.works_inventory)

    # ── 15) days <= 0：直接返回，不动库存 ──
    set_inv(st, works={SPEAR: 40})
    saved = copy.deepcopy(load_armies_data())
    try:
        save_armies_data({"units": [mk_army("a1", "melee", [{"era_model": SPEAR, "count": 0, "quota": 40}])]})
        tick_armies_daily(st, 0)
        d = load_armies_data()
        ok(rows_of(d, "a1", "melee")[0]["count"] == 0, "days=0 时不做补给")
        ok(st.works_inventory.get(SPEAR) == 40, "days=0 时库存不变", st.works_inventory)
    finally:
        save_armies_data(saved)

    print("\n" + ("=== 装备补充压力测试全部通过 ===" if not FAILS
                  else "=== 失败 %d 项：%s ===" % (len(FAILS), "；".join(FAILS))))
    return 1 if FAILS else 0


def test_main():
    rc = main()
    if rc:
        raise SystemExit(rc)


if __name__ == "__main__":
    raise SystemExit(main())
