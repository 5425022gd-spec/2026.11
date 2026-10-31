"""礼部祭祀体系（大明常祀、灾异三教祭祀、耗资、超时扣威望、宗教增益）与钦天监日历系统全面单元测试。"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_rites_and_calendar.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_rites_and_calendar_pos.json"))
from engine.session import GameSession
from engine.calendar import (
    game_to_gregorian, gregorian_to_game, get_day_ganzhi,
    calc_annual_sacrifices, get_calendar_month_data
)
from engine.depts_rites import (
    tick_sacrifices_monthly, tick_sacrifices_daily, start_sacrifice,
    calc_religion_summary, get_rites_overview
)
from engine.weather import record_pending_sacrifice
from engine.time import advance_day, advance_month


class TestRitesAndCalendar(unittest.TestCase):
    def setUp(self):
        self.session = GameSession()

    def test_calendar_conversions_and_ganzhi(self):
        """1) 测试游戏简化历与公历双向互转及干支纪日。"""
        # 基准：1627年8月25日 = 1627年10月3日 (戊午日)
        base_game = {"year": 1627, "month": 8, "day": 25}
        greg = game_to_gregorian(base_game)
        self.assertEqual((greg.year, greg.month, greg.day), (1627, 10, 3))

        rev_game = gregorian_to_game(greg)
        self.assertEqual(rev_game, {"year": 1627, "month": 8, "day": 25})

        gz_str, g_idx, z_idx = get_day_ganzhi(greg)
        self.assertEqual(gz_str, "戊午")
        self.assertEqual(g_idx, 4)  # 戊
        self.assertEqual(z_idx, 6)  # 午

    def test_four_regular_sacrifices(self):
        """2) 测试大明四大常祀吉日规则与计算（名目与规则不带括号与间隔号）。"""
        sacs_1628 = calc_annual_sacrifices(1628)
        names = [s["name"] for s in sacs_1628]
        for nm in ("社稷坛春祭", "山川坛春祭", "先农坛藉田礼", "雩祀", "社稷坛秋祭", "山川坛秋祭"):
            self.assertIn(nm, names)
        self.assertEqual(len(names), len(set(names)), "名目不得重复")

        # 校验社稷坛为戊日
        sheji_spring = next(s for s in sacs_1628 if s["name"] == "社稷坛春祭")
        self.assertTrue(sheji_spring["ganzhi"].startswith("戊"))
        self.assertEqual(sheji_spring["game_date"]["month"], 2)

        # 校验先农坛为亥日
        xiannong = next(s for s in sacs_1628 if s["name"] == "先农坛藉田礼")
        self.assertTrue(xiannong["ganzhi"].endswith("亥"))
        self.assertEqual(xiannong["game_date"]["month"], 2)

        # 校验雩祀为五月朔日（初一）
        yusi = next(s for s in sacs_1628 if s["name"] == "雩祀")
        self.assertEqual(yusi["game_date"]["month"], 5)
        self.assertEqual(yusi["game_date"]["day"], 1)

        # 名目、规则、寓意一律不用括号与间隔号
        for s in sacs_1628:
            for key in ("name", "rule", "desc"):
                self.assertNotIn("（", s[key], "%s 的 %s 还带括号" % (s["name"], key))
                self.assertNotIn("）", s[key], "%s 的 %s 还带括号" % (s["name"], key))
                self.assertNotIn("·", s[key], "%s 的 %s 还带间隔号" % (s["name"], key))

    def test_disaster_pending_and_monthly_refresh_three_religions(self):
        """3) 测试灾害死亡事件记录待办，次月1日刷新为互不冲突的三教祭祀清单。"""
        st = self.session.state
        st.pending_sacrifices = []
        st.sacrifices = []

        # 记录陕西大旱死亡 20,000 人
        record_pending_sacrifice(st, "大旱", "shaanxi", "陕西", 1627, 20000)
        self.assertEqual(len(st.pending_sacrifices), 1)
        self.assertEqual(st.pending_sacrifices[0]["deaths"], 20000)

        # 次月1日刷新
        notes = tick_sacrifices_monthly(st)
        self.assertEqual(len(st.pending_sacrifices), 0)  # 待办已清空
        self.assertEqual(len(st.sacrifices), 3)          # 生成 3 个互不冲突的祭祀

        # 验证三教类型、花费与威望惩罚
        rel_set = {s["religion"] for s in st.sacrifices}
        self.assertEqual(rel_set, {"佛教超度", "儒教消灾", "道教驱邪"})

        for s in st.sacrifices:
            self.assertEqual(s["deaths"], 20000)
            # 花费 = 20000 * 0.05 = 1000 两 = 0.10 万两
            self.assertAlmostEqual(s["cost_silver"], 1000.0)
            self.assertAlmostEqual(s["cost_wan"], 0.10, places=3)
            # 超时威望惩罚 = 20000 * 0.00005 = 1.0 点
            self.assertAlmostEqual(s["prestige_penalty"], 1.0, places=3)
            self.assertEqual(s["remain_days"], 30)
            self.assertEqual(s["status"], "pending")

    def test_start_sacrifice_and_influence_gain(self):
        """4) 测试开始祭祀扣除太仓银，次日礼成且该宗教影响力相对值 +1%。"""
        st = self.session.state
        st.treasury = 200.0
        st.pending_sacrifices = []
        st.sacrifices = []

        # 生成一次佛教超度祭祀
        record_pending_sacrifice(st, "大旱", "shaanxi", "陕西", 1627, 20000)
        tick_sacrifices_monthly(st)

        fo_sac = next(s for s in st.sacrifices if s["target_religion"] == "佛教")
        init_fo_inf = float(st.religion_influences["佛教"])  # 默认 20.0

        # 开始祭祀
        ok, msg = start_sacrifice(st, fo_sac["id"])
        self.assertTrue(ok)
        # 国库扣除 0.10 万两
        self.assertAlmostEqual(st.treasury, 199.90, places=2)
        self.assertEqual(fo_sac["status"], "in_progress")

        # 推进到第二天：祭典礼成
        events = tick_sacrifices_daily(st)
        self.assertEqual(fo_sac["status"], "completed")
        # 佛教影响力相对值 +1%: 20.0 * 1.01 = 20.20
        new_fo_inf = float(st.religion_influences["佛教"])
        self.assertAlmostEqual(new_fo_inf, init_fo_inf * 1.01, places=2)

    def test_sacrifice_timeout_and_prestige_penalty(self):
        """5) 测试逾期未祭（超过30天）扣除皇室威望。"""
        st = self.session.state
        st.pending_sacrifices = []
        st.sacrifices = []
        st.prestige_penalty = 0.0

        # 生成祭祀
        record_pending_sacrifice(st, "大地震", "beizhili", "北直隶", 1627, 10000)
        tick_sacrifices_monthly(st)

        # 30 天内不祭祀，逐日流逝
        for _ in range(30):
            tick_sacrifices_daily(st)

        # 全部超时失效
        for s in st.sacrifices:
            self.assertEqual(s["status"], "expired")

        # 扣除威望：每个 10000 * 0.00005 = 0.5 点，3 个共 1.5 点
        self.assertAlmostEqual(st.prestige_penalty, 1.5, places=2)

    def test_rites_table_is_the_calendar_source(self):
        """6) 礼部常祀表与钦天监日历同源：表里那几条，日历上就那几条。"""
        st = self.session.state
        gy = st.date["year"]
        table = [s["id"] for s in get_rites_overview(st)["regular_sacrifices"]]
        self.assertEqual(table, [s["id"] for s in calc_annual_sacrifices(gy)],
                         "礼部常祀表就是 calc_annual_sacrifices 那一份，不得另有来源")

    def test_calendar_takes_every_sacrifice_from_the_table(self):
        """7) 每一年的祭祀项目总是从常祀表动态落到日历：条数一致、日期一致、日历上不多不少。"""
        st = self.session.state
        for game_year in range(1627, 1634):
            table = calc_annual_sacrifices(game_year)
            self.assertEqual(len(table), 6, "%d 年常祀表应有 6 条" % game_year)
            for sac in table:
                gy, gm, gd = (int(x) for x in sac["greg_date"].split("-"))
                month_data = get_calendar_month_data(gy, gm, st)
                day = next((d for d in month_data["days"] if d["greg_date"] == sac["greg_date"]), None)
                self.assertIsNotNone(day, "%s 的公历吉日 %s 不在该月日历里" % (sac["name"], sac["greg_date"]))
                hits = [m for m in day["markers"] if m.get("type") == "regular_sacrifice"]
                self.assertEqual(len(hits), 1, "%s 在 %s 上应恰好一个标记" % (sac["name"], sac["greg_date"]))
                # 标记内容就是表里那一行
                self.assertEqual(hits[0]["id"], sac["id"])
                self.assertEqual(hits[0]["title"], sac["name"])
                self.assertEqual(hits[0]["icon"], sac["icon"])
                self.assertIn(sac["rule"], hits[0]["desc"], "标记说明要带表里的吉日规则")

        # 反查：日历上出现的常祀标记必须全部来自常祀表，没有写死在日历里的条目
        for greg_year in range(1627, 1634):
            allowed = set()
            for gy in (greg_year - 1, greg_year, greg_year + 1):
                allowed |= {s["id"] for s in calc_annual_sacrifices(gy)}
            for month in range(1, 13):
                for d in get_calendar_month_data(greg_year, month, st)["days"]:
                    for m in d["markers"]:
                        if m.get("type") == "regular_sacrifice":
                            self.assertIn(m["id"], allowed,
                                          "%s 上的 %s 不在常祀表里" % (d["greg_date"], m.get("title")))


if __name__ == "__main__":
    unittest.main()
