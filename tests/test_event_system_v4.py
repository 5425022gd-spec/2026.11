"""事件系统（四类事件体系、中旨、六科封驳、党派决策与变化池）全面单元测试。"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_event_system_v4.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_event_system_v4_pos.json"))
from engine.session import GameSession
from engine.factions import (
    get_character_faction, get_central_faction_distribution,
    get_cabinet_majority_faction, get_silijian_faction,
    evaluate_memorial_decision
)
from engine.memorials import (
    generate_filler, generate_side, generate_mainline,
    pick_memorial_writer, travel_days
)
from engine.variations import record_variation
from engine.depts_rites import calc_prestige


class TestEventSystemV4(unittest.TestCase):
    def setUp(self):
        self.session = GameSession()

    def test_four_event_categories(self):
        """测试四类事件体系结构与标识。"""
        # 1. 不重要事件（n层）
        fillers = generate_filler(2, state=self.session.state, id_fn=self.session.next_event_id)
        self.assertEqual(len(fillers), 2)
        for f in fillers:
            self.assertTrue(f["id"].startswith("n-"))
            self.assertEqual(f["source"], "filler")
            self.assertIn("draft_processed", f)
            self.assertIn("draft_unprocessed", f)
            self.assertIn("reward_processed", f)
            self.assertIn("reward_unprocessed", f)
            self.assertIn("decision_type", f)

        # 2. 次要事件（m层）
        sides = generate_side(self.session.state, self.session.llm, count=2, id_fn=self.session.next_event_id)
        self.assertEqual(len(sides), 2)
        for s in sides:
            self.assertTrue(s["id"].startswith("m-"))
            self.assertEqual(s["source"], "side")
            self.assertIn("draft_processed", s)
            self.assertIn("draft_unprocessed", s)

        # 3. 主要事件（l层）
        mainlines = generate_mainline(self.session.state, self.session.llm, id_fn=self.session.next_event_id)
        self.assertTrue(len(mainlines) > 0)
        self.assertTrue(mainlines[0]["id"].startswith("l-"))
        self.assertEqual(mainlines[0]["source"], "mainline")

    def test_factions_and_dual_drafts(self):
        """测试党派判定与内阁票拟决策。"""
        # 核心人物党派
        self.assertEqual(get_character_faction("魏忠贤"), "阉党")
        self.assertEqual(get_character_faction("施凤来"), "阉党")
        self.assertEqual(get_character_faction("韩爌"), "东林党")

        # 中央在职官员党派比例
        dist = get_central_faction_distribution()
        self.assertIn("阉党", dist)

        # 内阁多数党派
        cab_majority = get_cabinet_majority_faction(self.session.state)
        self.assertTrue(cab_majority in ("阉党", "无党派", "东林党"))

        # 双票拟与双奖励决策评估
        sample_memorial = {
            "title": "题本 · 特纠不职事（知府 魏德谋）",
            "accused_name": "魏忠贤",  # 涉事人：阉党
            "accused_title": "司礼监掌印太监",
            "draft_processed": "敕下按察司严查。",
            "draft_unprocessed": "事涉机密，原疏留中。",
            "reward_processed": "吏治整饬",
            "reward_unprocessed": "因循守旧"
        }
        dec = evaluate_memorial_decision(sample_memorial, self.session.state)
        self.assertIn("is_same_faction", dec)
        self.assertIn("decision_type", dec)
        self.assertIn("selected_draft", dec)
        self.assertIn("selected_reward", dec)

    def test_zhongzhi(self):
        """测试中旨的威望扣减、概率阻拦与成功执行。"""
        self.session.state.base_prestige = 60.0
        init_prestige = calc_prestige(self.session.state)["value"]

        # 发起中旨
        res_z = self.session.issue_zhongzhi("调拨户部太仓银五万两")
        self.assertIn("ok", res_z)
        after_z_prestige = calc_prestige(self.session.state)["value"]
        # 威望扣减 -3.0
        self.assertAlmostEqual(init_prestige - after_z_prestige, 3.0, delta=0.5)

    def test_annotate_is_record_only(self):
        """打字批阅只作记录：不再有六科封驳 AI、不再进事件树追踪（章奏回复 AI 已删除）。"""
        mems = self.session.list_memorials()
        self.assertTrue(len(mems) > 0)
        target = mems[0]

        ok, msg = self.session.memorial_act(target["id"], "annotate", "务求查明实情。")
        self.assertTrue(ok)
        self.assertEqual(target["status"], "annotated")
        self.assertEqual(target.get("pizou"), "务求查明实情。")
        self.assertIn("务求查明实情。", target.get("annotations") or [])
        # 事件树现在只记剧本工作流，不再记章奏升级
        self.assertEqual(self.session.script_sessions, [])
        self.assertFalse(hasattr(self.session, "tracking"))

    def test_variation_pool(self):
        """测试变化池添加与抽取。"""
        record_variation(self.session.state, "disaster", "河南发生特大水灾", province="河南")
        self.assertTrue(len(self.session.state.variation_pool) > 0)
        self.assertIn("disaster", self.session.state.variation_pool[-1]["type"])
        self.assertIn("河南", self.session.state.variation_pool[-1]["text"])


if __name__ == "__main__":
    unittest.main()
