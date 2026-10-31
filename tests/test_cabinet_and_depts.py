import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_cabinet_and_depts.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_cabinet_and_depts_pos.json"))
from engine.session import GameSession
from engine.depts import build_dept
from engine.char_cards import list_cards, get_card


class TestCabinetAndDepts(unittest.TestCase):
    def setUp(self):
        self.session = GameSession()

    def test_cabinet_staff(self):
        cabinet = build_dept("内阁", self.session.state)
        self.assertIsNotNone(cabinet)
        self.assertEqual(cabinet["name"], "内阁")
        
        expected_staff = [
            ("内阁首辅", "黄立极"),
            ("武英殿大学士", "黄立极"),
            ("建极殿大学士", "施凤来"),
            ("中极殿大学士", "张瑞图"),
            ("文渊阁大学士", "李国𣚴"),
            ("东阁大学士", "秦子龙"),
            ("中书舍人", "方奉孝"),
        ]
        
        staff = cabinet["staff"]
        self.assertEqual(len(staff), len(expected_staff))
        for (exp_title, exp_name), s in zip(expected_staff, staff):
            self.assertEqual(s["title"], exp_title)
            self.assertEqual(s["name"], exp_name)

    def test_character_cards(self):
        expected_names = ["黄立极", "施凤来", "张瑞图", "李国𣚴", "秦子龙", "方奉孝"]
        all_cards = {c["name"]: c for c in list_cards()}
        
        for name in expected_names:
            self.assertIn(name, all_cards)
            card = get_card(name)
            self.assertIsNotNone(card)
            self.assertIn("basic", card)
            self.assertIn("性格", card)
            self.assertIn("生平", card)
            self.assertIn("state", card)
            self.assertEqual(card["basic"]["姓名"], name)

    def test_other_depts(self):
        for name in ["司礼监", "都察院", "六科给事中", "通政使司"]:
            d = build_dept(name, self.session.state)
            self.assertIsNotNone(d)
            self.assertEqual(d["name"], name)
            self.assertTrue(len(d["staff"]) > 0)


if __name__ == "__main__":
    unittest.main()
