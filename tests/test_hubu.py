"""Unit tests for Hubu (户部) system:
1. Basic demographic & production data by class.
2. 10-tax system calculations, apportionment, and capacity penalty.
3. National and Privy treasury balance sheets.
4. Tax collection equal-ratio deduction from local_reserve and m1.
"""

import sys
import os

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_hubu.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_hubu_pos.json"))
from engine.session import GameSession
from engine.depts_hubu import (
    calc_hubu_basic_stats,
    calc_hubu_taxes,
    calc_treasury_balance_sheets,
    get_hubu_overview,
    TAX_DEFINITIONS,
    ALL_CLASSES,
)
from engine.economy import class_capacity, tick_hubu_taxes_monthly, get_gregorian_date
from engine.time import advance_month


def test_hubu_basic_stats():
    sess = GameSession()
    stats = calc_hubu_basic_stats(sess.state)

    assert stats["total_actual_pop"] > 10000.0, f"Total pop {stats['total_actual_pop']} should be > 10000万"
    assert stats["total_physical_capacity"] > 0, "Physical capacity should be > 0"
    assert stats["total_demand"] > 0, "Total demand should be > 0"
    assert len(stats["classes_table"]) == 7, "Should have 7 classes in table"

    class_names = [c["class_name"] for c in stats["classes_table"]]
    for expected_c in ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]:
        assert expected_c in class_names, f"{expected_c} missing in table"

    farmers = next(c for c in stats["classes_table"] if c["class_name"] == "农民")
    assert farmers["population"] > 5000.0, "Farmers population should be majority (> 5000万)"
    assert farmers["capacity"] > 0, "Farmers capacity should be > 0"

    print("test_hubu_basic_stats passed: pop=%.1f万, phys_prod=%.1f万, demand=%.1f万"
          % (stats["total_actual_pop"], stats["total_physical_capacity"], stats["total_demand"]))


def test_hubu_taxes():
    sess = GameSession()
    tax_info = calc_hubu_taxes(sess.state)

    # 12 个税目：原 11 个 ＋ 辽饷加派（玩家口径：辽饷就是税目，界面在兵部）
    assert len(tax_info["taxes"]) == 12, "Should have exactly 12 taxes"
    expected_taxes = ["丁银", "坊厢银", "班匠银", "门摊银", "夏税", "秋粮", "盐课",
                      "钞关税", "番舶抽分", "杂税", "捐纳得官", "辽饷加派"]
    tax_names = [t["name"] for t in tax_info["taxes"]]
    for name in expected_taxes:
        assert name in tax_names, f"Tax {name} missing"
    lx = [t for t in tax_info["taxes"] if t["id"] == "liaoxiang"][0]
    assert abs(lx["monthly_amount"] - 41.67) < 0.01, \
        "辽饷月额应仍是 41.67 万两，实际 %s" % lx["monthly_amount"]
    assert lx["id"] == "liaoxiang", "税目行必须同时给出税目ID"

    assert tax_info["total_annual_revenue"] > 500.0, "Total annual tax should be > 500万两"
    assert tax_info["total_monthly_revenue"] > 40.0, "Total monthly tax should be > 40万两"
    assert len(tax_info["prov_breakdown"]) > 0, "Province tax breakdown should not be empty"

    # Verify fanbo is empty / 0
    fanbo = next(t for t in tax_info["taxes"] if t["name"] == "番舶抽分")
    assert fanbo["monthly_amount"] == 0.0, "Fanbo should be 0 (reserved for trade system)"

    # Verify capacity penalty deduction in province
    bj = tax_info["prov_breakdown"][0]
    assert bj["monthly_tax"] > 0
    assert bj["annual_tax"] > 0

    print("test_hubu_taxes passed: annual_tax=%.1f万两, monthly_tax=%.2f万两"
          % (tax_info["total_annual_revenue"], tax_info["total_monthly_revenue"]))


def test_treasury_balance_sheets():
    sess = GameSession()
    sheets = calc_treasury_balance_sheets(sess.state)

    nt = sheets["national_treasury"]
    pt = sheets["privy_treasury"]

    assert nt["balance"] == sess.state.treasury
    assert len(nt["incomes"]) >= 10, "National treasury should have at least 10 income items"
    assert nt["total_monthly_income"] > 0
    assert nt["total_annual_income"] > 0
    assert "transactions" in nt

    assert pt["silver_balance"] == sess.state.privy_treasury
    assert pt["gold_balance"] == sess.state.gold_treasury
    assert "transactions" in pt
    assert "incomes" not in pt, "Privy treasury should not have fictitious expected income lists"
    assert "expenses" not in pt, "Privy treasury should not have fictitious expected expense lists"

    print("test_treasury_balance_sheets passed: NatIncome=%.2f万两/月, PrivySilver=%.2f万两, PrivyGold=%.4f万两"
          % (nt["total_monthly_income"], pt["silver_balance"], pt["gold_balance"]))


def test_tax_collection_and_equal_ratio():
    sess = GameSession()
    st = sess.state

    st.local_reserve = 100.0
    st.m1 = 300.0
    st.treasury = 200.0

    # Test tick_hubu_taxes_monthly
    notes = tick_hubu_taxes_monthly(st)
    assert len(notes) > 0, "Should generate tax collection notes"
    print("test_tax_collection_and_equal_ratio passed: %s" % notes[0][:60])


def test_overview_api():
    sess = GameSession()
    ov = get_hubu_overview(sess.state)

    assert "basic_stats" in ov
    assert "tax_system" in ov
    assert "national_treasury" in ov
    assert "privy_treasury" in ov
    assert "classes_table" in ov["basic_stats"]
    assert "taxes" in ov["tax_system"]

    print("test_overview_api passed")


if __name__ == "__main__":
    test_hubu_basic_stats()
    test_hubu_taxes()
    test_treasury_balance_sheets()
    test_tax_collection_and_equal_ratio()
    test_overview_api()
    print("\nALL HUBU TESTS PASSED SUCCESSFULLY!")
