# -*- coding: utf-8 -*-
"""剧本事件调度：四维正交（时间/地点/人物/类型）+ 标签驱动。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import script_events as se  # noqa: E402


class FakeState:
    def __init__(self, date=None, location="乾清宫"):
        self.date = date or {"year": 1627, "month": 9, "day": 1, "hour": 8, "minute": 0}
        self.emperor_location = location
        self.script_tags = {}


def st(y=1627, mo=9, d=1, h=8, mi=0, loc="乾清宫"):
    return FakeState({"year": y, "month": mo, "day": d, "hour": h, "minute": mi}, loc)


class TestTags(unittest.TestCase):
    def test_publish_and_check(self):
        s = st()
        self.assertFalse(se.has_tag(s, "西北大旱"))
        se.publish_tag(s, "西北大旱", "pe-1")
        self.assertTrue(se.has_tag(s, "西北大旱"))
        self.assertEqual(se.tag_time(s, "西北大旱"), "1627-09-01 08:00")

    def test_republish_keeps_single_entry(self):
        s = st()
        se.publish_tag(s, "甲")
        se.publish_tag(s, "甲")
        self.assertEqual(len(s.script_tags), 1)

    def test_ensure_state_fills_missing(self):
        s = st()
        del s.script_tags
        se.ensure_state(s)
        self.assertEqual(s.script_tags, {})


class TestTime(unittest.TestCase):
    def test_parse_stamp(self):
        self.assertEqual(se.parse_stamp("1627-09-01 08:30"), (1627, 9, 1, 8, 30))
        self.assertEqual(se.parse_stamp("1627-09-01"), (1627, 9, 1, 0, 0))
        self.assertIsNone(se.parse_stamp("乱写"))

    def test_at(self):
        s = st(mi=0)
        ok, _ = se._time_due(s, {"type": "at", "at": "1627-09-01 08:00"}, None)
        self.assertTrue(ok)
        ok, _ = se._time_due(s, {"type": "at", "at": "1627-09-01 08:30"}, None)
        self.assertFalse(ok)

    def test_after_start(self):
        s = st(h=10)
        start = (1627, 9, 1, 8, 0)
        ok, _ = se._time_due(s, {"type": "after_start", "hours": 2}, start)
        self.assertTrue(ok)
        ok, _ = se._time_due(s, {"type": "after_start", "hours": 3}, start)
        self.assertFalse(ok)

    def test_after_tag(self):
        s = st(h=9)
        se.publish_tag(s, "灾报")
        s.script_tags["灾报"]["at"] = "1627-09-01 08:00"
        ok, _ = se._time_due(s, {"type": "after_tag", "tag": "灾报", "minutes": 60}, None)
        self.assertTrue(ok)
        ok, _ = se._time_due(s, {"type": "after_tag", "tag": "没有这个", "minutes": 1}, None)
        self.assertFalse(ok)

    def test_add_delta_crosses_day(self):
        self.assertEqual(se.add_delta((1627, 9, 1, 23, 30), minutes=45), (1627, 9, 2, 0, 15))


class TestPlace(unittest.TestCase):
    def test_in_building(self):
        s = st(loc="乾清宫")
        ok, _ = se._place_due(s, {"type": "in_building", "place": "乾清宫"})
        self.assertTrue(ok)
        ok, _ = se._place_due(s, {"type": "in_building", "place": "文华殿"})
        self.assertFalse(ok)

    def test_tag_absent(self):
        s = st()
        ok, _ = se._place_due(s, {"type": "tag_absent", "tag": "已赈灾"})
        self.assertTrue(ok)
        se.publish_tag(s, "已赈灾")
        ok, _ = se._place_due(s, {"type": "tag_absent", "tag": "已赈灾"})
        self.assertFalse(ok)


class TestTick(unittest.TestCase):
    def test_four_dimensions_and_once(self):
        s = st()
        evs = [{"event_id": "pe-1", "type": "memorial",
                "time": {"type": "at", "at": "1627-09-01 08:00"},
                "place": {"type": "in_building", "place": "乾清宫"}}]
        fired = {}
        got = se.tick(s, evs, None, fired)
        self.assertEqual(len(got), 1)
        self.assertIn("pe-1", fired)
        # 同一事件不会二次触发
        self.assertEqual(len(se.tick(s, evs, None, fired)), 0)

    def test_place_blocks(self):
        s = st(loc="文华殿")
        evs = [{"event_id": "pe-2", "type": "letter",
                "place": {"type": "in_building", "place": "乾清宫"}}]
        self.assertEqual(len(se.tick(s, evs, None, {})), 0)

    def test_no_conditions_always_due(self):
        s = st()
        evs = [{"event_id": "pe-3", "type": "request_dialogue"}]
        self.assertEqual(len(se.tick(s, evs, None, {})), 1)


if __name__ == "__main__":
    unittest.main()


class TestDeviation(unittest.TestCase):
    """章奏批复与剧本预设不同 → 记一条偏离；相同则不记。"""

    def setUp(self):
        import tempfile, json
        from engine import mainline_script as ms
        self.ms = ms
        self._old = ms.MAINLINES_FILE
        fd, path = tempfile.mkstemp(prefix="ml_", suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"mainlines": [{"id": "ml-1", "title": "测试", "events": []}]}, f,
                      ensure_ascii=False)
        ms.MAINLINES_FILE = path
        ms._RUNTIME_MAINLINES = None
        self._path = path

    def tearDown(self):
        self.ms.MAINLINES_FILE = self._old
        self.ms._RUNTIME_MAINLINES = None
        try:
            os.remove(self._path)
        except OSError:
            pass

    def test_deviation_recorded(self):
        s = st()
        ok = self.ms.record_deviation(s, "ml-1", "pe-1", "发帑赈济", "加派辽饷")
        self.assertTrue(ok)
        ml = self.ms.load_mainlines_data()["mainlines"][0]
        self.assertEqual(len(ml["deviations"]), 1)
        text = self.ms.get_history_script_summary(ml)
        self.assertIn("偏离记录", text)
        self.assertIn("加派辽饷", text)

    def test_same_choice_not_recorded(self):
        s = st()
        ok = self.ms.record_deviation(s, "ml-1", "pe-1", "发帑赈济", "发帑赈济")
        self.assertFalse(ok)
        ml = self.ms.load_mainlines_data()["mainlines"][0]
        self.assertEqual(ml.get("deviations"), None)
