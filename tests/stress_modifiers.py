"""修正系统压力测试（【v0.3.7】· 待办第 16 条）。

跑极端假设、看系统能否正常返回：所有修正类型 × 建/改/删 × 边界值（0、负数、±100%、
超长月数、空名、重名）＋ 脏数据（缺字段、类型错的 effects），检查
① 不抛异常 ② 生效可验 ③ 删除后回到基线（可逆） ④ 脏数据下取数不崩。
"""

import copy
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

from engine.session import GameSession
from engine import economy
from engine import modifiers as M
from engine.depts_rites import calc_prestige
from engine.operations import (execute_operation_command, create_modifier,
                               delete_modifier,
                               modify_population_rate_modifier)


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


TARGETS = ["financial_rate", "prestige", "central_corruption", "all_local_corruption",
           "prov_corruption", "capacity", "attitude"]


def test_all_targets_build_and_delete():
    """每种 target 都要能建、能列出、能删，且删除后回到基线。"""
    s = GameSession()
    st = s.state
    before = {
        "prestige": calc_prestige(st)["value"],
        "capacity": economy.local_production(st.provinces["shaanxi"]["local"],
                                             M.build_local_modifier_index(st).get("shaanxi")),
        "attitude": float(st.provinces["shaanxi"]["local"]["classes"]["农民"]["attitude"]),
    }
    built = []
    for i, target in enumerate(TARGETS):
        name = "压测修正%d" % i
        content = {
            "capacity": "全国所有阶级产能+3%",
            "attitude": "全国所有阶级态度+4",
            "prestige": "皇室威望+5%",
            "financial_rate": "替代率 f+5%",
            "central_corruption": "中央腐败程度+3%",
            "all_local_corruption": "全国所有地方腐败程度+2%",
            "prov_corruption": "陕西地区腐败程度+6%",
        }[target]
        okk, msg = create_modifier(st, "吏部", name, content)
        ok(okk, "建立 %s 修正应成功：%s" % (target, msg))
        built.append(name)
    rows = M.get_central_modifier_rows(st) + M.get_province_local_modifiers(st, "shaanxi").get("rows", [])
    # 威望与产能要真的动
    ok(calc_prestige(st)["value"] > before["prestige"], "威望修正要生效")
    after_prod = economy.local_production(st.provinces["shaanxi"]["local"],
                                         M.build_local_modifier_index(st).get("shaanxi"))
    ok(abs(after_prod - before["capacity"]) > 1e-6, "产能修正要生效")
    for name in built:
        okk, msg = delete_modifier(st, name)
        ok(okk, "删除 %s 应成功：%s" % (name, msg))
    ok(abs(calc_prestige(st)["value"] - before["prestige"]) < 0.6,
       "删除后威望应回到基线：%.1f vs %.1f" % (calc_prestige(st)["value"], before["prestige"]))
    back_prod = economy.local_production(st.provinces["shaanxi"]["local"],
                                        M.build_local_modifier_index(st).get("shaanxi"))
    ok(abs(back_prod - before["capacity"]) < 0.51, "删除后产能应回到基线：%.2f vs %.2f"
       % (back_prod, before["capacity"]))
    print("修正全类型 ✓ %d 种 target 建/删可用，威望与产能生效且可逆" % len(TARGETS))


def test_boundary_values():
    """边界值：±100%、超大、0 月数、空名、重名。"""
    s = GameSession()
    st = s.state
    base = economy.local_production(st.provinces["shaanxi"]["local"],
                                   M.build_local_modifier_index(st).get("shaanxi"))
    cases = [
        ("减到底", "全国所有阶级产能-100%"),
        ("加到爆", "全国所有阶级产能+10000%"),
        ("零点", "全国所有阶级产能+0%"),
    ]
    for name, content in cases:
        okk, msg = create_modifier(st, "吏部", name, content)
        ok(okk, "边界值 %s 应可建立：%s" % (name, msg))
        prod = economy.local_production(st.provinces["shaanxi"]["local"],
                                        M.build_local_modifier_index(st).get("shaanxi"))
        ok(prod >= 0.0, "%s 后产能不得为负：%.2f" % (name, prod))
        delete_modifier(st, name)
    # 重名覆盖而不是叠加
    create_modifier(st, "吏部", "同名", "全国所有阶级产能+10%")
    create_modifier(st, "吏部", "同名", "全国所有阶级产能+20%")
    same = [d for d in st.department_modifiers.values() if d.get("name") == "同名"]
    ok(len(same) == 1, "同名修正应当覆盖而不是叠加：%d 条" % len(same))
    delete_modifier(st, "同名")
    # 空名与超长名
    for name, expect in (("", False), ("长" * 300, True)):
        okk, _ = create_modifier(st, "吏部", name, "全国所有阶级产能+1%")
        ok(okk == expect, "空名必须拒绝、超长名不应崩：%r -> %s" % (name[:6], okk))
        if okk:
            delete_modifier(st, name)
    # 删除不存在的
    okk, msg = delete_modifier(st, "并不存在的修正")
    ok(isinstance(okk, bool) and bool(msg), "删除不存在的修正不应崩：%s" % msg)
    # 地区修正的持续月数边界
    for months in (0, -3, 9999):
        okk, msg = create_modifier(st, "陕西", "压测地区修正", "全国所有阶级产能+10.00%", duration_months=months)
        ok(okk, "月数 %s 应可建立：%s" % (months, msg))
        delete_modifier(st, "压测地区修正")
    print("边界值 ✓ ±100%%/超大/0月数/空名/超长名/重名/删不存在，全部正常返回且产能不为负")


def test_education_and_population_modifiers():
    """两套教育修正与人口比例修正：建/改/删，各自只管自己那一栏。"""
    s = GameSession()
    st = s.state
    from engine.depts_rites import calc_education_summary
    e0 = calc_education_summary(st)
    loc0 = e0["local_education"]["annual_growth_expected"]
    high0 = e0["higher_education"]["annual_growth_expected"]
    create_modifier(st, "礼部", "压测义学", "受教育人口年增+37.00%", scope="全国", intro="各府设义学")
    e1 = calc_education_summary(st)
    ok(e1["local_education"]["annual_growth_expected"] != loc0, "受教育人口年增要变")
    ok(abs(e1["higher_education"]["annual_growth_expected"] - high0) < 0.01, "另一栏不受影响")
    create_modifier(st, "礼部", "压测国子监", "受高等教育人口年增-50.00%", scope="全国", intro="裁撤监生")
    e2 = calc_education_summary(st)
    ok(e2["higher_education"]["annual_growth_expected"] < high0, "受高等教育人口年增要降")
    delete_modifier(st, "压测义学")
    delete_modifier(st, "压测国子监")
    e3 = calc_education_summary(st)
    ok(abs(e3["local_education"]["annual_growth_expected"] - loc0) < 0.02, "删后受教育栏回基线")
    ok(abs(e3["higher_education"]["annual_growth_expected"] - high0) < 0.02, "删后高等栏回基线")
    # 人口比例修正
    r0 = economy.population_rates(st)["birth"]
    modify_population_rate_modifier(st, "压测人口", birth_delta=0.05)
    ok(abs(economy.population_rates(st)["birth"] - (r0 + 0.05)) < 1e-9, "人口修正要生效")
    modify_population_rate_modifier(st, "压测人口", delete=True)
    ok(abs(economy.population_rates(st)["birth"] - r0) < 1e-9, "删后回基线")
    print("教育/人口修正 ✓ 受教育与受高等教育互不串台，人口比例修正可逆")


def test_dirty_effects(): 
    """脏数据：缺字段、类型错、超范围的 effects，取数不崩。"""
    s = GameSession()
    st = s.state
    st.department_modifiers["脏数据1"] = {"name": "脏数据1", "effects": [
        {"target": "capacity"},                        # 缺 classes/delta
        {"target": "capacity", "classes": None, "delta_pct": None},
        {"target": "并不存在的目标", "delta": "不是数字"},
    ]}
    st.department_modifiers["脏数据2"] = {"name": "脏数据2", "effects": None}
    st.department_modifiers["脏数据3"] = {"name": "脏数据3"}
    # 这些调用都不该抛异常
    M.get_central_modifier_rows(st)
    M.build_local_modifier_index(st)
    M.get_province_all_modifiers(st, "shaanxi")
    economy.local_production(st.provinces["shaanxi"]["local"],
                            M.build_local_modifier_index(st).get("shaanxi"))
    ok(st.to_dict(), "脏数据下存档序列化仍要成功")
    for k in list(st.department_modifiers):
        if k.startswith("脏数据"):
            del st.department_modifiers[k]
    print("脏数据 ✓ 缺字段/类型错/未知 target/effects 为 None，取数与存档都不崩")


if __name__ == "__main__":
    test_all_targets_build_and_delete()
    test_boundary_values()
    test_education_and_population_modifiers()
    test_dirty_effects()
    print("── 修正系统压力测试通过")
