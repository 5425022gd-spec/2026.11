# -*- coding: utf-8 -*-
"""立绘调整参数：按「姓名_表情」存 scale/dx/dy，基准框 420×760，辅助线 (210,100)。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import hum  # noqa: E402


class TestHumAdjust(unittest.TestCase):
    def setUp(self):
        self._old = hum.ADJUST_FILE
        fd, path = tempfile.mkstemp(prefix="hum_adjust_", suffix=".json")
        os.close(fd)
        os.remove(path)
        hum.ADJUST_FILE = path
        self._path = path

    def tearDown(self):
        hum.ADJUST_FILE = self._old
        try:
            os.remove(self._path)
        except OSError:
            pass

    def test_default_is_identity(self):
        a = hum.get_adjust("魏忠贤", "常")
        self.assertEqual((a["scale"], a["dx"], a["dy"]), (1.0, 0.0, 0.0))

    def test_save_and_read_back(self):
        hum.save_adjust("魏忠贤", "常", 1.25, -8.5, 12.0)
        a = hum.get_adjust("魏忠贤", "常")
        self.assertAlmostEqual(a["scale"], 1.25, places=4)
        self.assertAlmostEqual(a["dx"], -8.5, places=2)
        self.assertAlmostEqual(a["dy"], 12.0, places=2)

    def test_clamped(self):
        a = hum.save_adjust("魏忠贤", "喜", 99, -999, 999)
        self.assertLessEqual(a["scale"], 3.0)
        self.assertGreaterEqual(a["dx"], -100.0)
        self.assertLessEqual(a["dy"], 100.0)

    def test_reject_bad_expr(self):
        with self.assertRaises(ValueError):
            hum.save_adjust("魏忠贤", "怒斥", 1, 0, 0)

    def test_frame_and_guide(self):
        f = hum.frame_info()
        self.assertEqual((f["w"], f["h"]), (420, 760))
        self.assertEqual((f["guide_x"], f["guide_y"]), (210, 100))


if __name__ == "__main__":
    unittest.main()
