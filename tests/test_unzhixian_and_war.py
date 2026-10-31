"""Comprehensive test suite for:
1. Unzhixian (99 rich templates from Prompt/unzhixian, removed old templates).
2. Military Arrears quarterly tallying, treasury shortfall allocation and capacity penalty.
3. Adjacent Province Distances, Dijkstra Shortest Path Relay delivery time.
4. Movement Modifiers between provinces.
5. Army Dispatching, Re-selection Recalculation, Daily Marching, and War Department Real-time Sync.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_unzhixian_and_war.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_unzhixian_and_war_pos.json"))
from engine.session import GameSession
from engine.state import WorldState
from engine.memorials import load_filler_templates, generate_filler, travel_days
from engine.distances import (
    ADJACENT_DISTANCES, ADJACENT_GRAPH, find_shortest_path,
    get_edge_modifier, set_edge_modifier, get_province_neighbor_distances
)
from engine.armies import (
    get_all_armies, dispatch_army, update_army_arrears, select_units,
    clear_arrears, tick_armies_daily, tick_military_expenses_monthly)
from engine.depts_war import get_war_overview, get_relay_stations_info
from engine.economy import class_capacity


class TestUnzhixianAndWar(unittest.TestCase):
    def setUp(self):
        self.session = GameSession()

    def test_unzhixian_templates_and_purged_old(self):
        """1) 测试 unzhixian 99 个新模板加载，且旧的特纠不职有司和特纠纵兵殃民已彻底删除。"""
        templates = load_filler_templates()
        self.assertGreaterEqual(len(templates), 90)

        # 验证旧模板已彻底删除
        old_titles = [t.get("title", "") for t in templates]
        for t in old_titles:
            self.assertNotIn("特纠不职有司事（", t)
            self.assertNotIn("特纠纵兵殃民、临敌失守事", t)

        # 验证新分类涵盖 14 种
        sub_cats = {t.get("sub_category") for t in templates}
        self.assertIn("弹劾地方", sub_cats)
        self.assertIn("弹劾中央", sub_cats)
        self.assertIn("贪腐", sub_cats)
        self.assertIn("举荐", sub_cats)
        self.assertIn("旱灾", sub_cats)
        self.assertIn("水涝", sub_cats)
        self.assertIn("高温", sub_cats)
        self.assertIn("严寒", sub_cats)
        self.assertIn("小黄汛", sub_cats)
        self.assertIn("大黄汛", sub_cats)
        self.assertIn("夏丰收", sub_cats)
        self.assertIn("秋丰收", sub_cats)

        # 验证生成不重要事件
        fillers = generate_filler(5, state=self.session.state)
        self.assertEqual(len(fillers), 5)
        for f in fillers:
            self.assertTrue(f["id"].startswith("n-"))
            self.assertEqual(f["source"], "filler")
            self.assertIn("谨题", f["content"])
            self.assertIn("draft_processed", f)
            self.assertIn("draft_unprocessed", f)
            self.assertIn("reward_processed", f)
            self.assertIn("reward_unprocessed", f)
            # 验证变量没有残留括号占位符
            self.assertNotIn("（巡抚人名）", f["content"])
            self.assertNotIn("（官员职位）", f["content"])
            self.assertNotIn("（随机人名1）", f["content"])

    def test_dijkstra_relay_shortest_paths(self):
        """2) 测试省际邻接距离与 Dijkstra 最短路径公文传递时间。"""
        # 陕西到北京：经山西到达，时间为 550/250 + 410/250 = 2.2 + 1.64 = 3.84 天
        res_sx = find_shortest_path("shaanxi", "beizhili")
        self.assertEqual(res_sx["path"], ["shaanxi", "shanxi", "beizhili"])
        self.assertEqual(res_sx["dist_km"], 960)
        self.assertAlmostEqual(res_sx["time_days"], 3.8, delta=0.1)

        # 验证 travel_days 与最短路径一致
        days_sx = travel_days("陕西西安府长安县", self.session.state)
        self.assertEqual(days_sx, 4)  # round(3.8) = 4

        # 验证四川到北京：经陕西、山西、北京
        res_sc = find_shortest_path("sichuan", "beizhili")
        self.assertEqual(res_sc["path"], ["sichuan", "shaanxi", "shanxi", "beizhili"])
        self.assertEqual(res_sc["dist_km"], 1610)

        # 验证兵部驿站总表
        relays = get_relay_stations_info(self.session.state)
        self.assertGreaterEqual(len(relays["stations"]), 16)
        station_sx = next(s for s in relays["stations"] if s["province_id"] == "shaanxi")
        self.assertEqual(station_sx["distance_km"], 960)
        self.assertIn("山西", station_sx["path_str"])

    def test_movement_modifiers(self):
        """3) 测试省际移动修正（默认1.0）及动态加速/减速。"""
        st = self.session.state
        # 默认修正为 1.0
        mod_default = get_edge_modifier("shaanxi", "shanxi", st)
        self.assertEqual(mod_default, 1.0)

        # 设置加速修正（例如修筑秦晋驰道，修正为 2.0）
        set_edge_modifier("shaanxi", "shanxi", 2.0, st)
        self.assertEqual(get_edge_modifier("shaanxi", "shanxi", st), 2.0)

        # 重新计算路径用时：陕西到山西由 2.2 天缩减为 1.1 天，总用时由 3.8 天缩减为 2.7 天
        res_faster = find_shortest_path("shaanxi", "beizhili", st)
        self.assertAlmostEqual(res_faster["time_days"], 2.7, delta=0.1)

        # 恢复默认
        set_edge_modifier("shaanxi", "shanxi", 1.0, st)

    def test_army_dispatch_and_recalculation(self):
        """4) 测试军队调动与中途重选目标的重新计算。"""
        st = self.session.state
        # 调动白杆兵：从四川调往北直隶
        ok, msg = dispatch_army("mobile_baigan", "beizhili", st)
        self.assertTrue(ok)
        armies = get_all_armies(st)
        baigan = next(m for m in select_units(armies, category="mobile") if m["id"] == "mobile_baigan")
        self.assertEqual(baigan["target_location"], "beizhili")
        self.assertEqual(baigan["path"], ["sichuan", "shaanxi", "shanxi", "beizhili"])
        self.assertGreater(baigan["remain_days"], 0)

        # 中途重选目标为云南：从当前位置（四川）重新规划计算
        ok2, msg2 = dispatch_army("mobile_baigan", "yunnan", st)
        self.assertTrue(ok2)
        armies2 = get_all_armies(st)
        baigan2 = next(m for m in select_units(armies2, category="mobile") if m["id"] == "mobile_baigan")
        self.assertEqual(baigan2["target_location"], "yunnan")
        self.assertEqual(baigan2["path"], ["sichuan", "yunnan"])

        # 调回四川石砫
        dispatch_army("mobile_baigan", "sichuan", st)

    def test_army_arrears_rules_and_opening_months(self):
        """5) 测试开局欠饷规则（机动 6 个月、镇军 36 个月）、欠饷折物质减产，且不再有季度清零。"""
        st = self.session.state
        armies = get_all_armies(st)

        # 开局欠饷＝人数 × PAY_PER_MAN × 该类别的月数（机动 6、镇军 36）
        from engine.armies import PAY_PER_MAN, opening_arrears_wan
        for u in armies:
            self.assertAlmostEqual(u["arrears_wan"], opening_arrears_wan(u), places=4)
        garrison = next(u for u in armies if u.get("category") == "garrison" and u.get("arrears_wan"))
        mobile = next(u for u in armies if u.get("category") == "mobile" and u.get("arrears_wan"))
        self.assertAlmostEqual(
            mobile["arrears_wan"],
            round(int(mobile["count"]) * PAY_PER_MAN * 6 / 10000.0, 2), places=4)
        self.assertAlmostEqual(
            garrison["arrears_wan"],
            round(int(garrison["count"]) * PAY_PER_MAN * 36 / 10000.0, 2), places=4)

        # 欠饷折物质减产：实时读兵部名册，不读省份副本
        from engine.modifiers import get_province_local_modifiers
        live = get_province_local_modifiers(st, garrison["province_id"])
        self.assertGreater(live["arrears_wan"], 0.0)
        self.assertGreater(live["flat_penalty"]["农民"], 0.0)

        # 季度推进不再自动清零欠饷（欠饷是「军饷筹措」主线的目标，只在付清时减少）
        from engine.economy import tick_economy_monthly
        before = {u["id"]: u["arrears_wan"] for u in get_all_armies(st)}
        st.date["month"] = 9          # 季初
        tick_economy_monthly(st)
        after = {u["id"]: u["arrears_wan"] for u in get_all_armies(st)}
        self.assertTrue(any(after[i] >= before[i] for i in before),
                        "季初不得把欠饷清零")

        # 清空欠饷只有显式调用才发生（测试/调试用）
        clear_arrears(st)
        live_reset = get_province_local_modifiers(st, "shaanxi")
        self.assertEqual(live_reset["arrears_wan"], 0.0)

    def test_war_dept_sync_and_dest_options(self):
        """6) 测试兵部汇总视图：实时同步机动军队调动，镇军不计入调动表且不可移动。"""
        st = self.session.state
        war_data = get_war_overview(st)
        self.assertIn("armies", war_data)
        self.assertIn("dest_provinces", war_data)
        self.assertGreaterEqual(len(war_data["dest_provinces"]), 16)

        # 兵部调动表只包含机动部队（白杆兵），不计入镇军
        army_names = [a["name"] for a in war_data["armies"]]
        self.assertIn("白杆兵", army_names)
        self.assertNotIn("陕西镇军", army_names)

        # 验证地方镇军禁止移动调遣
        ok_gar, msg_gar = dispatch_army("gar_shaanxi", "beizhili", st)
        self.assertFalse(ok_gar)
        self.assertIn("不可", msg_gar)

    def test_minute_advancement_and_opening_prestige(self):
        """7) 测试时间分钟制递增（批奏+10m、中旨+10m、召对+30m+10m）及开局皇室威望为66.9（基础60×(1+11.5%)）。"""
        st = self.session.state
        # 1. 开局威望为 66.9 (基础60.0 × 1.115)
        from engine.depts_rites import calc_prestige
        p_init = calc_prestige(st)
        self.assertEqual(p_init["base"], 60.0)
        self.assertEqual(p_init["value"], 66.9)

        # 2. 批阅章奏：时间 +10 分钟
        st.date["hour"] = 8
        st.date["minute"] = 0
        fillers = generate_filler(1, state=st)
        st.memorials = fillers
        self.session.memorial_act(fillers[0]["id"], "read")
        self.assertEqual(st.date["minute"], 10)

        # 3. 发起中旨：时间 +10 分钟
        self.session.issue_zhongzhi("发内帑银一万两")
        self.assertEqual(st.date["minute"], 20)

        # 5. 廷议：发起 +5 分钟，之后不耗时间
        from engine.characters import list_callable
        c_ids = [c["id"] for c in list_callable()[:2]]
        ok, msg = self.session.council.start(fillers[0]["id"], c_ids)
        self.assertTrue(ok)
        self.assertEqual(st.date["minute"], 25)  # 20 + 5 = 25（发起廷议收 5 分钟）
        self.assertEqual(st.date["hour"], 8)
        self.session.council.generate_pizou()
        self.assertEqual(st.date["minute"], 25)  # 结束不再加时
        self.assertEqual(st.date["hour"], 8)


if __name__ == "__main__":
    unittest.main()
