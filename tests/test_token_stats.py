# -*- coding: utf-8 -*-
"""模型用量统计：usage 累加、命中率、缺项按 0 计。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import llm  # noqa: E402


class TestTokenStats(unittest.TestCase):
    def setUp(self):
        llm.reset_token_stats()

    def tearDown(self):
        llm.reset_token_stats()

    def test_empty(self):
        st = llm.token_stats()
        self.assertEqual(st["calls"], 0)
        self.assertEqual(st["hit_rate"], 0.0)

    def test_accumulate(self):
        llm.record_usage({"prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200,
                          "prompt_tokens": 1000, "completion_tokens": 120})
        llm.record_usage({"prompt_cache_hit_tokens": 200, "prompt_cache_miss_tokens": 300,
                          "prompt_tokens": 500, "completion_tokens": 80})
        st = llm.token_stats()
        self.assertEqual(st["calls"], 2)
        self.assertEqual(st["cache_hit"], 1000)
        self.assertEqual(st["cache_miss"], 500)
        self.assertEqual(st["completion"], 200)
        self.assertAlmostEqual(st["hit_rate"], 1000 / 1500, places=4)

    def test_missing_fields(self):
        llm.record_usage({})
        st = llm.token_stats()
        self.assertEqual(st["calls"], 1)
        self.assertEqual(st["cache_hit"], 0)
        self.assertEqual(st["hit_rate"], 0.0)

    def test_bad_input_ignored(self):
        llm.record_usage(None)
        llm.record_usage("not a dict")
        self.assertEqual(llm.token_stats()["calls"], 0)


if __name__ == "__main__":
    unittest.main()
