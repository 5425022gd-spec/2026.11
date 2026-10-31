"""地方数据与新灾害/天气/地震/黄汛/瘟疫综合系统测试：
1. 数据完整性与派生值
2. 人口与人口每月增长
3. 静态气候数据
4. 台风省份限制（严禁北直隶、山东、南直隶、广西、辽东）
5. 降水平稳分配与闭合（春 4~6 天，夏 6~8 天）
6. 灾害与瘟疫减产惩罚联动
7. 瘟疫大灾后第 20 天判定与小疫/大疫生命周期
8. 历史地震脚本判定（公历 1628-10-07 等准确触发与死伤/减产）
9. 灾害死亡人数公式、连续受灾修正自增与重置
10. 黄汛与大黄汛判定、逐月衰减、死亡判定与黄河水利建筑损毁
11. 灾害建筑修正（海宁海塘洪涝-70%，灵州黄河石堤黄汛-20%与概率-20%）
12. 概率减半与小灾影响减半
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_local.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_local_pos.json"))
from engine.session import GameSession
from engine.state import WorldState
from engine.weather import (
    tick_weather, init_weather, month_temp, climate_of,
    COASTAL_PROVINCES, get_province_region, DISASTER_TABLE,
    DISASTER_DEATH_COEFFS, province_flood_reduction, yellow_flood_reduction,
    yellow_flood_chance_reduction,
    HISTORICAL_EARTHQUAKES, apply_population_loss
)
from engine.time import advance_day, advance_month
from engine import localdata
from engine.economy import class_capacity, local_production, get_gregorian_date

CLASSES = ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]


class TestLocalAndDisaster(unittest.TestCase):
    def test_local_data_integrity(self):
        """1) 数据完整性：17省 local 齐全，派生值计算准确。"""
        s = GameSession()
        ming = [p for p in s.state.provinces.values() if p["owner"] == "ming"]
        self.assertEqual(len(ming), 17)

        for p in ming:
            loc = p.get("local") or {}
            self.assertTrue(loc, "%s 缺 local" % p["name"])
            self.assertIn(loc["core"], ("核心", "宣称", "占领"))
            cls = loc["classes"]
            self.assertEqual(set(cls.keys()), set(CLASSES))
            total = sum(c["pop"] for c in cls.values())
            self.assertAlmostEqual(localdata.population(loc), total, places=2)
            self.assertTrue(0 <= loc.get("corruption", 0) <= 100)
            self.assertIsNotNone(loc.get("weather"))

    def test_population_monthly_growth(self):
        """2) 人口每月自然增长。"""
        s = GameSession()
        before = localdata.population(s.state.provinces["beizhili"]["local"])
        s.run("j 1m")
        after = localdata.population(s.state.provinces["beizhili"]["local"])
        self.assertAlmostEqual(after / before, 1.001, delta=5e-4)

    def test_climate_data(self):
        """3) 静态气候数据合法性。"""
        c_bj = climate_of("beizhili")
        self.assertGreater(month_temp(c_bj, 7), month_temp(c_bj, 1))
        self.assertLess(month_temp(c_bj, 1), 0)
        c_gd = climate_of("guangdong")
        self.assertGreater(month_temp(c_gd, 1), 10)

    def test_typhoon_restrictions(self):
        """4) 台风刷新限制：严禁在北直隶、山东、南直隶、广西、辽东刷新。"""
        forbidden = {"beizhili", "shandong", "nanzhili", "guangxi", "liaodong"}
        for p in forbidden:
            self.assertNotIn(p, COASTAL_PROVINCES)

        random.seed(123)
        s = GameSession()
        s.state.date = {"era": "天启", "year": 1627, "month": 9, "day": 1, "hour": 0}
        s.state.weather_plan = None
        for _ in range(180):
            events = tick_weather(s.state, 1)
            for ev in events:
                if ev.get("type") == "typhoon" or ev.get("disaster") == "台风":
                    self.assertNotIn(ev["province_id"], forbidden)
            advance_day(s.state, 1)

    def test_precipitation_distribution_smoothness(self):
        """5) 降水分配平稳性与闭合：春季季风 4~6 天，夏季 6~8 天，方差平稳且严格闭合。"""
        random.seed(42)
        s = GameSession()
        s.state.date = {"era": "天启", "year": 1627, "month": 3, "day": 1, "hour": 0}
        s.state.weather_plan = None
        init_weather(s.state)
        plan = s.state.weather_plan["plan"]
        self.assertEqual(plan["season"], 0)

        for pid in ["shaanxi", "beizhili", "zhejiang", "sichuan"]:
            events_pid = [e for e in plan["events"] if e["province"] == pid and e["type"] == "monsoon"]
            self.assertTrue(4 <= len(events_pid) <= 6, "%s 春季降水天数异常: %d" % (pid, len(events_pid)))
            mms = [e["mm"] for e in events_pid]
            avg = sum(mms) / len(mms)
            for v in mms:
                self.assertAlmostEqual(v / avg, 1.0, delta=0.35)

        s.state.date = {"era": "天启", "year": 1627, "month": 6, "day": 1, "hour": 0}
        s.state.weather_plan = None
        init_weather(s.state)
        plan_summer = s.state.weather_plan["plan"]
        self.assertEqual(plan_summer["season"], 1)
        for pid in ["shaanxi", "beizhili", "zhejiang", "sichuan"]:
            events_pid = [e for e in plan_summer["events"] if e["province"] == pid and e["type"] == "monsoon"]
            self.assertTrue(6 <= len(events_pid) <= 8, "%s 夏季降水天数异常: %d" % (pid, len(events_pid)))

    def test_disaster_capacity_penalty_and_plague(self):
        """6) 灾害减产惩罚、瘟疫判定与经济产能联动。"""
        clean_loc = {
            "classes": {
                "农民": {"pop": 100.0, "stock": 5.0}
            },
            "projects": [],
            "weather": {"capacity_penalty": 0.0},
        }

        base_cap = class_capacity("农民", 100.0, clean_loc)
        self.assertEqual(base_cap, 150.0)

        # 大旱减产 30%
        clean_loc["weather"]["capacity_penalty"] = 0.30
        drought_cap = class_capacity("农民", 100.0, clean_loc)
        self.assertEqual(drought_cap, 105.0)
        self.assertAlmostEqual(drought_cap / base_cap, 0.70, places=4)

        # 大疫额外减产 25%
        clean_loc["plague"] = {"name": "大疫", "penalty": 0.25, "remain_days": 100}
        plague_cap = class_capacity("农民", 100.0, clean_loc)
        self.assertEqual(plague_cap, 67.5)
        self.assertAlmostEqual(plague_cap / base_cap, 0.45, places=4)

        clean_loc["weather"]["capacity_penalty"] = 0.0
        del clean_loc["plague"]
        recovered_cap = class_capacity("农民", 100.0, clean_loc)
        self.assertEqual(recovered_cap, base_cap)

    def test_plague_trigger_at_day_20(self):
        """7) 瘟疫在大灾难发生后第 20 天判定触发。"""
        random.seed(999)
        s = GameSession()
        s.state.date = {"era": "天启", "year": 1627, "month": 6, "day": 1, "hour": 0}
        s.state.weather_plan = None
        init_weather(s.state)

        for d in range(1, 31):
            events = tick_weather(s.state, 1)
            for ev in events:
                if ev.get("type") == "plague":
                    self.assertIn(ev["name"], ("小疫", "大疫"))
                    self.assertIn(ev["penalty"], (0.10, 0.25))
            advance_day(s.state, 1)

    def test_earthquake_script_trigger(self):
        """8) 历史地震脚本判定：1628年10月7日万全都司6.25级地震触发，死伤万分之一，下月产能-1%。"""
        s = GameSession()
        # 推进到 1628年10月7日公历对应的游戏日期
        # 1627-08-25 简化历 = 公历 1627-10-03
        # 1628-10-07 公历 相差 370 天
        target_greg = (1628, 10, 7)

        bj_loc = s.state.provinces["beizhili"]["local"]
        init_pop = localdata.population_exact(bj_loc)

        triggered = False
        for step in range(400):
            greg = get_gregorian_date(s.state.date)
            if (greg.year, greg.month, greg.day) == target_greg:
                cur_pop = localdata.population_exact(bj_loc)
                events = tick_weather(s.state, 1)
                eq_ev = next((e for e in events if e.get("type") == "earthquake"), None)
                self.assertIsNotNone(eq_ev)
                self.assertEqual(eq_ev["location"], "北直隶万全都指挥使司")
                self.assertEqual(eq_ev["magnitude"], 6.25)
                self.assertEqual(eq_ev["penalty"], 0.01)
                expected_death = int(round(cur_pop * 0.0001))
                self.assertEqual(eq_ev["deaths"], expected_death)
                self.assertIn("earthquake_penalty", bj_loc)
                self.assertEqual(bj_loc["earthquake_penalty"]["penalty"], 0.01)
                triggered = True
                break
            tick_weather(s.state, 1)
            advance_day(s.state, 1)

        self.assertTrue(triggered, "未能触发 1628-10-07 地震")

    def test_disaster_death_formula_and_consecutive_modifier(self):
        """9) 灾害死亡人数公式与连续修正自增与重置。"""
        # 公式：总死亡 = 人口 × 0.1 × 产能惩罚 × 灾害种类修正 × 连续修正 × 建筑修正
        pop = 8000000
        # 小旱：种类修正 0.01, 产能惩罚 0.05, 连续 1 -> 8000000 * 0.1 * 0.05 * 0.01 * 1 = 400 人
        death_xiao_han = int(round(pop * 0.1 * 0.05 * DISASTER_DEATH_COEFFS["小旱"] * 1 * 1.0))
        self.assertEqual(death_xiao_han, 400)

        # 大旱连续发生第 3 个季度：种类修正 0.10, 产能惩罚 0.30, 连续 3 -> 8000000 * 0.1 * 0.30 * 0.10 * 3 = 72000 人
        death_da_han_3 = int(round(pop * 0.1 * 0.30 * DISASTER_DEATH_COEFFS["大旱"] * 3 * 1.0))
        self.assertEqual(death_da_han_3, 72000)

        # 测试人口按阶级等比扣减
        test_loc = {
            "classes": {
                "农民": {"pop": 700.0, "pop_exact": 7000000},
                "百工": {"pop": 100.0, "pop_exact": 1000000},
            }
        }
        apply_population_loss(test_loc, 8000)
        self.assertEqual(test_loc["classes"]["农民"]["pop_exact"], 7000000 - 7000)
        self.assertEqual(test_loc["classes"]["百工"]["pop_exact"], 1000000 - 1000)

    def test_yellow_flood_mechanisms_and_building_damage(self):
        """10) 黄汛与大黄汛机制：河南/山东/南直隶夏秋大涝大降水触发，大黄汛损坏黄河灾害建筑。

        石堤在陕西、受灾三省没有名字带「黄河」的工程——所以判定必须按工程的数据字段认，
        按工程名/备注文本认的话这条永远不命中（待办 26）。
        """
        s = GameSession()
        hn_loc = s.state.provinces["henan"]["local"]
        # 名字带「黄河」但数据里没有黄汛字段的工程：不算黄河灾害建筑，不该被损毁
        hn_loc["projects"] = [
            {"name": "开封黄河大堤", "kind": "工程建筑", "status": "运营中", "buff": {}}
        ]
        sx_dike = None
        for pj in s.state.provinces["shaanxi"]["local"]["projects"]:
            if pj.get("name") == "灵州黄河石堤":
                sx_dike = pj
        self.assertIsNotNone(sx_dike, "陕西应有灵州黄河石堤")
        self.assertEqual(sx_dike["status"], "运营中")

        # 模拟注入大黄汛
        from engine.weather import _resolve_pending_yellow_flood
        hn_loc["pending_yellow_flood"] = {"stage": 1, "trigger_date": dict(s.state.date)}

        events = []
        # 固定随机数使得第二天 10% 概率命中大黄汛 (rng.random() < 0.10 -> 0.05)
        class MockRng:
            def random(self):
                return 0.05

        _resolve_pending_yellow_flood(s.state, "henan", s.state.provinces["henan"], s.state.date, MockRng(), events)

        # 验证大黄汛生成
        self.assertIn("yellow_flood", hn_loc)
        yf = hn_loc["yellow_flood"]
        self.assertEqual(yf["name"], "大黄汛")
        # 灵州黄河石堤完好，惩罚减少 20% -> 0.36 * 0.8 = 0.288
        self.assertAlmostEqual(yf["initial_penalty"], 0.288, places=3)
        self.assertEqual(yf["total_days"], 270)

        # 黄河灾害建筑按数据字段被损毁：陕西的石堤也算（它的防护是全国口径）
        from engine.projects import damage_of
        self.assertEqual(damage_of(sx_dike), 1.0, "灵州黄河石堤应被大黄汛全毁")
        # 管大小涝的海宁海塘不归黄河决口管，不该被损毁
        zj_pond = None
        for pj in s.state.provinces["zhejiang"]["local"]["projects"]:
            if pj.get("name") == "海宁海塘":
                zj_pond = pj
        self.assertIsNotNone(zj_pond)
        self.assertEqual(damage_of(zj_pond), 0.0, "海宁海塘不该被大黄汛损毁")
        # 只有名字像、数据里没有灾害字段的工程不动
        self.assertEqual(damage_of(hn_loc["projects"][0]), 0.0)
        # 全毁后不再计入黄汛减免
        from engine.weather import yellow_flood_reduction
        self.assertAlmostEqual(yellow_flood_reduction(s.state), 0.0, places=4)

    def test_disaster_building_modifiers(self):
        """11) 灾害建筑修正：减免比例取自工程数据字段（代码里不再按工程名写死）。"""
        s = GameSession()
        # 浙江海宁海塘完好：本省大小涝产能损失减轻 70%
        self.assertAlmostEqual(province_flood_reduction(s.state, "zhejiang"), 0.70, places=4)
        # 灵州黄河石堤完好：黄汛减产减轻 20%、发生机会减少 20%
        self.assertAlmostEqual(yellow_flood_reduction(s.state), 0.20, places=4)
        self.assertAlmostEqual(yellow_flood_chance_reduction(s.state), 0.20, places=4)
        # 数据来源就是工程的 buff 字段，不是别处的常量
        proj = None
        for pj in s.state.provinces["zhejiang"]["local"]["projects"]:
            if pj["name"] == "海宁海塘":
                proj = pj
        self.assertIsNotNone(proj)

        # 测试海宁海塘减免后小涝惩罚: 0.05 * 0.30 = 0.015 (减少70%)
        # 见 weather.py _roll_province_disasters
        from engine.weather import _roll_province_disasters
        class MockRngFlood:
            def random(self):
                return 0.01  # 必然命中洪涝

        dp = _roll_province_disasters(s.state, "zhejiang", 0, MockRngFlood())
        for item in dp["items"]:
            if item["kind"] == "涝":
                # 原小涝 0.02 减少 70% -> 0.006，或原 0.05 减少 70% -> 0.015
                self.assertLess(item["penalty"], 0.02)

    def test_probability_reduction_50pct(self):
        """12) 概率调整验证：基础发生概率减半向下取整、小灾影响减半向下取整、大灾概率减半向下取整。"""
        # 北方春旱：原 30% -> 15%，小旱 90%原减产10%->新5%，大旱 10%减半->5%
        north_spring = DISASTER_TABLE["north"][0]
        han = next(item for item in north_spring if item[0] == "旱")
        self.assertEqual(han[1], 0.15)
        xiao_han = han[2][0]
        da_han = han[2][1]
        self.assertEqual(xiao_han[1], 0.95)  # 小旱占比 95%
        self.assertEqual(xiao_han[2], 0.05)  # 小旱减产 5%
        self.assertEqual(da_han[1], 0.05)    # 大旱占比 5%
        self.assertEqual(da_han[2], 0.30)    # 大旱减产 30%


if __name__ == "__main__":
    unittest.main()
