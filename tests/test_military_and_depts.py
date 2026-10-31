"""Test suite for Armies, Works Department, War Department and Local Modifiers.
"""
import sys
import os
import json

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_military_and_depts.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_military_and_depts_pos.json"))
from engine.state import WorldState
from engine.armies import get_all_armies, update_army_arrears, select_units
from engine.depts_works import get_works_overview, ARMAMENT_CATALOG
from engine.depts_war import get_war_overview, calc_liaoxiang_apportionment, get_relay_stations_info
from engine import localdata
from engine.economy import class_capacity
from engine.modifiers import get_province_local_modifiers


def test_armies_structure():
    with open("world/provinces.json", encoding="utf-8") as f:
        provinces = json.load(f)
    st = WorldState(provinces)

    armies = get_all_armies(st)
    # 一份名册靠属性取用：category 定编制、owner 定归属
    mobiles = select_units(armies, category="mobile")
    garrisons = select_units(armies, category="garrison", owner="ming")
    rebels = select_units(armies, category="mobile", owner="叛军")
    assert len(rebels) == 4, f"Expected 4 rebel armies (叛军), got {len(rebels)}"

    assert len(mobiles) == 7, f"Expected 7 mobile units (3 主力 + 4 叛军), got {len(mobiles)}"
    mobile_names = {m["name"] for m in mobiles}
    for nm in ("白杆兵", "满清八旗", "蒙古盟军"):
        assert nm in mobile_names, f"missing mobile army {nm}"

    # Check equipment details
    for m in mobiles:
        eq = m["equipment"]
        assert len(eq["armors"]) > 0
        assert len(eq["melee"]) > 0
        assert len(eq["ranged"]) > 0
        assert len(eq["horses"]) > 0
        assert "arrears_wan" in m
        assert "monthly_pay" in m
    # 八旗另有火炮；蒙古盟军纯骑无炮
    baqi = next(m for m in mobiles if m["id"] == "mobile_baqi")
    meng = next(m for m in mobiles if m["id"] == "mobile_mongol_alliance")
    assert len(baqi["equipment"]["artillery"]) > 0
    assert len(meng["equipment"]["artillery"]) == 0
    assert baqi["count"] == 50000 and baqi["current_location"] == "shengjing"
    assert meng["count"] == 2000 and meng["current_location"] == "nenkeerqin"

    assert len(garrisons) > 10, "Expected garrison units for Ming provinces"
    # 非明属地方军（满清各部牛录、部落兵）同样在册，按 owner 属性识别
    gnames = {g["name"] for g in select_units(armies, category="garrison")}
    for nm in ("盛京地区部落兵", "海西地区部落兵", "嫩科尔沁地区蒙古牛录"):
        assert nm in gnames, f"missing garrison {nm}"

    # Test arrears update and penalty (每欠 1 万两减少所在地全阶级产能 1%)
    ok = update_army_arrears("gar_shaanxi", 2.50, st)
    assert ok is True
    armies_updated = get_all_armies(st)
    gar_sx = next(x for x in select_units(get_all_armies(st), category="garrison") if x["id"] == "gar_shaanxi")
    assert gar_sx["arrears_wan"] == 2.50
    # 【v0.3.7】军队上的 penalty_pct 死字段已删：欠饷扣的是物质绝对量，由 modifiers 折算
    live_sx0 = get_province_local_modifiers(st, "shaanxi")
    assert live_sx0["arrears_wan"] == 2.50

    # 欠饷减产由修正系统实时读取兵部军册得出（省份里不再存副本）
    live_sx = get_province_local_modifiers(st, "shaanxi")
    assert live_sx["arrears_wan"] == 2.50, live_sx["arrears_wan"]
    # 欠饷按"银两 × 1两=1单位物质 ÷ 物价指数"折成物质绝对扣减，与税负同口径
    assert live_sx["flat_penalty"]["农民"] >= 2.50 - 1e-6, live_sx["flat_penalty"]["农民"]
    loc_sx = st.provinces["shaanxi"]["local"]
    assert "garrison" not in loc_sx, "省份不应存镇军副本（人数/军饷/发饷比例全在名册）"
    print("test_armies_structure passed")


def test_works_overview():
    with open("world/provinces.json", encoding="utf-8") as f:
        provinces = json.load(f)
    st = WorldState(provinces)

    works = get_works_overview(st)
    assert "engineering" in works
    assert "armaments" in works
    assert len(works["engineering"]["projects"]) > 0
    # 水利管理表已并入工程建设表，类别改称灾害建筑
    assert "waterworks" not in works
    assert any(p["kind"] == "灾害建筑" for p in works["engineering"]["projects"])
    assert len(works["armaments"]) == len(ARMAMENT_CATALOG)
    print("test_works_overview passed")


def test_war_and_liaoxiang():
    with open("world/provinces.json", encoding="utf-8") as f:
        provinces = json.load(f)
    st = WorldState(provinces)

    war_info = get_war_overview(st)
    assert len(war_info["officers"]) > 0
    assert len(war_info["relays"]["stations"]) > 0

    lx = calc_liaoxiang_apportionment(st)
    apps = lx["apportionment"]
    assert "beizhili" in apps
    assert "nanzhili" in apps
    assert apps["nanzhili"]["allocated_silver_monthly"] > 0
    # 【v0.3.7】辽饷按物质折算扣减，旧的 penalty_pct 已删
    assert apps["nanzhili"]["matter_loss"] > 0
    print("test_war_and_liaoxiang passed")


def test_no_fabricated_arrears():
    """军册无欠饷记录时不得凭人数估算欠饷（未欠饷即 0，且省份不存任何镇军副本）。"""
    from engine.armies import clear_arrears
    with open("world/provinces.json", encoding="utf-8") as f:
        provinces = json.load(f)
    st = WorldState(provinces)
    clear_arrears(st)   # 军册欠饷归零，确保与其他用例互不影响

    for pid, p in st.provinces.items():
        loc = p.get("local") or {}
        live = get_province_local_modifiers(st, pid)
        assert live["arrears_wan"] == 0.0, \
            f"{p.get('name')} 欠饷归零后应为 0，实际 {live['arrears_wan']}"
        assert "garrison" not in loc, f"{p.get('name')} 省份不应存镇军副本"
    print("test_no_fabricated_arrears passed")


def test_no_province_copies():
    """部门修正一律实时读取：打开界面/计算修正不得向省份写入任何副本。"""
    import copy
    with open("world/provinces.json", encoding="utf-8") as f:
        provinces = json.load(f)
    st = WorldState(provinces)
    before = copy.deepcopy({pid: p.get("local") for pid, p in st.provinces.items()})

    from engine.modifiers import get_province_all_modifiers
    caps = [get_province_all_modifiers(st, "beizhili")["classes_summary"]["农民"]["capacity"]
            for _ in range(3)]
    assert caps[0] == caps[1] == caps[2], f"重复读取结果不一致，说明有落盘副作用：{caps}"
    after = {pid: p.get("local") for pid, p in st.provinces.items()}
    for pid in before:
        assert before[pid] == after[pid], f"{pid} 的地方数据被修正计算改动了"
    print("test_no_province_copies passed")


if __name__ == "__main__":
    test_armies_structure()
    test_works_overview()
    test_war_and_liaoxiang()
    test_no_fabricated_arrears()
    test_no_province_copies()
    print("\nALL MILITARY & DEPTS TESTS PASSED!")
