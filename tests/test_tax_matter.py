# -*- coding: utf-8 -*-
"""税负折算物质产能扣减的测试（玩家定义的新修正口径）。

口径：
1. 每笔税的产能扣减基准 = 该税"摊到下次征税前"的月均摊额（年税 ÷12、月税 ÷1，均为年额÷12），
   常驻每月实时结算，不再有"年折"。
2. 扣减量是**物质绝对量**：损失 = 银两 × （1两白银=1单位物质）÷ 物价指数 P_t。
   太仆寺常盈库积银、兵部驻军欠饷同口径。
3. 物价指数 P_t = （M_t/Y_t）/（M_0/Y_0），固定基准为开局，P_0 = 1；
   P_t 是物价水平，不是通胀率（通胀率 = P_t/P_{t-1} - 1）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_tax_matter.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_tax_matter_pos.json"))
from engine.session import GameSession
from engine.economy import (calc_price_index, calc_liquidity_health, calc_national_demand,
                            class_capacity, LIANG_TO_MATTER_RATIO)
from engine.depts_hubu import get_ming_provinces
from engine.modifiers import build_local_modifier_index, get_province_local_modifiers


def main():
    s = GameSession()
    st = s.state

    # ── 1) 基准比例与开局物价指数 ──
    assert LIANG_TO_MATTER_RATIO == 1.0, "基准比例应为 1 两白银 = 1 单位物质"
    p0 = calc_price_index(st)
    assert p0 == 1.0, "开局物价指数应为 1.0，实际 %s" % p0
    assert st.price_index_base > 0, "应记下开局基准 r₀"
    print("基准 1两=1单位；开局物价指数 P₀ = %.4f（基准 r₀ = %.6f）✓" % (p0, st.price_index_base))

    # ── 2) 损失 = 银两 × 1 ÷ P；M 涨 11 倍 → 同样税额的物质损失降到 1/11 ──
    idx0 = build_local_modifier_index(st)
    bj0 = get_province_local_modifiers(st, "beizhili", idx0)
    loss0 = bj0["flat_penalty"]["农民"]
    st.m1 = round(st.price_index_base * (1.0 - st.financial_rate) * calc_national_demand(st) * 11.0, 2)
    p11 = calc_price_index(st)
    assert abs(p11 - 11.0) < 0.05, "M 涨 11 倍后物价指数应约为 11，实际 %s" % p11
    bj11 = get_province_local_modifiers(st, "beizhili", build_local_modifier_index(st))
    loss11 = bj11["flat_penalty"]["农民"]
    assert abs(loss11 / loss0 - 1.0 / p11) < 0.02, \
        "物价涨 %.2f 倍后税负物质损失应降到 1/%.2f，实际 %s → %s" % (p11, p11, loss0, loss11)
    print("物价指数 %.2f → 农民税负物质损失 %.2f → %.2f 万单位/月（×1/%.2f）✓"
          % (p11, loss0, loss11, p11))

    # ── 3) 单笔税的物质损失 = 该省月摊税额 × 1 ÷ P ──
    st.m1 = round(st.price_index_base * (1.0 - st.financial_rate) * calc_national_demand(st), 2)
    calc_price_index(st)
    idx = build_local_modifier_index(st)
    bj = get_province_local_modifiers(st, "beizhili", idx)
    from engine.depts_hubu import calc_hubu_taxes
    tax = calc_hubu_taxes(st)
    pa = next(p for t in tax["taxes"] if t["name"] == "夏税"
              for p in t["prov_apportionment"] if p["province_id"] == "beizhili")
    assert abs(pa["matter_loss"] - round(pa["monthly_tax"] * LIANG_TO_MATTER_RATIO, 2)) < 0.02, \
        "户部账里的夏税物质扣减应为 月摊税额 × 1：%s vs %s" % (pa["matter_loss"], pa["monthly_tax"])
    sy = [it for it in bj["items"] if it["name"] == "夏税"][0]
    assert abs(sy["matter_loss"] - pa["matter_loss"]) < 0.05, \
        "修正系统与户部账的夏税物质扣减应一致：%s vs %s" % (sy["matter_loss"], pa["matter_loss"])
    print("夏税：户部账 %.2f = 修正表 %.2f 万单位/月（月摊 %.2f 万两 × 1 ÷ P）✓"
          % (pa["matter_loss"], sy["matter_loss"], pa["monthly_tax"]))

    loc = st.provinces["beizhili"]["local"]
    from engine.economy import _local_environment_penalty, PROD_WEIGHTS, get_province_class_buffs
    # 有 live 时，天时（灾害/瘟疫/黄汛/地震）由修正系统带进 live["penalty_pct"]，
    # 环境扣减只剩承载力超载；无 live 的路径（非明属省份）才在本函数里直接算天时。
    env_pen = _local_environment_penalty(loc, from_live=True)
    env_all = _local_environment_penalty(loc)
    assert env_all >= env_pen - 1e-9, "无 live 时的扣减不该小于只算超载的那份"
    for c in ("农民", "商人", "百工", "百役"):
        pop = float((loc.get("classes") or {}).get(c, {}).get("pop") or 0.0)
        cap = class_capacity(c, pop, loc, bj)
        loss = float(bj["flat_penalty"].get(c, 0.0))
        pct_buff, flat_buff, _notes = get_province_class_buffs(loc, c)
        pct_buff += float(bj["buff_pct"].get(c, 0.0))
        pct_pen = float(bj["penalty_pct"].get(c, 0.0)) + env_pen
        expect = max(0.0, pop * PROD_WEIGHTS[c] * max(0.0, 1.0 + pct_buff - pct_pen)
                     + flat_buff - loss)
        assert abs(cap - expect) < 0.05, "%s：应 %.2f，实际 %.2f（物质扣减 %.2f）" % (c, expect, cap, loss)

    # ── 4) 常盈库积银与驻军欠饷也走物质绝对量，不再叠加百分比 ──
    cy_items = [it for it in bj["items"] if it["name"] == "常盈库积银"]
    assert cy_items and cy_items[0]["matter_loss"] > 0, "常盈库积银应为物质扣减"
    assert all(it.get("effect_pct", 0.0) == 0.0 for it in bj["items"]
               if it["dept"] in ("户部", "兵部", "太仆寺")), "银两类修正不得再用百分比"
    print("常盈库积银 物质扣减 %.2f 万单位/月；户部/兵部/太仆寺修正均无百分比 ✓" % cy_items[0]["matter_loss"])

    # ── 5) 扣减不得为负；并统计被扣到 0 的阶级（绝对量扣减对小产能阶级的必然结果）──
    zero = []
    for pid, p, lc in get_ming_provinces(st):
        live = idx.get(pid)
        for c in ("农民", "商人", "百工", "百役", "士绅"):
            pop = float((lc.get("classes") or {}).get(c, {}).get("pop") or 0.0)
            if pop <= 0:
                continue
            cap = class_capacity(c, pop, lc, live)
            assert cap >= 0.0, "%s-%s 产能不得为负" % (p["name"], c)
            if cap == 0.0:
                zero.append("%s-%s" % (p["name"], c))
    print("产能归零的（省-阶级）共 %d 处：%s" % (len(zero), "、".join(zero[:8])))

    print("\nTAX MATTER OK")


def test_main():
    main()


if __name__ == "__main__":
    main()

