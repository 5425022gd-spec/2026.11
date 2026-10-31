"""Test suite for Province Modifiers System (engine/modifiers.py).

Tests:
1. Military modifiers (Liaoxiang apportionment, garrison pay arrears, stationed mobile army arrears).
2. Department modifiers (Civil Personnel discipline, Justice laws, Revenue tax apportionment, Rites religion).
3. Weather and disaster modifiers (disasters, plague, yellow flood, earthquake).
4. Project and mitigation modifiers (engineering buffs, Haining seawall, Lingzhou stone dike, mines).
5. Interprovincial movement modifiers.
6. Supply-demand modifiers and the class attitude drift.
7. Comprehensive per-class capacity and net modifier calculations.
8. API / Province view integration.
"""

import sys
import os
import unittest
import json

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_modifiers.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_modifiers_pos.json"))
from engine.state import WorldState
from engine.modifiers import (
    get_province_military_modifiers,
    get_province_dept_modifiers,
    get_province_weather_modifiers,
    get_province_project_modifiers,
    get_province_movement_modifiers,
    get_province_supply_demand_modifiers,
    get_province_class_capacity_summary,
    get_province_all_modifiers,
)
from engine.armies import update_army_arrears


class TestProvinceModifiers(unittest.TestCase):

    def setUp(self):
        with open("world/provinces.json", encoding="utf-8") as f:
            provinces = json.load(f)
        self.state = WorldState(provinces)

    def tearDown(self):
        # 军册是进程级单例：清零后必须让它重新按 world/armies.json 装载，
        # 否则后续用例读到的是 0 欠饷（test_unzhixian_and_war 的开局欠饷断言会挂）
        from engine import armies
        from engine.armies import clear_arrears
        clear_arrears(self.state)
        armies._RUNTIME_ARMIES = None

    def test_military_modifiers(self):
        """1. 测试军饷与防务修正：辽饷分摊与欠饷记账。"""
        # 测试默认北直隶
        mil = get_province_military_modifiers(self.state, "beizhili")
        self.assertIn("liaoxiang", mil)
        self.assertIn("army_arrears", mil)

        # 辽饷分摊应大于 0
        lx = mil["liaoxiang"]
        self.assertGreater(lx["allocated_silver_monthly"], 0)
        self.assertEqual(lx["affected_classes"], ["商人", "农民", "百工", "百役"])

        # 设置欠饷：记账进兵部名册
        update_army_arrears("gar_beizhili", 3.0, self.state)
        mil_updated = get_province_military_modifiers(self.state, "beizhili")
        arr = mil_updated["army_arrears"]
        self.assertEqual(arr["total_arrears_wan"], 3.0)

    def test_dept_modifiers(self):
        """2. 测试六部政纪、法律、赋税与宗教修正。"""
        dept = get_province_dept_modifiers(self.state, "shaanxi")

        # 腐败修正 (50 * 1.32 = 66.0)
        corr = dept["corruption"]
        self.assertEqual(corr["base"], 50.0)
        self.assertAlmostEqual(corr["mult_sum"], 0.32, places=4)
        self.assertEqual(corr["value"], 66.0)

        # 承载力修正 (base * 0.90)
        carry = dept["carrying"]
        self.assertAlmostEqual(carry["mult_sum"], -0.10, places=4)
        self.assertAlmostEqual(carry["value"], carry["base"] * 0.90, places=1)

        # 户部税赋
        tax = dept["taxation"]
        self.assertGreater(tax["monthly_tax_total"], 0)
        self.assertGreater(len(tax["taxes"]), 0)

        # 礼部宗教全局加成 (净 +5.5% 产能, -2.0% 需求)
        rel = dept["religion"]
        self.assertAlmostEqual(rel["net_prod_pct"], 0.055, places=4)
        self.assertAlmostEqual(rel["net_demand_pct"], -0.020, places=4)

    def test_weather_and_disaster_modifiers(self):
        """3. 测试气象灾害、瘟疫、黄汛与地震修正。"""
        loc = self.state.provinces["henan"]["local"]
        loc["weather"] = {"condition": "晴", "temp": 28, "disaster": "大旱", "disaster_remain": 45, "capacity_penalty": 0.30}
        loc["plague"] = {"name": "小疫", "penalty": 0.10, "remain_days": 60}
        loc["yellow_flood"] = {"name": "黄汛", "initial_penalty": 0.18, "decay_per_month": 0.03, "month_idx": 1, "remain_days": 150}
        loc["earthquake_penalty"] = {"name": "大地震", "penalty": 0.05, "remain_days": 20}

        wea = get_province_weather_modifiers(self.state, "henan", loc)
        self.assertTrue(wea["disaster"]["active"])
        self.assertEqual(wea["disaster"]["penalty_pct"], 0.30)
        self.assertTrue(wea["plague"]["active"])
        self.assertEqual(wea["plague"]["penalty_pct"], 0.10)
        self.assertTrue(wea["yellow_flood"]["active"])
        self.assertEqual(wea["yellow_flood"]["current_penalty_pct"], 0.15)
        self.assertTrue(wea["earthquake"]["active"])
        self.assertEqual(wea["earthquake"]["penalty_pct"], 0.05)

        # 合计减产: 0.30 + 0.10 + 0.15 + 0.05 = 0.60
        self.assertAlmostEqual(wea["total_disaster_penalty_pct"], 0.60, places=4)

    def test_project_and_mitigation_modifiers(self):
        """4. 测试地方工程与灾害建筑减免（减免数值与文案都来自词条表，不写散文）。"""
        # 浙江海宁海塘：小涝/大涝导致的产能损失，文案就是词条名
        zj_proj = get_province_project_modifiers(self.state, "zhejiang")
        self.assertGreater(len(zj_proj["disaster_mitigations"]), 0)
        zj_effs = [m["effect"] for m in zj_proj["disaster_mitigations"]]
        self.assertTrue(any("小涝导致的产能损失-70%" == e for e in zj_effs), zj_effs)
        self.assertTrue(any("大涝导致的产能损失-70%" == e for e in zj_effs), zj_effs)

        # 河南（黄河流域）：黄汛减产与发生概率的减免也按词条写
        hn_proj = get_province_project_modifiers(self.state, "henan")
        self.assertGreater(len(hn_proj["disaster_mitigations"]), 0)
        hn_effs = " ".join(m["effect"] for m in hn_proj["disaster_mitigations"])
        self.assertIn("黄汛导致的产能损失-20%", hn_effs)
        self.assertIn("大黄汛导致的产能损失-20%", hn_effs)
        self.assertIn("黄汛发生概率-20%", hn_effs)

        # 云南省矿产测试
        yn_proj = get_province_project_modifiers(self.state, "yunnan")
        self.assertGreater(yn_proj["total_annual_silver"], 0)
        self.assertGreater(yn_proj["total_annual_gold"], 0)

    def test_movement_and_supply_demand(self):
        """5. 测试省际移动修正与供需指数。"""
        mov = get_province_movement_modifiers(self.state, "shaanxi")
        self.assertGreater(len(mov["neighbors"]), 0)
        self.assertIn("path", mov["path_to_beijing"])

        sd = get_province_supply_demand_modifiers(self.state, "shaanxi")
        self.assertGreater(sd["ratio"], 0)
        self.assertIn("status_name", sd)
        self.assertIn("drift_text", sd)

    def test_all_modifiers_integration(self):
        """6. 测试综合打包函数 get_province_all_modifiers。"""
        all_mods = get_province_all_modifiers(self.state, "sichuan")
        self.assertEqual(all_mods["province_id"], "sichuan")
        self.assertEqual(all_mods["province_name"], "四川")
        self.assertIn("summary_badges", all_mods)
        self.assertIn("military", all_mods)
        self.assertIn("departments", all_mods)
        self.assertIn("weather_disaster", all_mods)
        self.assertIn("projects_and_mines", all_mods)
        self.assertIn("movement", all_mods)
        self.assertIn("supply_demand", all_mods)
        self.assertIn("classes_summary", all_mods)

        # 验证阶级明细包含公式解说
        cls_sum = all_mods["classes_summary"]
        self.assertIn("农民", cls_sum)
        self.assertIn("formula_text", cls_sum["农民"])
        self.assertIn("capacity", cls_sum["农民"])

    def test_controlled_territory_scope(self):
        """7. 测试限定全局修正范围：作为明朝只修正全图中控制区域为核心和宣称的地区。"""
        # 满清地区（盛京）为未控敌国
        hj_dept = get_province_dept_modifiers(self.state, "shengjing")
        self.assertEqual(hj_dept["taxation"]["monthly_tax_total"], 0.0)
        self.assertEqual(hj_dept["religion"]["net_prod_pct"], 0.0)
        self.assertEqual(hj_dept["corruption"]["mult_sum"], 0.0)

        hj_mil = get_province_military_modifiers(self.state, "shengjing")
        self.assertEqual(hj_mil["liaoxiang"]["allocated_silver_monthly"], 0.0)
        self.assertEqual(hj_mil["army_arrears"]["garrison_arrears_wan"], 0.0)

        # 核心地区陕西
        sx_dept = get_province_dept_modifiers(self.state, "shaanxi")
        self.assertGreater(sx_dept["taxation"]["monthly_tax_total"], 0.0)
        self.assertEqual(sx_dept["corruption"]["value"], 66.0)

    def test_province_friendly_and_enemy_armies(self):
        """8. 测试地区己方军队与敌方军队抽取。"""
        from engine.armies import get_province_armies, get_army_by_id

        # 1) 满清地区（盛京）：敌军八旗
        pa_hj = get_province_armies("shengjing", self.state)
        self.assertTrue(pa_hj["has_enemy"])
        self.assertGreaterEqual(len(pa_hj["enemy"]), 1)
        baqi = next(e for e in pa_hj["enemy"] if e["id"] == "mobile_baqi")
        self.assertEqual(baqi["commander"], "皇太极")
        self.assertEqual(baqi["count"], 50000)

        # 2) 四川地区：己方白杆兵+四川镇军，敌方奢寅部+安位部
        pa_sc = get_province_armies("sichuan", self.state)
        self.assertTrue(pa_sc["has_battle"])
        friendly_names = [f["name"] for f in pa_sc["friendly"]]
        enemy_names = [e["name"] for e in pa_sc["enemy"]]
        self.assertIn("白杆兵", friendly_names)
        self.assertIn("四川镇军", friendly_names)
        self.assertIn("奢寅部叛军", enemy_names)
        self.assertIn("安位部叛军", enemy_names)

        # 3) 陕西地区：己方陕西镇军，敌方王二部+郑彦夫部
        pa_sx = get_province_armies("shaanxi", self.state)
        self.assertTrue(pa_sx["has_battle"])
        sx_enemies = [e["name"] for e in pa_sx["enemy"]]
        self.assertIn("王二部起义军", sx_enemies)
        self.assertIn("郑彦夫部起义军", sx_enemies)

        # 4) 测试 get_army_by_id
        a_sheyin = get_army_by_id("enemy_sheyin", self.state)
        self.assertIsNotNone(a_sheyin)
        self.assertEqual(a_sheyin["commander"], "奢崇明")
        self.assertIn("armors", a_sheyin["equipment"])

    def test_memorial_annotate_immediate_dialogue_archive(self):
        """9. 测试章奏打字批阅使用批奏发送后立即刷新并归档。"""
        from engine.session import GameSession
        session = GameSession()
        mems = session.list_memorials()
        self.assertGreater(len(mems), 0)
        target_m = mems[0]

        # 确保无封驳
        session.state.prestige_penalty = 100.0

        ok, msg = session.memorial_act(target_m["id"], "annotate", "特敕该部从速查办")
        self.assertTrue(ok)
        self.assertEqual(target_m["status"], "annotated")
        self.assertEqual(target_m["pizou"], "特敕该部从速查办")

        # 验证对话归档已立即生成
        dlgs = session.list_dialogues("annotate")
        self.assertGreater(len(dlgs), 0)
        latest_dlg = dlgs[0]
        self.assertEqual(latest_dlg["memorial_id"], target_m["id"])
        self.assertEqual(latest_dlg["pizou"], "特敕该部从速查办")


if __name__ == "__main__":
    unittest.main()
