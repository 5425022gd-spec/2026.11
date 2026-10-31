# -*- coding: utf-8 -*-
"""国家 AI 的工具范式：指令清单与试跑都走工具，提示词里不再塞目录。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
os.environ["LLM_MOCK"] = "1"

from engine import faction_ai  # noqa: E402
from engine.session import GameSession  # noqa: E402


class TestFactionAiTools(unittest.TestCase):
    def setUp(self):
        self.session = GameSession()
        self.state = self.session.state

    def test_tools_have_full_schema(self):
        for make in (faction_ai._operation_rules_tool, faction_ai._try_operation_tool):
            t = make()
            for key in ("name", "description", "parameters", "output", "execute"):
                self.assertIn(key, t, "%s 缺 %s" % (make.__name__, key))
            self.assertTrue(t["description"])

    def test_prompt_has_no_operation_catalogue(self):
        text = faction_ai._read_prompt()
        self.assertNotIn("{{operation_ai}}", text)
        self.assertIn("get_faction_rules", text)

    def test_try_operation_reports_and_does_not_mutate(self):
        t = faction_ai._try_operation_tool()
        before = self.state.treasury
        value = t["execute"]({"command": "拨付国库到地方 陕西 1", "faction": "满清"},
                             self.state, None)
        self.assertIn("ok", value)
        self.assertIn("message", value)
        self.assertEqual(self.state.treasury, before, "试跑不许改真实局面")

    def test_try_operation_rejects_garbage(self):
        t = faction_ai._try_operation_tool()
        value = t["execute"]({"command": "这不是一条指令"}, self.state, None)
        self.assertFalse(value["ok"])
        self.assertTrue(value["message"])


if __name__ == "__main__":
    unittest.main()
