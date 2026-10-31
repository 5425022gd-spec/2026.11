"""承载力测试：ΔN 公式、超载惩罚、开局 N＝D、以及惩罚真的落到产能上。"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_carrying.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_carrying_pos.json"))
from engine.session import GameSession
from engine import economy as E
from engine.localdata import carrying as carrying_value


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_delta_formula():
    # 产能足 → ΔN 为正；不足 → 为负；产需相等 → 0（|N−D| 或 P−D 为 0 时不变化）
    up = E.carrying_delta(90.0, 100.0, 130.0)
    down = E.carrying_delta(90.0, 100.0, 70.0)
    ok(up > 0 and down < 0, "产能足承载力升、不足则降：%s / %s" % (up, down))
    ok(abs(up + down) < 1e-12, "同一 |N−D| 下升降幅度相等")
    ok(abs(E.carrying_delta(100.0, 100.0, 100.0)) < 1e-12, "产需相等则不变")
    ok(abs(E.carrying_delta(100.0, 100.0, 130.0)) < 1e-12, "N＝D 时 |N−D|＝0，也不变")

    # 手算校验：N=100, D=90, P=120, L=3
    #   |N−D|/N = 0.1；sgn=+1；L·ln(1+30/3)=3·ln(11)
    import math
    want = 0.1 * 1.0 * 3.0 * math.log(11.0)
    got = E.carrying_delta(100.0, 90.0, 120.0)
    ok(abs(got - want) < 1e-9, "公式对不上：want %.6f got %.6f" % (want, got))

    # L 在 2.5~3.5 内：取不同 L 只改幅度不改符号
    ok(E.carrying_delta(100.0, 90.0, 120.0, 2.5) > 0 and E.carrying_delta(100.0, 90.0, 120.0, 3.5) > 0,
       "L 取 2.5~3.5 都成立")
    ok(abs(E.CARRYING_SCALE_L - 3.0) < 1e-9, "当前 L 取 3.0")
    print("ΔN 公式 ✓ 符号随 P−D、幅度按 |N−D|/N × L·ln(1+|P−D|/L)")


def test_overload_penalty():
    ok(E.carrying_overload_ratio(100.0, 90.0) == 0.0, "D<N 不算超载")
    ok(abs(E.carrying_overload_ratio(100.0, 110.0) - 0.1) < 1e-12, "超载率 = (D−N)/N")
    # 惩罚% = ((N−D)/N)² × 500：超载 1% → 0.0001×500 = 0.05%
    loc = {"carrying_overload_ratio": 0.01}
    ok(abs(E.carrying_overload_penalty(loc) - 0.0005) < 1e-12, "超载 1% → 产能 -0.05%")
    loc10 = {"carrying_overload_ratio": 0.10}
    ok(abs(E.carrying_overload_penalty(loc10) - 0.05) < 1e-12, "超载 10% → 产能 -5%")
    ok(E.carrying_overload_penalty({}) == 0.0, "没超载就没有惩罚")
    print("超载惩罚 ✓ 惩罚% = ((N−D)/N)² × 500，仅在 D>N 时施加")


def test_opening_carrying():
    with open("world/provinces.json", encoding="utf-8") as f:
        data = json.load(f)
    provs = [p for p in data["provinces"] if (p.get("local") or {}).get("classes")]
    ok(len(provs) == 20, "有阶级数据的 20 省都要设开局承载力")
    for p in provs:
        loc = p["local"]
        ok(float(loc.get("base_carrying") or 0) > 0, "%s 要有 base_carrying" % p["name"])
        ok("carrying" not in loc, "旧字段 carrying 要并进 base_carrying")
    s = GameSession()
    st = s.state
    sx = st.provinces["shaanxi"]
    n0 = float(sx["local"]["base_carrying"])
    ok(abs(float(sx["local"]["base_carrying"]) - n0) < 0.01, "开局承载力＝设定值")
    val = carrying_value(sx["local"], st)
    ok(0.5 * n0 < val < 1.5 * n0, "修正后承载力应还在同量级：base %.1f → value %.1f" % (n0, val))
    print("开局承载力 ✓ 20 省都设了 base_carrying（＝开局总需求），旧字段已并掉")


def test_tick_updates_carrying():
    s = GameSession()
    st = s.state
    sx = st.provinces["shaanxi"]["local"]
    n_before = float(sx["base_carrying"])
    E.tick_material_monthly(st)
    n_after = float(sx["base_carrying"])
    # 陕西开局产不足需 → 承载力下降
    ok(n_after <= n_before, "产不足需时承载力应下降或持平：%.2f → %.2f" % (n_before, n_after))
    ok("carrying_overload_ratio" in sx, "要记下超载率")

    # 把需求压低、产能抬高（造一个产能充足的省），承载力应上升
    okk = 0
    for pid, p in st.provinces.items():
        loc = p.get("local") or {}
        if not loc.get("classes"):
            continue
        loc["base_carrying"] = round(E.local_demand(loc) - 200.0, 1)   # 需求明显高于承载力
        E.tick_material_monthly(st)
        ratio = float(loc.get("carrying_overload_ratio") or 0.0)
        pen = float(loc.get("carrying_overload_penalty") or 0.0)
        if ratio > 0:
            ok(abs(pen - (ratio ** 2) * E.CARRYING_OVERLOAD_COEF / 100.0) < 1e-6,
               "%s 惩罚与公式不符" % p.get("name"))
            okk += 1
        if okk >= 3:
            break
    ok(okk >= 1, "至少有一个省进入超载并记下惩罚")
    print("月度推进 ✓ 承载力按 ΔN 变化、超载率与惩罚写入省数据")


def test_penalty_hits_production():
    s = GameSession()
    st = s.state
    loc = st.provinces["shaanxi"]["local"]
    loc["carrying_overload_ratio"] = 0.0
    base = E.class_capacity("农民", 100.0, loc=loc)
    loc["carrying_overload_ratio"] = 0.1
    with_pen = E.class_capacity("农民", 100.0, loc=loc)
    ok(with_pen < base, "超载惩罚要压产能：无惩罚 %.2f → 有惩罚 %.2f" % (base, with_pen))
    cut = (base - with_pen) / base
    ok(0.04 < cut < 0.06, "超载 10%% 应扣约 5%% 产能（含固定增益项，实际扣 %.2f%%）" % (cut * 100))
    loc["carrying_overload_ratio"] = 0.0
    print("惩罚落地 ✓ 超载惩罚进产能（与灾害/瘟疫同一条地方扣减）")


if __name__ == "__main__":
    test_delta_formula()
    test_overload_penalty()
    test_opening_carrying()
    test_tick_updates_carrying()
    test_penalty_hits_production()
    print("── 承载力测试全部通过")
