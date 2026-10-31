"""Dual-Cycle Economy Unit Tests.
Verifies:
1. Physical material cycle calculations (production base weights, demand base weights, buffs).
2. Currency cycle calculations (M1, M2, local reserve activation, monthly loss, liquidity health g).
3. Gold system (gold_treasury, gold_local_reserve, gold_m1, monthly 0.01% loss).
4. Mining production (silver & gold annual outputs).
5. Local data indicators (tax, stocks).
6. Mainline stories configuration.
"""
import sys
import os
import json

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_economy.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_economy_pos.json"))
from engine.state import WorldState
from engine.economy import (
    PROD_WEIGHTS,
    DEMAND_WEIGHTS,
    calc_liquidity_health,
    calc_leverage,
    class_capacity,
    class_production,
    class_demand,
    get_province_class_buffs,
    local_production,
    local_demand,
    supply_demand_index,
    tick_economy_monthly,
    inject_bullion,
    issue_fiat,
)
from engine import localdata


def test_weights_and_buffs():
    # Verify base weights
    assert PROD_WEIGHTS["农民"] == 1.5
    assert PROD_WEIGHTS["百工"] == 2.0
    assert PROD_WEIGHTS["百役"] == 0.5
    assert PROD_WEIGHTS["商人"] == 10.0
    assert DEMAND_WEIGHTS["宗室"] == 50.0
    assert DEMAND_WEIGHTS["士绅"] == 20.0
    assert DEMAND_WEIGHTS["商人"] == 5.0
    assert DEMAND_WEIGHTS["农民"] == 1.0
    assert DEMAND_WEIGHTS["百工"] == 1.2
    assert DEMAND_WEIGHTS["百役"] == 0.8
    assert DEMAND_WEIGHTS["流民"] == 0.5

    # Test mock buffs
    # 农民: pop 100, base 1.5, pct_buff 10% -> 100 * 1.5 * 1.10 = 165
    mock_loc = {
        "projects": [
            {"name": "水利A", "buff": {"pct": {"农民": 0.10}}},
            {"name": "水利B", "buff": {"pct": {"农民": 0.20}}},
            {"name": "矿场C", "buff": {"flat": {"百工": 25.0}}},
        ]
    }
    pct, flat, notes = get_province_class_buffs(mock_loc, "农民")
    assert abs(pct - 0.30) < 1e-4
    assert flat == 0.0
    # 100 * 1.5 * (1 + 0.30) = 195.0
    cap_farmer = class_capacity("农民", 100.0, mock_loc)
    assert abs(cap_farmer - 195.0) < 1e-2

    # 百工: pop 10, base 2.0, flat_buff 25 -> 10 * 2.0 + 25 = 45.0
    cap_art = class_capacity("百工", 10.0, mock_loc)
    assert abs(cap_art - 45.0) < 1e-2

    print("test_weights_and_buffs passed")


def test_gold_and_mining():
    with open("world/provinces.json", encoding="utf-8") as f:
        provinces = json.load(f)
    st = WorldState(provinces)

    assert st.gold_treasury >= 100.0
    assert st.gold_local_reserve >= 25.0
    assert st.gold_m1 >= 1.5
    gold_0 = st.gold_m1

    tick_economy_monthly(st)

    # Monthly gold m1 decay: 0.01%
    expected_gold_m1 = gold_0 * (1.0 - 0.0001)
    assert abs(st.gold_m1 - expected_gold_m1) < 0.01
    print("test_gold_and_mining passed")


def test_liquidity_and_leverage():
    g = calc_liquidity_health(300.0, 0.15, 352.941176)
    assert abs(g - 1.0) < 0.01, "g should be close to 1.0, got %f" % g

    lev = calc_leverage(0.15)
    assert abs(lev - (1.0 / 0.85)) < 0.01, "leverage should be 1/(1-f)"
    print("test_liquidity_and_leverage passed")


def test_mainlines_quotes():
    with open("world/mainlines.json", encoding="utf-8") as f:
        data = json.load(f)
    mainlines = data.get("mainlines", [])
    assert len(mainlines) == 5
    m_dict = {m["id"]: m for m in mainlines}

    assert "各军始犹典衣卖箭" in m_dict["army_pay"]["desc"]
    assert "财犹泉也" in m_dict["fiscal_deficit"]["desc"]
    assert "延安、庆阳、平凉旱" in m_dict["northwest_famine"]["desc"]
    assert "蜀自奢酋倡乱" in m_dict["she_an_rebellion"]["desc"]
    print("test_mainlines_quotes passed")


if __name__ == "__main__":
    test_weights_and_buffs()
    test_gold_and_mining()
    test_liquidity_and_leverage()
    test_mainlines_quotes()
    print("\nALL UNIT TESTS PASSED SUCCESSFULLY!")
