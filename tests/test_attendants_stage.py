# -*- coding: utf-8 -*-
"""近侍与立绘调度：三格近侍、用语随机抽取、变化标签、七位立绘调度。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
os.environ["LLM_MOCK"] = "1"

from engine import attendants as att  # noqa: E402
from engine import stage  # noqa: E402
from engine.session import GameSession  # noqa: E402


class TestAttendants(unittest.TestCase):
    def setUp(self):
        self.session = GameSession()
        self.state = self.session.state

    def test_three_slots_with_default_phrases(self):
        slots = att.slots(self.state)
        self.assertEqual(len(slots), 3)
        self.assertTrue(all(not s["filled"] for s in slots))
        for s in slots:
            for c in att.CATEGORIES:
                self.assertTrue(s["phrases"].get(c), "默认用语不能为空：%s" % c)

    def test_default_phrases_are_male_voice(self):
        """默认用语一律男性臣属口吻，不许出现内侍/女性自称。"""
        import json
        raw = json.load(open(att.PHRASES_FILE, encoding="utf-8"))
        for cat in att.CATEGORIES:
            for line in raw.get(cat) or []:
                self.assertNotIn("奴婢", line, "默认用语出现「奴婢」：%s" % line)
                self.assertTrue(line.strip())

    def test_legacy_phrase_migrates(self):
        """老存档里的「奴婢」自称读入时改成「臣」。"""
        self.state.attendants = [{"name": "魏忠贤",
                                  "phrases": {c: "奴婢在，听候吩咐。" for c in att.CATEGORIES}}
                                 for _ in range(3)]
        slots = att.slots(self.state)
        for s in slots:
            for c in att.CATEGORIES:
                self.assertNotIn("奴婢", s["phrases"][c])

    def test_set_slot_and_surname(self):
        att.apply_slot_change(self.state, 0, "魏忠贤")
        s = att.slots(self.state)[0]
        self.assertTrue(s["filled"])
        self.assertEqual(s["surname"], "魏")
        self.assertEqual(att.primary_slot(self.state), 0)

    def test_change_goes_to_variation_pool(self):
        before = len(self.state.historical_variations)
        att.apply_slot_change(self.state, 1, "王体乾")
        att.apply_slot_change(self.state, 1, "")          # 撤下也要记
        self.assertEqual(len(self.state.historical_variations), before + 2)
        texts = [v["text"] for v in self.state.historical_variations[-2:]]
        self.assertTrue(texts[0].startswith("【近侍】"), texts)
        self.assertIn("王体乾", texts[1])

    def test_primary_is_leftmost_filled(self):
        att.apply_slot_change(self.state, 2, "李永贞")
        self.assertEqual(att.primary_slot(self.state), 2)
        att.apply_slot_change(self.state, 0, "魏忠贤")
        self.assertEqual(att.primary_slot(self.state), 0)

    def test_phrase_random_from_semicolon(self):
        att.apply_slot_change(self.state, 0, "魏忠贤")
        att.set_phrase(self.state, 0, "应答", "甲;乙;丙")
        seen = {att.phrase(self.state, "应答") for _ in range(40)}
        self.assertTrue(seen.issubset({"甲", "乙", "丙"}))
        self.assertGreater(len(seen), 1, "分号分隔的多条应当随机抽")

    def test_empty_slot_falls_back(self):
        text = att.phrase(self.state, "问寝")
        self.assertTrue(text, "没有近侍时用默认用语，不能空")

    def test_state_roundtrip(self):
        att.apply_slot_change(self.state, 0, "魏忠贤")
        data = self.state.to_dict()
        from engine.state import WorldState
        back = WorldState.from_dict(data)
        self.assertEqual(att.slots(back)[0]["name"], "魏忠贤")


class TestStage(unittest.TestCase):
    def test_seven_positions_and_layering(self):
        cast = ["甲", "乙", "丙", "丁", "戊", "己", "庚"]
        plan = stage.plan(cast, "甲")
        self.assertEqual([p["position"] for p in plan],
                         ["中间", "左三", "右三", "左二", "右二", "左一", "右一"])
        self.assertTrue(plan[0]["active"])
        # 中间最大、最上
        self.assertGreater(plan[0]["width"], plan[1]["width"])
        self.assertGreater(plan[0]["z"], plan[1]["z"])

    def test_speaker_jumps_to_middle(self):
        plan = stage.plan(["甲", "乙", "丙"], "丙")
        self.assertEqual(plan[0]["name"], "丙")
        self.assertEqual(plan[0]["position"], "中间")
        self.assertEqual(plan[1]["name"], "甲")
        self.assertEqual(plan[1]["position"], "左三")

    def test_more_than_seven_hidden(self):
        cast = [chr(ord("甲") + i) for i in range(9)]
        plan = stage.plan(cast, "甲")
        self.assertEqual(len([p for p in plan if p["visible"]]), 7)
        self.assertEqual(len([p for p in plan if not p["visible"]]), 2)

    def test_duplicate_raises(self):
        with self.assertRaises(ValueError):
            stage.plan(["甲", "乙", "甲"], "甲")

    def test_idle_is_dimmer_and_smaller(self):
        plan = stage.plan(["甲", "乙"], "甲")
        active, idle = plan[0], plan[1]
        self.assertEqual(active["opacity"], 1.0)
        self.assertLess(idle["opacity"], 1.0)
        self.assertLess(idle["scale"], active["scale"])
        self.assertNotEqual(idle["filter"], "none")


if __name__ == "__main__":
    unittest.main()
