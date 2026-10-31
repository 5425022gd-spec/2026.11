# -*- coding: utf-8 -*-
"""官职表口径断言：一张表六列，三类计数固定，读口只有一处。

口径（玩家定义）：
    职位名称 / 人名 / 职位类型（京官·文职·武职）/ 职位分类（部门或省）/ 开始 / 截止

这里守的是「口径不再分叉」这件事：
  ① 三类计数就是 232 / 288 / 50，合计 570；
  ② 类型与分类来自数据，不来自关键字——兵备道是文职，把总不是武职；
  ③ 同一 (职位, 分类) 唯一，在任者不超过一个；
  ④ 在任者必须有开始时间，截止不早于开始；
  ⑤ 缺员就是缺员：读界面不会凭空生成人（这条以前会，229 个京官就是这么来的）；
  ⑥ 三个前端出口的行数必须与表里按类型分组一致。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from engine import positions as _pos  # noqa: E402

EXPECTED = {"京官": 232, "文职": 288, "武职": 50}


class TestPositionsUnified(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 别的用例会往真库里增删职位，这里用一份临时库，保证计数断言稳定
        cls._tmp = os.path.join(tempfile.gettempdir(), "dsh_positions_unified.db")
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(cls._tmp + suffix)
            except OSError:
                pass
        cls._old_db = _pos.DB_PATH
        _pos.DB_PATH = cls._tmp
        _pos.init_db(force=True)

    @classmethod
    def tearDownClass(cls):
        _pos.DB_PATH = cls._old_db
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(cls._tmp + suffix)
            except OSError:
                pass

    def setUp(self):
        _pos.init_db()

    # ── ① 计数 ──
    def test_counts(self):
        self.assertEqual(_pos.assert_consistent(EXPECTED), EXPECTED)
        self.assertEqual(sum(EXPECTED.values()), 570)

    # ── ② 类型是数据，不是关键字 ──
    def test_kind_is_data_not_keywords(self):
        self.assertEqual(_pos.kind_of_title("延安府知府"), "文职")
        self.assertEqual(_pos.kind_of_title("徽宁兵备道"), "文职")
        self.assertEqual(_pos.kind_of_title("陕西提学道"), "文职")
        self.assertEqual(_pos.kind_of_title("辽东巡抚"), "文职")
        self.assertEqual(_pos.kind_of_title("总兵官（宁远）"), "武职")
        self.assertEqual(_pos.kind_of_title("辽东都指挥使"), "武职")
        # 内廷的「里外监把总」在编制里属京官，不能因为带「把总」就算武职
        self.assertNotEqual(_pos.kind_of_title("御用监里外监把总"), "武职")

    def test_categories_match_kind(self):
        from engine import depts, depts_neiting
        depts_ok = set(depts.DEPTS) | set(depts_neiting.DEPTS)
        provs = {r["category"] for r in _pos.rows(kind="文职")} | \
                {r["category"] for r in _pos.rows(kind="武职")}
        for r in _pos.rows(kind="京官"):
            self.assertIn(r["category"], depts_ok, "京官的分类必须是部门：%s" % r["title"])
        for c in provs:
            self.assertTrue(c, "地方官职的分类不能为空")
        self.assertEqual(len(provs), 16, "地方分类应为 16 个辖区，实际 %s" % sorted(provs))

    # ── ③ 唯一性 ──
    def test_unique_slot(self):
        conn = _pos._conn()
        dup = conn.execute(
            "SELECT title, category, COUNT(*) c FROM positions GROUP BY title, category"
            " HAVING c > 1").fetchall()
        conn.close()
        self.assertEqual([tuple(d) for d in dup], [])

    # ── ④ 日期 ──
    def test_dates(self):
        for r in _pos.incumbents():
            self.assertTrue(r["start_date"], "在任者缺开始时间：%s" % r["title"])
            if r["end_date"]:
                self.assertGreaterEqual(r["end_date"], r["start_date"],
                                        "截止早于开始：%s" % r["title"])

    # ── ⑤ 缺员：读界面不生成，铨选时补 ──
    def test_vacancy_does_not_spawn_on_read(self):
        before = len(_pos.rows())
        from engine import depts, depts_neiting
        from engine.session import GameSession
        st = GameSession().state
        rows = 0
        for name in list(depts.DEPTS) + list(depts_neiting.DEPTS):
            d = depts.build_dept(name, st)
            rows += len(d["staff"]) if d else 0
        self.assertEqual(len(_pos.rows()), before, "读部门界面不许往表里写人")
        self.assertEqual(rows, len(_pos.rows(kind="京官")), "部门管理层行数应等于京官槽数")
        vacant = [r for r in _pos.rows(kind="京官") if not r["person"]]
        self.assertTrue(all(r["status"] == "vacant" for r in vacant))

    def test_fill_vacancies_is_a_process(self):
        """缺员由月度铨选补，**不设上限**：缺几个补几个，补的人带开始时间。"""
        # 先人为腾空三个槽，避免依赖库里原本有几个缺员
        rows = [r for r in _pos.rows() if r["person"]][:3]
        for r in rows:
            _pos.update(r["id"], person="", status="vacant")
        before = [r for r in _pos.rows() if not r["person"]]
        self.assertGreaterEqual(len(before), 3)

        filled = _pos.fill_vacancies("1627-09")
        self.assertEqual(len(filled), len(before), "铨选不设上限：应当一次补完")
        self.assertEqual([r for r in _pos.rows() if not r["person"]], [], "补完不应再有缺员")
        for f in filled:
            row = [r for r in _pos.rows(kind=f["kind"], category=f["category"])
                   if r["title"] == f["title"]][0]
            self.assertEqual(row["person"], f["person"])
            self.assertEqual(row["start_date"], "1627-09", "补官必须写开始时间")
            self.assertEqual(row["status"], "active")

    # ── ⑥ 三个出口与表一致 ──
    def test_three_surfaces_match_table(self):
        from engine import depts_libu, depts_war
        from engine.session import GameSession
        st = GameSession().state
        ov = depts_libu.get_libu_overview(st)
        self.assertEqual(ov["roster_total"], 288)
        war = depts_war.get_military_officers_grouped()
        self.assertEqual(sum(len(v) for v in war.values()), 50)
        self.assertEqual(sum(len(v) for v in ov["roster_groups"].values()), 288)

    # ── 到期替换：只换有截止时间的，关键人物不动 ──
    def test_replace_expired_keeps_key_figures(self):
        tmp = os.path.join(tempfile.gettempdir(), "dsh_positions_test.db")
        shutil.copyfile(_pos.DB_PATH, tmp)
        old = _pos.DB_PATH
        try:
            _pos.DB_PATH = tmp
            conn = sqlite3.connect(tmp)
            key_before = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE end_date IS NULL AND person!=''").fetchone()[0]
            conn.close()
            changes = _pos.replace_expired("1635-01")
            self.assertTrue(changes, "1635 年应当有一批到期的随机官被换掉")
            conn = sqlite3.connect(tmp)
            key_after = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE end_date IS NULL AND person!=''").fetchone()[0]
            conn.close()
            self.assertEqual(key_before, key_after, "关键人物（无截止时间）不该被替换")
        finally:
            _pos.DB_PATH = old
            try:
                os.remove(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
