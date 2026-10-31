"""礼部科举教育系统测试：三场考试设置、两个人口栏共用一套修正、教育加成与结构性失业。"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_education.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_education_pos.json"))
from engine.session import GameSession
from engine import depts_rites as R
from engine import calendar as CAL


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_exams():
    s = GameSession()
    rows = R.exam_rows(s.state)
    ok([r["name"] for r in rows] == ["乡试", "会试", "殿试"], "三场考试：乡试/会试/殿试")
    for r in rows:
        ok(r["interval_years"] == 3, "%s 开局间隔三年" % r["name"])
        ok(r["subjects"], "%s 要写开设科目" % r["name"])
        ok(r["last_date"] == "未开考", "开局都没考过")
        ok(r["next_date"].endswith("日"), "要算出下次开考的公历日子")
    # 考过一场后，下次开考 = 上次 + 间隔
    s.state.exam_last["xiangshi"] = 1627
    r2 = next(r for r in R.exam_rows(s.state) if r["id"] == "xiangshi")
    ok(r2["next_year"] == 1630, "上次 1627 年乡试 → 下次 1630 年，实际 %s" % r2["next_year"])
    ok(r2["last_date"] != "未开考", "考过了要显示上次开考日")

    # 开考日要同步到日历
    d = r2["next_greg"]
    mk = CAL.get_calendar_markers_map(d["year"], d["month"], s.state)
    kinds = [m.get("type") for ms in mk.values() for m in ms]
    ok("exam" in kinds, "日历上要有科举开考的标记")
    print("考试设置 ✓ 三场、间隔相对上次考试、开考日已同步到日历")


def test_two_modifier_sets():
    """两套修正分开：受教育人口修正管受教育人口，受高等教育人口修正管受高等教育人口。"""
    s = GameSession()
    e = R.calc_education_summary(s.state)
    loc_names = [m["name"] for m in e["local_education"]["edu_modifiers"]]
    high_names = [m["name"] for m in e["higher_education"]["edu_modifiers"]]
    ok(loc_names == ["教育腐败", "考试僵化"], "受教育人口修正，实际 %s" % loc_names)
    ok(high_names == ["北京国子监", "南京国子监", "捐纳泛滥"], "受高等教育人口修正，实际 %s" % high_names)
    ok(abs(e["local_education"]["pct_mod"] - (-0.12 - 0.08)) < 1e-9, "受教育人口修正百分比相加")
    ok(abs(e["higher_education"]["pct_mod"] - (0.08 + 0.05 - 0.06)) < 1e-9, "受高等教育人口修正百分比相加")

    loc0 = e["local_education"]["annual_growth_expected"]
    high0 = e["higher_education"]["annual_growth_expected"]
    # 只动受教育那一套 → 只有受教育人口变化
    from engine.operations import create_modifier
    create_modifier(s.state, "礼部", "各府设义学", "受教育人口年增+50%", scope="全国")
    e2 = R.calc_education_summary(s.state)
    ok(e2["local_education"]["annual_growth_expected"] > loc0, "受教育人口预计增加随本栏修正上升")
    ok(abs(e2["higher_education"]["annual_growth_expected"] - high0) < 0.01,
       "另一栏不受影响：%.2f → %.2f" % (high0, e2["higher_education"]["annual_growth_expected"]))
    # 再动受高等教育那一套 → 只有受高等教育人口变化
    loc1 = e2["local_education"]["annual_growth_expected"]
    create_modifier(s.state, "礼部", "增扩国子监", "受高等教育人口年增+50%", scope="全国")
    e3 = R.calc_education_summary(s.state)
    ok(e3["higher_education"]["annual_growth_expected"] > high0, "受高等教育人口随本栏修正上升")
    ok(abs(e3["local_education"]["annual_growth_expected"] - loc1) < 0.01, "受教育那一栏不受影响")
    print("两套修正 ✓ 受教育人口修正 / 受高等教育人口修正 各管一栏，互不串台")


def test_bonus_formula():
    s = GameSession()
    e = R.calc_education_summary(s.state)
    tot = e["total_pop"]
    t1 = e["local_education"]["current_pop"] / tot
    t2 = e["higher_education"]["current_pop"] / tot
    ok(abs(e["bonus"]["local"]["pct"] - 1.549 * t1 ** 1.735 / (t1 ** 1.735 + 0.549)) < 1e-9,
       "受教育人口加成按给定公式")
    ok(abs(e["bonus"]["higher"]["pct"] - 1.00235 * t2 ** 1.955 / (t2 ** 1.955 + 0.00235)) < 1e-9,
       "受高等教育人口加成按给定公式")
    ok(abs(e["bonus"]["total_pct"] - (e["bonus"]["local"]["pct"] + e["bonus"]["higher"]["pct"])) < 1e-9,
       "合计加成 = 两项相加")
    ok(e["bonus"]["local"]["pct"] > 0, "开局就有教育加成")

    # 加成要真的进产能
    from engine.modifiers import build_local_modifier_index
    idx = build_local_modifier_index(s.state)
    some = next(iter(idx.values()))
    ok(some["buff_pct"], "产能里有加成类目")
    pct = R.education_bonus_pct(s.state)
    ok(pct > 0 and abs(pct - e["bonus"]["total_pct"]) < 1e-9, "产能取的就是这份教育加成")
    print("教育加成 ✓ 两条公式与合计、并真进全国各地区各阶级产能")


def test_structural_unemployment():
    s = GameSession()
    ok(not R.structural_unemployment(0.05)["active"], "占比未过一成不触发")
    u = R.structural_unemployment(0.1035)
    ok(u["active"] and u["steps"] == 3, "占比 10.35%% 超出 3 档（每档 0.1%%），实际 %s" % u["steps"])
    ok(abs(u["penalty_pct"] - 3 * 0.0002) < 1e-9, "每档全国产能减 0.02%%")

    # 触发时刷新「结构性失业」修正，回落则撤掉
    s.state.higher_educated_pop = s.state.local_educated_pop * 0 + 0.12 * 16980.0
    note = R.refresh_structural_unemployment(s.state)
    mod = (getattr(s.state, "department_modifiers", None) or {}).get("结构性失业")
    ok(mod and note, "占比过高要刷新结构性失业修正")
    eff = mod["effects"][0]
    ok(eff["target"] == "capacity" and eff["delta_pct"] < 0, "结构性失业是产能减成")
    s.state.higher_educated_pop = 15.0
    R.refresh_structural_unemployment(s.state)
    ok("结构性失业" not in (s.state.department_modifiers or {}), "占比回落即撤去该修正")
    print("结构性失业 ✓ 超 10% 触发并刷新修正，回落撤去")


def test_save_roundtrip():
    from engine.state import WorldState
    s = GameSession()
    from engine.operations import create_modifier
    create_modifier(s.state, "礼部", "各府设义学", "受教育人口年增+5%", scope="全国")
    create_modifier(s.state, "礼部", "增扩国子监", "受高等教育人口年增+10%", scope="全国")
    s.state.exam_last = {"xiangshi": 1630}
    ws = WorldState.from_dict(json.loads(json.dumps(s.state.to_dict())))
    ok(any(m["name"] == "各府设义学" for m in ws.department_modifiers.values()), "受教育人口修正随存档")
    ok(any(m["name"] == "增扩国子监" for m in ws.department_modifiers.values()), "受高等教育人口修正随存档")
    ok(ws.exam_last.get("xiangshi") == 1630, "考试记录随存档")
    print("存档 ✓ 教育修正与考试记录随存档往返")


if __name__ == "__main__":
    test_exams()
    test_two_modifier_sets()
    test_bonus_formula()
    test_structural_unemployment()
    test_save_roundtrip()
    print("── 礼部科举教育测试全部通过")
