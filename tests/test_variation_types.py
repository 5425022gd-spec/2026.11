# -*- coding: utf-8 -*-
"""变化类型归类断言：正文里的具体事实优先于调用方给的粗类型。

以前 `normalize_variation_type` 先看 vtype，于是调用方传的 "disaster" 一命中，
祭祀告成、农民起事、后金入寇全被记成「自然灾害」，调兵令箭被记成「军队欠饷」，
AI 章奏拿到的超控范围因此是错的那一套。这里把每条真实文本钉住。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine.variations import (VARIATION_SCHEMA_MAP, normalize_variation_type,  # noqa: E402
                               record_variation)


class TestVariationTypes(unittest.TestCase):
    def check(self, vtype, text, expected):
        self.assertEqual(normalize_variation_type(vtype, text), expected,
                         "「%s」应归 %s" % (text, expected))

    def test_text_beats_coarse_type(self):
        # 祭祀：调用方传的是 disaster，但正文是礼制
        self.check("disaster", "【祭祀告成】天地坛 天地礼成，佛教影响力提升", "diplomacy_trade")
        self.check("disaster", "【祭祀逾期】太庙 社稷超时未祭，威望受损", "diplomacy_trade")
        # 叛乱与入寇：调用方传 disaster
        self.check("disaster", "【陕西】延安府农民起事，聚众三千", "military_defense_war")
        self.check("disaster", "后金骑兵入寇辽东", "military_defense_war")
        # 调兵：调用方传 military，正文是边防
        self.check("military", "【朝廷调兵令箭出京】传发【关宁军】 山海关新战备指令", "military_defense_war")
        self.check("military", "【军令变更】关宁军 山海关战备指令正式更新", "military_defense_war")
        # 行军：调用方传 official
        self.check("official", "【关宁军】自山海关开拔 进驻宁远", "military_defense_war")
        # 遣使：调用方传 official
        self.check("official", "科尔沁部遣使入贡", "diplomacy_trade")

    def test_real_disasters_still_disasters(self):
        self.check("disaster", "【陕西】发生2.30级地震 死伤120人，产能-15%", "disaster_earthquake")
        self.check("weather", "【福建】发生小热浪 强度 2", "disaster_weather")
        self.check("plague", "【河南】爆发大疫 产能-20%", "disaster_plague")
        self.check("disaster", "黄河在开封决口", "disaster_yellow_flood")
        self.check("disaster", "【陕西】发生小旱（减产-5%）", "disaster_weather")

    def test_personnel_and_arrears(self):
        self.check("official", "新官到任 3 员", "official_personnel")
        self.check("official", "按察司副使卸任", "official_personnel")
        self.check("army", "驻军欠饷2万两", "military_arrears")
        self.check("military", "补发欠饷 3 万两", "military_arrears")

    def test_every_type_has_a_schema(self):
        for kind in ("disaster_weather", "disaster_yellow_flood", "disaster_earthquake",
                     "disaster_plague", "military_arrears", "military_defense_war",
                     "works_project", "official_personnel", "justice_law",
                     "diplomacy_trade", "dept_reorg", "court_directive", "local_rumor"):
            self.assertIn(kind, VARIATION_SCHEMA_MAP, "缺类型定义：%s" % kind)

    def test_empty_pool_fallback_is_bindable(self):
        """空池兜底的三条风闻必须能查范围（以前必抛「未知变化类型」）。"""
        from engine.memorial_tools import _scope_of, list_variation_rows
        from engine.session import GameSession
        st = GameSession().state
        st.variation_pool = []          # 空池，强制走兜底
        rows = list_variation_rows(st)
        self.assertEqual(len(rows), 3)
        for row in rows:
            scope = _scope_of(row)
            self.assertEqual(scope["variation_type"], "local_rumor")
            self.assertFalse(scope["bound"])
            self.assertTrue(scope["schema_text"])


if __name__ == "__main__":
    unittest.main()
