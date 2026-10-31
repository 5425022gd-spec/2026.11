# -*- coding: utf-8 -*-
"""存档契约：写出去什么、读回来还是什么；老存档缺字段要能迁移。

待办 18 的验收：存档读写、迁移与契约测试。
- 契约：`WorldState.to_dict()` 的每个值都必须能 JSON 序列化；
- 往返：save → load 之后关键字段一致，二次往返完全相等；
- 迁移：老存档缺 attendants / attendant_prompt / tasks_done / mainline_state 等新字段时，
  读入不崩、补默认值，且不会把已有数据抹掉。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
os.environ["LLM_MOCK"] = "1"

import engine.session as session_mod  # noqa: E402
from engine import attendants as att  # noqa: E402
from engine.session import GameSession  # noqa: E402
from engine.state import WorldState  # noqa: E402

KEY_FIELDS = ("date", "treasury", "privy_treasury", "m1", "local_reserve",
              "central_corruption", "base_prestige", "variation_seq",
              "attendants", "attendant_prompt", "tasks_done",
              "achievements_done", "mainline_state")


class TestSaveContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.mkdtemp(prefix="dsh_save_")
        cls._old_save = session_mod.SAVE_FILE
        session_mod.SAVE_FILE = os.path.join(cls._tmpdir, "save.json")

    @classmethod
    def tearDownClass(cls):
        session_mod.SAVE_FILE = cls._old_save
        try:
            os.remove(os.path.join(cls._tmpdir, "save.json"))
        except OSError:
            pass
        try:
            os.rmdir(cls._tmpdir)
        except OSError:
            pass

    def setUp(self):
        try:
            os.remove(session_mod.SAVE_FILE)
        except OSError:
            pass

    def test_to_dict_is_json_serializable(self):
        s = GameSession()
        data = s.state.to_dict()
        # 不能有 set / 自定义对象 / NaN 之类的东西混进来
        json.dumps(data, ensure_ascii=False, allow_nan=False)

    def test_save_load_roundtrip(self):
        s = GameSession()
        att.apply_slot_change(s.state, 0, "魏忠贤")
        s.state.treasury = 1234.5
        s.state.tasks_done = ["t1", "t2"]
        s._save()

        s2 = GameSession()
        s2._load()
        for f in KEY_FIELDS:
            self.assertEqual(getattr(s2.state, f), getattr(s.state, f),
                             "存档字段不一致：%s" % f)
        self.assertEqual(att.slots(s2.state)[0]["name"], "魏忠贤")

    def test_double_roundtrip_is_stable(self):
        s = GameSession()
        att.apply_slot_change(s.state, 1, "王体乾")
        d1 = s.state.to_dict()
        d2 = WorldState.from_dict(d1).to_dict()
        d3 = WorldState.from_dict(d2).to_dict()
        self.assertEqual(d2, d3, "二次往返应完全相等")

    def test_old_save_migrates(self):
        """模拟老存档：删掉本版新增字段，读入必须补齐而不是崩。"""
        s = GameSession()
        data = s.state.to_dict()
        for key in ("attendants", "attendant_prompt", "tasks_done",
                    "achievements_done", "mainline_state", "script_sessions"):
            data.pop(key, None)
        data["variation_seq"] = 0
        back = WorldState.from_dict(data)
        self.assertEqual(len(att.slots(back)), 3, "老存档要补出三格近侍")
        self.assertTrue(all(s["phrases"].get("应答") for s in att.slots(back)),
                        "老存档要补默认用语")
        self.assertEqual(back.tasks_done, [])
        self.assertEqual(back.mainline_state, {})
        self.assertEqual(back.attendant_prompt, {})

    def test_save_file_shape(self):
        s = GameSession()
        s._save()
        with open(session_mod.SAVE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("state", "armies_data", "dialogues", "memorials"):
            self.assertIn(key, data, "存档顶层缺字段：%s" % key)


if __name__ == "__main__":
    unittest.main()
