# -*- coding: utf-8 -*-
"""回归测试：统一口径清理中的确定性缺陷。"""
import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_cleanup_regressions.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_cleanup_regressions_pos.json"))
from engine.session import GameSession
from engine.operations import create_modifier, tick_local_modifiers_monthly
from engine.variations import record_variation
from engine.variations import normalize_variation_type, format_variations_for_prompt


def test_local_modifier_expires():
    session = GameSession()
    state = session.state
    ok, _ = create_modifier(state, "陕西", "短期修正", "全国所有阶级产能+10.00%", duration_months=2)
    assert ok
    assert state.provinces["shaanxi"]["local"]["custom_modifiers"][0]["duration_months"] == 2
    tick_local_modifiers_monthly(state)
    assert state.provinces["shaanxi"]["local"]["custom_modifiers"][0]["duration_months"] == 1
    tick_local_modifiers_monthly(state)
    assert state.provinces["shaanxi"]["local"]["custom_modifiers"] == []


def test_variation_unknown_and_empty_are_explicit():
    assert normalize_variation_type("official", "新官到任") == "official_personnel"
    try:
        normalize_variation_type("not-a-real-type", "")
    except ValueError:
        pass
    else:
        raise AssertionError("未知变化类型必须报错")
    assert format_variations_for_prompt([]) == "（当前无变化）"


def test_variation_sequence_survives_save_and_load():
    session = GameSession()
    record_variation(session.state, "official", "新官到任")
    with tempfile.TemporaryDirectory() as tmpdir, patch("engine.session.SAVE_FILE", os.path.join(tmpdir, "save.json")):
        session._save()
        restored = GameSession()
        restored._load()
        record_variation(restored.state, "official", "继任官到任")
        assert restored.state.variation_pool[-1]["id"] == "var-002"


def test_local_market_stock_survives_monthly_settlement():
    from engine.economy import tick_material_monthly
    from engine.localdata import under_ming_control
    state = GameSession().state
    province = next(p for p in state.provinces.values()
                    if (p.get("local") or {}).get("classes") and under_ming_control(p))
    local = province["local"]
    local["local_market_stock"] = 25.0
    tick_material_monthly(state)
    assert local["local_market_stock"] >= 0.0


def test_m2_includes_all_declared_reserves():
    state = GameSession().state
    state.m1 = 1.0
    state.treasury = 2.0
    state.privy_treasury = 3.0
    state.local_reserve = 4.0
    state.changying_treasury = 5.0
    assert state.m2 == 15.0


def test_required_state_fields_are_not_serialization_fallbacks():
    state = GameSession().state
    del state.liaoxiang_monthly
    try:
        state.to_dict()
    except AttributeError:
        pass
    else:
        raise AssertionError("缺少必需的辽饷状态字段必须阻止存档")

    state = GameSession().state
    del state.changying_treasury
    try:
        _ = state.m2
    except AttributeError:
        pass
    else:
        raise AssertionError("缺少常盈库状态字段必须阻止 M2 静默少算")


def test_broken_army_roster_is_not_an_empty_roster():
    from engine.armies import load_armies_data
    with patch("engine.armies._RUNTIME_ARMIES", None), patch("engine.armies.ARMIES_FILE", "missing-armies.json"):
        try:
            load_armies_data()
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("军队名册缺失必须报错，不能伪装成空军队")


def test_non_ming_market_is_isolated():
    from engine.economy import tick_material_monthly
    from engine.localdata import under_ming_control
    state = GameSession().state
    non_ming = next(p for p in state.provinces.values()
                    if (p.get("local") or {}).get("classes") and not under_ming_control(p))
    local = non_ming["local"]
    # 本测试只验「外邦市场不串台」：先摘掉全部超控修正，免得开局修正改了需求、
    # 全国市场因供需变化而正常涨落，把这条断言测歪。
    keep_mods = dict(state.department_modifiers)
    state.department_modifiers = {}
    try:
        local["local_market_stock"] = 10000.0
        before_national = state.national_market_stock
        tick_material_monthly(state)
        assert state.national_market_stock == before_national
        assert local["local_market_stock"] >= 0.0
    finally:
        state.department_modifiers = keep_mods


if __name__ == "__main__":
    for test in (
        test_local_modifier_expires,
        test_variation_unknown_and_empty_are_explicit,
        test_variation_sequence_survives_save_and_load,
        test_local_market_stock_survives_monthly_settlement,
        test_m2_includes_all_declared_reserves,
        test_required_state_fields_are_not_serialization_fallbacks,
        test_broken_army_roster_is_not_an_empty_roster,
        test_non_ming_market_is_isolated,
    ):
        test()
    print("cleanup regressions passed")
