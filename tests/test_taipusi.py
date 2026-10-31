"""太仆寺常盈库与马政测试。

规则（玩家定义）：
1. 常盈库自成一本账，不做收支结算，计入 M2，开局 70 万两；
2. 每年公历 1 月 1 日零点，自地方窖藏抽解 50 万两入常盈库；
   每万两抽银使全国产能 -0.1%，按已控制区省份数动态均摊到各省；
3. 每月孳牧上等战马 100 / 中等战马 200 / 下等驭马 600；
   马价按 25/20/10 两自常盈库扣支，常盈库不足则本月停产；
4. 马匹供军队装备增援取用（军械仍取工部仓储）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_taipusi.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_taipusi_pos.json"))
from engine.session import GameSession
from engine.depts_taipusi import (INITIAL_CHANGYING_WAN, ANNUAL_EXTRACT_WAN,
                                  CHANGYING_MOD_NAME, controlled_provinces,
                                  get_taipusi_horses, horse_monthly_cost_wan,
                                  HORSE_MODELS)
from engine.depts_works import ARMAMENT_CATALOG, get_works_inventory
from engine.time import date_key
from engine.calendar import game_to_gregorian


def main():
    s = GameSession()
    st = s.state

    # ── 1) 开局 ──
    assert st.changying_treasury == INITIAL_CHANGYING_WAN, \
        "常盈库开局应为 70 万两，实际 %s" % st.changying_treasury
    inv = get_taipusi_horses(st)
    assert all(v == 0 for v in inv.values()), "太仆寺马匹开局应为 0，实际 %s" % inv
    print("开局常盈库 %.1f 万两，马匹 %s ✓" % (st.changying_treasury, inv))

    # ── 2) 常盈库与私库都计入 M2 ──
    m2_before = st.m2
    st.changying_treasury += 100.0
    assert abs((st.m2 - m2_before) - 100.0) < 1e-6, \
        "常盈库应计入 M2（增 100 万两，M2 应同步增 100 万两）"
    st.changying_treasury -= 100.0
    assert abs(st.m2 - m2_before) < 1e-6
    st.privy_treasury += 100.0
    assert abs((st.m2 - m2_before) - 100.0) < 1e-6, \
        "私库应计入 M2（增 100 万两，M2 应同步增 100 万两）"
    st.privy_treasury -= 100.0
    assert abs(st.m2 - m2_before) < 1e-6
    print("常盈库与私库均计入 M2（M2=%.2f = M₁%.2f + 国库%.2f + 私库%.2f + 地方%.2f + 常盈库%.2f）✓"
          % (st.m2, st.m1, st.treasury, st.privy_treasury, st.local_reserve, st.changying_treasury))

    # ── 3) 马匹定义由太仆寺自持，工部器物目录不再含马匹 ──
    works_inv = get_works_inventory(st)
    for hn in ("上等战马", "中等战马", "下等驭马"):
        assert hn not in works_inv, "工部仓储不应再有马匹：%s" % hn
    assert not [m for m in ARMAMENT_CATALOG.values() if m.get("category") == "horses"], \
        "工部器物目录不得再含马匹（定义归太仆寺自持）"
    assert [h["name"] for h in HORSE_MODELS] == ["上等战马", "中等战马", "下等驭马"], HORSE_MODELS
    assert [h["monthly_capacity"] for h in HORSE_MODELS] == [100, 200, 600], HORSE_MODELS
    assert [h["unit_cost"] for h in HORSE_MODELS] == [25.0, 20.0, 10.0], HORSE_MODELS
    print("工部目录无马匹、马匹定义由太仆寺自持（100/200/600 匹，25/20/10 两）✓")

    # ── 4) 月度孳牧：入库并自常盈库扣马价 ──
    cost = horse_monthly_cost_wan()
    assert abs(cost - 1.25) < 1e-9, \
        "月马价应为 100×25+200×20+600×10 = 12500 两 = 1.25 万两，实际 %s" % cost
    bal_before = st.changying_treasury
    s.run("j 1m")
    inv = get_taipusi_horses(st)
    assert inv["上等战马"] == 100 and inv["中等战马"] == 200 and inv["下等驭马"] == 600, \
        "月产应为 100/200/600，实际 %s" % inv
    assert abs(st.changying_treasury - (bal_before - cost)) < 1e-6, \
        "一月后常盈库应为 %.4f，实际 %.4f" % (bal_before - cost, st.changying_treasury)
    print("一月后马匹 %s，常盈库 %.2f 万两（扣马价 %.2f 万两）✓"
          % (inv, st.changying_treasury, cost))

    # ── 5) 常盈库不足则停产 ──
    st.changying_treasury = 0.5   # 小于 1.25
    inv_before = dict(get_taipusi_horses(st))
    s.run("j 1m")
    assert get_taipusi_horses(st) == inv_before, "常盈库不足时不应产马"
    assert st.changying_treasury == 0.5, "停产时不得扣银"
    print("常盈库不足 → 停产且不扣银 ✓")

    # ── 6) 公历元旦年抽 ──
    s2 = GameSession()
    st2 = s2.state
    probs = controlled_provinces(st2)
    assert len(probs) > 3, "已控制区数量异常：%d" % len(probs)
    print("已控制区 %d 个（动态统计）" % len(probs))

    # 起点 简化历 1627-08-25 = 公历 1627-10-03；再推 90 日即公历 1628-01-01
    s2.run("j 60d")
    g_mid = game_to_gregorian(st2.date)
    assert (g_mid.year, g_mid.month, g_mid.day) == (1627, 12, 2), \
        "推 60 日应到公历 1627-12-02，实际 %s" % g_mid
    res_before = st2.local_reserve
    bal_before = st2.changying_treasury
    lines = s2.run("j 30d")
    g = game_to_gregorian(st2.date)
    assert (g.year, g.month, g.day) == (1628, 1, 1), \
        "再推 30 日应正好落在公历 1628-01-01，实际 %s" % g
    assert abs((res_before - st2.local_reserve) - ANNUAL_EXTRACT_WAN) < 1e-6, \
        "地方窖藏应减少 50 万两，实际减少 %.4f" % (res_before - st2.local_reserve)
    assert abs((st2.changying_treasury - bal_before) - ANNUAL_EXTRACT_WAN) < 1e-6, \
        "常盈库应只增 50 万两（j Nd 不触发月度结算，故无马价支出），实际增 %.4f" \
        % (st2.changying_treasury - bal_before)
    assert st2.changying_last_year == 1628, "应记录年抽公历年份 1628"
    hit = [l for l in lines if "抽解马价银" in l]
    assert hit, "推进输出应含年抽提示"
    print("公历元旦年抽 ✓：%s" % hit[0].strip())

    # ── 7) 抽解银按"摊到下次抽取前"均摊（年额÷12），不落盘副本 ──
    from engine.depts_taipusi import changying_penalties, year_extract_wan
    pen = changying_penalties(st2)
    assert len(pen) == len(probs), \
        "应有 %d 个省摊到常盈库抽解，实际 %d" % (len(probs), len(pen))
    total_month = sum(v["monthly_extract_wan"] for v in pen.values())
    assert abs(total_month - ANNUAL_EXTRACT_WAN / 12.0) < 1e-4, \
        "各省每月均摊合计应为 50/12 = %.4f 万两，实际 %.4f" % (ANNUAL_EXTRACT_WAN / 12.0, total_month)
    assert abs(year_extract_wan(st2) - ANNUAL_EXTRACT_WAN) < 1e-9, "本年度抽解额应为 50 万两"
    for p in st2.provinces.values():
        assert not [m for m in ((p.get("local") or {}).get("custom_modifiers") or [])
                    if m.get("name") == CHANGYING_MOD_NAME], "常盈库修正不得写进省份副本"
    print("抽解银均摊 ✓：%d 省 × %.4f 万两/月 = %.4f 万两/月（实时派生，无省份副本）"
          % (len(pen), pen[probs[0][0]]["monthly_extract_wan"], total_month))

    # 开局即继承公历 1627 年正月朔的抽解（历史状况）
    s3 = GameSession()
    pen_open = changying_penalties(s3.state)
    assert len(pen_open) == len(controlled_provinces(s3.state))
    assert abs(sum(v["monthly_extract_wan"] for v in pen_open.values()) - ANNUAL_EXTRACT_WAN / 12.0) < 1e-4, \
        "开局应已带着 1627 年抽解额的每月均摊"
    print("开局历史状况 ✓：1627 年抽解 50 万两已在账上（%d 省，每月均摊）" % len(pen_open))

    # 同一年不重复抽
    res_mid = st2.local_reserve
    s2.run("j 1d")
    assert st2.local_reserve == res_mid, "同一公历年不得重复抽取"
    print("同一公历年只抽一次 ✓")

    # ── 8) 存档往返 ──
    from engine.state import WorldState
    d = st2.to_dict()
    ws = WorldState.from_dict(d)
    assert abs(ws.changying_treasury - st2.changying_treasury) < 1e-9
    assert ws.taipusi_horses == st2.taipusi_horses
    assert ws.changying_last_year == st2.changying_last_year
    assert len(ws.changying_ledger) == len(st2.changying_ledger)
    print("存读档往返 ✓（常盈库 %.2f 万两、马匹 %s）"
          % (ws.changying_treasury, ws.taipusi_horses))

    print("\nTAIPUSI OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
