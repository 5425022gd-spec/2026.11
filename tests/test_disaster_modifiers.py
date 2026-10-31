# -*- coding: utf-8 -*-
"""灾害四量的修正口径：概率、减产、死亡、损毁程度都是数字，都走唯一公式。

口径（玩家定义）：修正只对数字起作用，所以任何数字都该能作为 target 被加减；
不许在代码里留「先乘一个系数」的静态特殊处理。工程减灾也只是其中一路修正来源。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

os.environ["LLM_MOCK"] = "1"

from engine import positions as _pos  # noqa: E402
from engine.modifier_registry import value as mod_value  # noqa: E402
from engine.projects import damage_of, scale, set_damage  # noqa: E402
from engine.session import GameSession  # noqa: E402


class TestDisasterModifiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 用临时官职库，免得测试改到真库
        import tempfile
        cls._tmp = os.path.join(tempfile.gettempdir(), "dsh_disaster_test.db")
        for s in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(cls._tmp + s)
            except OSError:
                pass
        cls._old = _pos.DB_PATH
        _pos.DB_PATH = cls._tmp
        _pos.init_db(force=True)

    @classmethod
    def tearDownClass(cls):
        _pos.DB_PATH = cls._old
        for s in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(cls._tmp + s)
            except OSError:
                pass

    def setUp(self):
        self.session = GameSession()
        self.state = self.session.state

    def add_modifier(self, name, effects):
        self.state.department_modifiers[name] = {
            "id": name, "name": name, "dept": "工部",
            "effects": effects,
        }

    # ── 概率 ──
    def test_flood_chance_is_a_modifier(self):
        base = mod_value(self.state, "disaster_chance", 0.70, "henan",
                         kind="yellow_flood")["value"]
        self.assertAlmostEqual(base, 0.56, places=4)   # 石堤 -20%
        self.add_modifier("河防加派", [{"target": "disaster_chance", "delta_pct": -0.50}])
        after = mod_value(self.state, "disaster_chance", 0.70, "henan",
                          kind="yellow_flood")["value"]
        self.assertAlmostEqual(after, 0.21, places=4)  # 0.70 × (1 − 0.2 − 0.5)

    # ── 减产 ──
    def test_penalty_is_a_modifier(self):
        base = mod_value(self.state, "disaster_penalty", 0.36, "henan",
                         kind="yellow_flood_major")["value"]
        self.assertAlmostEqual(base, 0.288, places=4)
        self.add_modifier("堤防岁修", [{"target": "disaster_penalty", "delta_pct": -0.25}])
        after = mod_value(self.state, "disaster_penalty", 0.36, "henan",
                          kind="yellow_flood_major")["value"]
        self.assertAlmostEqual(after, 0.198, places=4)

    # ── 死亡 ──
    def test_death_is_a_modifier(self):
        self.add_modifier("赈济灾民", [{"target": "disaster_death", "delta_pct": -0.30}])
        v = mod_value(self.state, "disaster_death", 1000.0, "henan")["value"]
        self.assertAlmostEqual(v, 700.0, places=2)

    # ── 损毁程度 ──
    def test_damage_degree_is_a_modifier(self):
        self.add_modifier("石堤加固", [{"target": "damage_degree", "delta_pct": -0.60}])
        v = mod_value(self.state, "damage_degree", 1.0, "henan",
                      kind="yellow_flood_major")["value"]
        self.assertAlmostEqual(v, 0.40, places=4)

    def test_damage_scales_building_effect(self):
        dike = None
        for pj in self.state.provinces["zhejiang"]["local"]["projects"]:
            if pj["name"] == "海宁海塘":
                dike = pj
        self.assertIsNotNone(dike)
        from engine.weather import province_flood_reduction
        self.assertAlmostEqual(province_flood_reduction(self.state, "zhejiang"), 0.70, places=4)
        set_damage(dike, 0.5)
        self.assertEqual(damage_of(dike), 0.5)
        self.assertEqual(scale(dike), 0.5)
        # 损毁一半：减免也只剩一半
        self.assertAlmostEqual(province_flood_reduction(self.state, "zhejiang"), 0.35, places=4)

    # ── 超控接口 ──
    def test_damage_override_command(self):
        from engine.operations import execute_operation_command
        ok, msg = execute_operation_command(
            self.state, "工程损毁程度 陕西 灵州黄河石堤 40")
        self.assertTrue(ok, msg)
        dike = None
        for pj in self.state.provinces["shaanxi"]["local"]["projects"]:
            if pj["name"] == "灵州黄河石堤":
                dike = pj
        self.assertAlmostEqual(damage_of(dike), 0.40, places=4)
        # 全毁后黄汛减免归零
        from engine.weather import yellow_flood_reduction
        self.assertAlmostEqual(yellow_flood_reduction(self.state), 0.12, places=4)
        ok, msg = execute_operation_command(
            self.state, "工程损毁程度 陕西 灵州黄河石堤 100")
        self.assertTrue(ok, msg)
        self.assertAlmostEqual(yellow_flood_reduction(self.state), 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
