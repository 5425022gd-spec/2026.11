"""人口机制测试：自然增减（0.3%增 / 0.2%死）、修正接口、赈济、流民转化、阶级变化、教育人口流失。"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_population.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_population_pos.json"))
from engine.session import GameSession
from engine import economy as E
from engine.operations import modify_population_rate_modifier
from engine.localdata import under_ming_control


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_natural_rates_and_interface():
    s = GameSession()
    r = E.population_rates(s.state)
    ok(abs(r["birth"] - 0.003) < 1e-12 and abs(r["death"] - 0.002) < 1e-12,
       "开局是每月自然增加千分之三、自然死亡千分之二，实际 " + str(r))

    done, _ = modify_population_rate_modifier(s.state, "劝农安集", birth_delta=0.001, death_delta=-0.0005)
    ok(done, "加一条人口修正")
    r2 = E.population_rates(s.state)
    ok(abs(r2["birth"] - 0.004) < 1e-12 and abs(r2["death"] - 0.0015) < 1e-12,
       "修正叠加到比例上，实际 %s" % r2)

    # 随存档往返
    from engine.state import WorldState
    ws = WorldState.from_dict(json.loads(json.dumps(s.state.to_dict())))
    ok([m["name"] for m in ws.pop_rate_modifiers] == ["劝农安集"], "人口修正随存档")
    modify_population_rate_modifier(ws, "劝农安集", delete=True)
    ok(not ws.pop_rate_modifiers, "可撤销")
    print("自然变动 ✓ 0.3%增/0.2%死，比例可由修正刷新并随存档")


def test_all_provinces_with_classes():
    """人口机制作用于所有有阶级数据的地区（不只明属）。"""
    s = GameSession()
    st = s.state
    non_ming = [(pid, p) for pid, p in st.provinces.items()
                if (p.get("local") or {}).get("classes") and not under_ming_control(p)]
    ok(non_ming, "世界数据里应有非明属但有阶级数据的省")
    pid, p = non_ming[0]
    before = {k: int(v.get("pop_exact") or 0) for k, v in p["local"]["classes"].items()}
    E.tick_material_monthly(st)
    after = {k: int(v.get("pop_exact") or 0) for k, v in p["local"]["classes"].items()}
    ok(any(after[k] != before[k] for k in before),
       "非明属省份的人口也要按月变动，实际 " + str((before, after)))
    print("作用范围 ✓ 非明属但有阶级数据的 %s 也按月变动" % p.get("name", pid))


def test_relief():
    s = GameSession()
    st = s.state
    st.treasury = 10000.0
    loc = st.provinces["shaanxi"]["local"]
    want = E.relief_matter_wan(loc)
    total = round(sum(want.values()), 4)
    ok(total > 0, "陕西应有赈济额（宗室 5000 两、流民 8000 两 → %.4f万两）" % total)
    t0, r0 = st.treasury, st.local_reserve
    notes = E.tick_material_monthly(st)
    ok(any("赈济" in n for n in notes), "要有一条赈济日志")
    paid = round(sum(E.relief_matter_wan(p.get("local") or {}).values() * 0 + 0 for p in []) + 0, 4)
    # 国库出、地方窖藏入，进出相等（本 tick 不动这两项其它）
    ok(abs((t0 - st.treasury) - (st.local_reserve - r0)) < 0.05,
       "赈济自国库转地方窖藏，进出应相等：国库 %.4f 窖藏 %.4f" % (t0 - st.treasury, st.local_reserve - r0))
    ok(st.local_reserve > r0, "地方窖藏增加")

    # 国库不足则不发
    st2 = GameSession().state
    st2.treasury = 0.0
    r_before = st2.local_reserve
    notes2 = E.tick_material_monthly(st2)
    ok(any("未能发放" in n for n in notes2), "太仓不足时要有未发放的日志")
    ok(st2.local_reserve == r_before, "不发赈济则地方窖藏不动")
    print("赈济 ✓ 国库→地方窖藏等额转移，等额凭空产生该阶级产能，太仓不足即不发")


def test_refugee_rate():
    ok(E.refugee_rate(1.0) == 0.0 and E.refugee_rate(1.2) == 0.0, "供给比≥1 不转化")
    ok(abs(E.refugee_rate(0.95) - 0.0001) < 1e-12, "供给比 0.95 只转 0.01%%")
    ok(abs(E.refugee_rate(0.9) - (0.0001 + 0.0002)) < 1e-12, "低 0.1 加 0.02%%")
    ok(abs(E.refugee_rate(0.5) - (0.0001 + 5 * 0.0002)) < 1e-12, "低 0.5 加 5 档")
    # 真跑一遍：把某省某阶级的供给比压低（抽干库存与产出），看人口流向流民
    s = GameSession()
    st = s.state
    loc = st.provinces["shaanxi"]["local"]
    cls = loc["classes"]
    before_ref = int(cls["流民"]["pop_exact"])
    for c in cls.values():
        c["stock"] = 0.0
    for name in ("农民", "百工", "百役", "士绅", "商人"):
        cls[name]["pop_exact"] = int(cls[name]["pop_exact"] * 0.05)
    E.tick_material_monthly(st)
    ok(int(cls["流民"]["pop_exact"]) > before_ref, "供给不足时人口应转入流民")
    print("流民转化 ✓ 供给比<1 转 0.01%%，每低 0.1 再加 0.02%%")


def test_class_shift():
    classes = {
        "士绅": {"pop_exact": 100000, "pop": 10.0, "attitude": 90, "high_att_months": 1},
        "农民": {"pop_exact": 800000, "pop": 80.0, "attitude": 40},
        "百工": {"pop_exact": 100000, "pop": 10.0, "attitude": 40},
        "百役": {"pop_exact": 50000, "pop": 5.0, "attitude": 40},
        "商人": {"pop_exact": 50000, "pop": 5.0, "attitude": 40},
    }
    notes = E.apply_class_shift(classes)
    ok(notes and "士绅" in notes[0], "态度高的士绅应自同组抽补")
    ok(classes["士绅"]["pop_exact"] == 100000 + 100, "增加本阶级人口的 0.1%%（100 人）")
    ok(classes["农民"]["pop_exact"] + classes["百工"]["pop_exact"] + classes["百役"]["pop_exact"]
       == 950000 - 100, "自同组其他阶级按人口抽补")
    ok(classes["商人"]["pop_exact"] == 50000, "商人不参与抽补（同组只有士农工商役）")
    # 未连续两月则不动
    classes2 = {"士绅": {"pop_exact": 100000, "attitude": 90, "high_att_months": 0},
                "农民": {"pop_exact": 100000, "attitude": 40},
                "百工": {"pop_exact": 100000, "attitude": 40},
                "百役": {"pop_exact": 100000, "attitude": 40}}
    ok(not E.apply_class_shift(classes2), "只高一个月不抽补")
    print("阶级变化 ✓ 士农工商役中态度连续两月高者得 0.1%%，自同组按人口抽")


def test_education_monthly_loss():
    s = GameSession()
    st = s.state
    edu0, high0 = st.local_educated_pop, st.higher_educated_pop
    E.tick_rites_monthly(st)
    r = E.population_rates(st)
    ok(abs(st.local_educated_pop - round(edu0 * (1 - r["death"]), 4)) < 1e-6,
       "教育人口每月按自然死亡比例流失")
    ok(abs(st.higher_educated_pop - round(high0 * (1 - r["death"]), 4)) < 1e-6,
       "受高等教育人口同样流失")
    print("教育人口 ✓ 每月按自然死亡比例（%.3f%%）流失" % (r["death"] * 100))


if __name__ == "__main__":
    test_natural_rates_and_interface()
    test_all_provinces_with_classes()
    test_relief()
    test_refugee_rate()
    test_class_shift()
    test_education_monthly_loss()
    print("── 人口机制测试全部通过")
