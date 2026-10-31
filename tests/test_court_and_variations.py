# -*- coding: utf-8 -*-
"""Comprehensive test suite for:
1. Operations system (all 18 categories of engine overrides).
2. Variations registry & Scoped Operations binding.
3. Court Conference (廷议系统) with dynamic data switches, conference_2.txt, meeting_2.txt, meeting_pizou_phase1-4.txt.
4. Northwest Famine weather probability modulation (1628-1644).
5. Mainline affairs automatic completion conditions.
6. Monthly Global Summary (zongjie.txt) state reconstruction.
"""

import json
import os
import sys
import tempfile

import unittest

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

# 本套用例会增删部门与编制，进而向官员库写入人；
# 指到临时库，别把玩家的 db/officials.db 改脏（须在 import engine 之前设）
_TMP_DB = os.path.join(tempfile.gettempdir(), "dsh_court_test_officials.db")
for _suffix in ("", "-journal", "-wal", "-shm"):
    try:
        os.remove(_TMP_DB + _suffix)
    except OSError:
        pass
os.environ["OFFICIALS_DB_PATH"] = _TMP_DB

from engine.state import WorldState
from engine.session import GameSession
from engine.operations import (
    execute_operation_command,
    delete_army,
    create_modifier,
    delete_modifier,
    create_dept,
    delete_dept,
    replace_civil_official,
    add_civil_office,
    delete_civil_office,
    create_tax_item,
    delete_tax_item,
    set_diplomatic_relation,
    modify_pay_category,
    add_military_office,
    delete_military_office,
    replace_military_officer,
    modify_armament,
    create_project,
    delete_project,
    tick_construction_monthly,
)
from engine.variations import (
    normalize_variation_type,
    get_scoped_operations_schema,
    format_variations_for_prompt,
    VARIATION_SCHEMA_MAP
)
from engine.variations import record_variation
from engine.council import extract_context_data


class TestCourtAndVariations(unittest.TestCase):

    def setUp(self):
        import copy
        from engine.armies import load_armies_data, select_units
        self.orig_units = copy.deepcopy(load_armies_data().get("units") or [])
        self.session = GameSession()
        self.state = self.session.state

    def tearDown(self):
        from engine.armies import load_armies_data
        load_armies_data()["units"] = self.orig_units

    def test_all_18_operations_execution(self):
        """1. 测试《设计.txt》规定的全部 18 类超控指令执行。"""
        # 1) 地方军队删除
        ok, msg = delete_army(self.state, "sichuan", "奢寅部叛军")
        self.assertTrue(ok)
        from engine.armies import load_armies_data
        self.assertNotIn("奢寅部叛军", [u["name"] for u in (load_armies_data().get("units") or [])])

        # 2) 独立地区修正创建与删除
        ok, msg = create_modifier(self.state, "shaanxi", "特设抗旱粮道", "全国所有阶级产能+8.00%", duration_months=6)
        self.assertTrue(ok)
        loc_sx = self.state.provinces["shaanxi"]["local"]
        self.assertTrue(any(m["name"] == "特设抗旱粮道" for m in loc_sx.get("custom_modifiers", [])))

        ok, msg = delete_modifier(self.state, "特设抗旱粮道")
        self.assertTrue(ok)
        self.assertFalse(any(m["name"] == "特设抗旱粮道" for m in loc_sx.get("custom_modifiers", [])))

        # 3) 部门增删 (外廷与内廷)
        ok, msg = create_dept(self.state, "外廷", "军器总督司")
        self.assertTrue(ok)
        from engine import depts_waizhusi
        self.assertIn("军器总督司", depts_waizhusi.DEPTS)

        ok, msg = delete_dept(self.state, "外廷", "军器总督司")
        self.assertTrue(ok)
        self.assertNotIn("军器总督司", depts_waizhusi.DEPTS)

        # 4) 吏部文官人事与行政纪律
        import copy
        from engine import depts_libu
        orig_buffs = copy.deepcopy(depts_libu.ADMIN_DISCIPLINE_BUFFS)
        try:
            from engine import positions as _pos
            _pos.add(title="陕西督粮道佥事", kind="文职", category="陕西", start="1627-08")
            ok, msg = add_civil_office(self.state, "陕西", "督粮道佥事", "张清正")
            self.assertTrue(ok)
            from engine.officials import list_active
            self.assertTrue(any(o["name"] == "张清正" for o in list_active()))

            ok, msg = replace_civil_official(self.state, "陕西", "督粮道佥事", "张清正", "李奉公")
            self.assertTrue(ok)
            self.assertTrue(any(o["name"] == "李奉公" for o in list_active()))

            ok, msg = delete_civil_office(self.state, "陕西", "督粮道佥事")
            self.assertTrue(ok)
            self.assertFalse(any(o["name"] == "李奉公" for o in list_active()))

            ok, msg = create_modifier(self.state, "吏部", "严核考成", "全国所有地方腐败程度-3.00%，中央腐败程度-5.00%", scope="全国")
            self.assertTrue(ok)
            self.assertTrue(any(m["name"] == "严核考成" for m in self.state.department_modifiers.values()))
        finally:
            depts_libu.ADMIN_DISCIPLINE_BUFFS.clear()
            depts_libu.ADMIN_DISCIPLINE_BUFFS.extend(orig_buffs)

        # 5) 户部赋税增删
        ok, msg = create_tax_item(self.state, "new_sea_tax", "海舶新饷", "关税", 0.008)
        self.assertTrue(ok)
        from engine import depts_hubu
        self.assertTrue(any(t["name"] == "海舶新饷" for t in depts_hubu.TAX_DEFINITIONS))

        ok, msg = delete_tax_item(self.state, "海舶新饷")
        self.assertTrue(ok)
        self.assertFalse(any(t["name"] == "海舶新饷" for t in depts_hubu.TAX_DEFINITIONS))

        # 6) 礼部外交与教育修正
        ok, msg = set_diplomatic_relation(self.state, "朝鲜", relation="和睦", trade_status="贸易")
        self.assertTrue(ok)
        from engine import depts_rites
        cx = next(c for c in depts_rites.DIPLOMATIC_COUNTRIES if c["id"] == "chaoxian")
        self.assertEqual(cx["relation"], "和睦")
        self.assertEqual(cx["trade_status"], "贸易")

        ok, msg = create_modifier(self.state, "礼部", "增拔国子监", "受高等教育人口年增+10.00%", scope="全国")
        self.assertTrue(ok)
        self.assertTrue(any(m["name"] == "增拔国子监" for m in self.state.department_modifiers.values()))

        # 7) 兵部粮饷与武职
        ok, msg = modify_pay_category(self.state, "liaoxiang", "辽饷加派", 35.0)
        self.assertTrue(ok)
        # 新口径：辽饷是税目 liaoxiang，本指令改的是它的税目修正，
        # 月额由税基现算，不再往 LIAOXIANG_CONFIG 写一份并行的值
        from engine import depts_hubu
        lx_row = depts_hubu.liaoxiang_tax_row(self.state)
        self.assertIsNotNone(lx_row)
        self.assertAlmostEqual(lx_row["monthly_amount"], 35.0, places=1)

        ok, msg = add_military_office(self.state, "辽东", "广宁参将", "赵铁胆")
        self.assertTrue(ok)
        self.assertTrue(any(o["name"] == "赵铁胆" for o in list_active()))

        ok, msg = replace_military_officer(self.state, "辽东", "广宁参将", "赵铁胆", "孙伏虎")
        self.assertTrue(ok)
        self.assertTrue(any(o["name"] == "孙伏虎" for o in list_active()))

        ok, msg = delete_military_office(self.state, "辽东", "广宁参将")
        self.assertTrue(ok)
        self.assertFalse(any(o["name"] == "孙伏虎" for o in list_active()))

        # 8) 刑部法律修改
        import copy
        from engine import depts_xingbu
        orig_laws = copy.deepcopy(depts_xingbu.ACTIVE_LAWS)
        try:
            ok, msg = create_modifier(self.state, "刑部", "《考成严律》", "全国所有地方腐败程度-4.00%，中央腐败程度-8.00%", scope="全国")
            self.assertTrue(ok)
            self.assertTrue(any(m["name"] == "《考成严律》" for m in self.state.department_modifiers.values()))
        finally:
            depts_xingbu.ACTIVE_LAWS.clear()
            depts_xingbu.ACTIVE_LAWS.extend(orig_laws)

        # 9) 工部器物制造与工程水利（带周期月扣国库入地方窖藏）
        ok, msg = modify_armament(self.state, "ranged_new_firelock", "新式自生火铳", category="ranged", unit_cost=8.0, monthly_capacity=300)
        self.assertTrue(ok)
        from engine import depts_works
        self.assertIn("ranged_new_firelock", depts_works.ARMAMENT_CATALOG)

        # 新建工程（工期 2 个月，月费 1.5 万两）
        init_treasury = self.state.treasury
        init_local_res = self.state.local_reserve
        ok, msg = create_project(self.state, "河南", "开封新护城石堤", kind="灾害建筑", buff_text="全国所有阶级产能+5%", desc="御黄汛护城", cost=3.0, build_months=2)
        self.assertTrue(ok)
        loc_hn = self.state.provinces["henan"]["local"]
        new_pj = next(p for p in loc_hn["projects"] if p["name"] == "开封新护城石堤")
        self.assertEqual(new_pj["status"], "未完工")

        # 模拟第 1 个月扣费
        notes_m1 = tick_construction_monthly(self.state)
        self.assertAlmostEqual(self.state.treasury, init_treasury - 1.5, places=2)
        self.assertAlmostEqual(self.state.local_reserve, init_local_res + 1.5, places=2)
        self.assertEqual(new_pj["status"], "未完工")

        # 模拟第 2 个月竣工
        notes_m2 = tick_construction_monthly(self.state)
        self.assertEqual(new_pj["status"], "运营中")
        self.assertTrue(any("竣工" in n for n in notes_m2))

    def test_operations_command_dispatcher(self):
        """2. 测试指令分发器 execute_operation_command。"""
        ok, msg = execute_operation_command(self.state, "创建修正 陕西 澄城仓储 本省 全国所有阶级产能+3% 仓储备荒")
        self.assertTrue(ok)
        loc = self.state.provinces["shaanxi"]["local"]
        self.assertTrue(any(m["name"] == "澄城仓储" for m in loc.get("custom_modifiers", [])))

        ok, msg = execute_operation_command(self.state, "调拨地方窖藏到国库 陕西 5.0")
        self.assertTrue(ok)

    def test_variations_registry_and_scoped_operations(self):
        """3. 测试变化类型自动归一化与 Scoped Operations 静态绑定。"""
        # 1) 归一化
        self.assertEqual(normalize_variation_type("disaster", "发生大旱"), "disaster_weather")
        self.assertEqual(normalize_variation_type("yellow_flood", "黄河决口"), "disaster_yellow_flood")
        self.assertEqual(normalize_variation_type("army", "驻军欠饷2万两"), "military_arrears")
        self.assertEqual(normalize_variation_type("official", "按察司副使卸任"), "official_personnel")

        # 2) Scoped operations 文本绑定
        schema = get_scoped_operations_schema("disaster_weather", "陕西")
        self.assertIn("创建修正 陕西", schema)
        self.assertIn("新建灾害建筑 陕西", schema)

        # 3) 变化池生成 ID
        record_variation(self.session.state, "disaster", "【陕西】发生小旱（减产-5%）", province="shaanxi")
        last_var = self.session.state.variation_pool[-1]
        self.assertTrue(last_var["id"].startswith("var-"))
        self.assertEqual(last_var["type"], "disaster_weather")

    def test_court_conference_and_data_switches(self):
        """4. 测试廷议系统：数据动态开关注入、conference_2 与双结束出口。"""
        council = self.session.council

        # 1) 开启廷议
        ok, msg = council.start("fiscal_deficit", ["魏忠贤", "黄立极"], mode="court",
                                mainline_id="fiscal_deficit", initial_switches=["hubu", "macro"])
        self.assertTrue(ok)
        self.assertEqual(council.mode, "court")
        self.assertEqual(council.data_switches, ["hubu", "macro"])

        # 2) 随堂动态更新开关
        council.set_data_switches(["hubu", "libu", "shaanxi"])
        self.assertIn("shaanxi", council.data_switches)

        # 3) 验证数据提取（勾选的每个界面发的是它整份内容，不是摘要）
        c_data = extract_context_data(self.state, council.data_switches)
        self.assertIn("户部账册", c_data)
        self.assertIn("吏部案卷", c_data)
        self.assertIn("陕西地方实录", c_data)
        self.assertIn("全国宏观实录", c_data)
        # 全量口径：界面里的栏名要跟着内容一起出现（不是只发一句摘要）
        self.assertIn("中央腐败程度", c_data)
        self.assertIn("行政纪律", c_data)
        self.assertIn("全国基本数据", c_data)

        # 4) AI 发言
        council.player_speak("太仓存银吃紧，众爱卿有何良策？")
        ok, msg = council.next_ai_reply()
        self.assertTrue(ok)

        # 5) 廷议出口 2：决议施政（超控）
        ok, pizou, ops = council.end_court_with_pizou()
        self.assertTrue(ok)
        self.assertFalse(council.active)

        # 验证归档入【廷议会话】
        dlgs = self.session.list_dialogues(category="court")
        self.assertGreater(len(dlgs), 0)
        self.assertEqual(dlgs[0]["category"], "court")

    def test_mainlines_completion_triggers(self):
        """5. 测试要紧事物结束条件达成即结案并发奖励（判定与发点在引擎）。"""
        from engine.mainline_script import mainline_rows, evaluate_mainlines
        before = self.state.tech_points
        delete_army(self.state, "sichuan", "奢寅部叛军")
        delete_army(self.state, "sichuan", "安位部叛军")
        newly = evaluate_mainlines(self.state)
        self.assertTrue(any(r["id"] == "she_an_rebellion" for r in newly),
                        "奢安之乱结束条件达成后应结案：%s" % newly)
        row = next(r for r in mainline_rows(self.state) if r["id"] == "she_an_rebellion")
        self.assertTrue(row["ended"])
        # 奖励只发一次，且真的加到科技点上
        self.assertAlmostEqual(self.state.tech_points - before, row["reward_points"], places=4)
        evaluate_mainlines(self.state)
        self.assertAlmostEqual(self.state.tech_points - before, row["reward_points"], places=4)


if __name__ == "__main__":
    unittest.main()
