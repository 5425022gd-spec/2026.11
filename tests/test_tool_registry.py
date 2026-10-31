# -*- coding: utf-8 -*-
"""工具表：条目完整、工厂可解析、工具名与登记一致。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import tool_registry as tr  # noqa: E402


class TestToolRegistry(unittest.TestCase):
    def test_table_not_empty(self):
        self.assertGreaterEqual(len(tr.table()), 15)

    def test_required_fields(self):
        for row in tr.table():
            for key in ("name", "kind", "module", "factory", "systems"):
                self.assertIn(key, row, "缺字段 %s：%s" % (key, row))
            self.assertIn(row["kind"], ("query", "finish"))

    def test_no_duplicate_names(self):
        names = [r["name"] for r in tr.table()]
        self.assertEqual(len(names), len(set(names)))

    def test_all_factories_resolve(self):
        self.assertEqual(tr.verify(), [])

    def test_by_name_and_for_system(self):
        self.assertEqual(tr.by_name("submit_speeches")["kind"], "finish")
        self.assertIsNone(tr.by_name("不存在的工具"))
        council = {r["name"] for r in tr.for_system("council")}
        self.assertIn("submit_speeches", council)
        self.assertIn("query_data", council)

    def test_describe_mentions_every_tool(self):
        text = tr.describe()
        for row in tr.table():
            self.assertIn(row["name"], text)


if __name__ == "__main__":
    unittest.main()
