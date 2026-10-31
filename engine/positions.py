# -*- coding: utf-8 -*-
"""官职表：全项目唯一的官职来源（一张表，六列口径）。

口径（玩家定义，本模块是唯一实现处）：

    一张表六列 = 职位名称 / 人名 / 职位类型（京官·文职·武职）/
                 职位分类（京官填部门名，地方填省名）/ 开始时间 / 截止时间

- **职位类型是数据，不是判断**：写进 `kind` 列，取值只许是 `KINDS` 三个。
  不再有 `is_military_title()` 那种按关键字猜的做法——兵备道、提学道、巡抚、
  总督都是文职，靠字面永远分不清；内廷的「里外监把总」会被「把总」误判成武职。
- **职位分类**是前端的分栏键：京官＝部门（吏部、司礼监…），文职/武职＝省份（辽东…）。
- **开始时间 / 截止时间**：`end_date` 为空＝在任或关键人物（不参与到期替换）；
  填了日期＝该随机官员到点换人（`replace_expired`）。
- **人名可以为空**：空＝缺员。缺员就显示缺员，**不许在读取时凭空生成一个人**。
- 数据源只有一份：`data/positions_seed.json`（570 行，由 tools/rebuild_positions.py 生成）。
  运行期所有增删改都落这张表，`db/officials.db` 的 `positions` 表就是它。
"""

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = (os.environ.get("OFFICIALS_DB")
           or os.environ.get("OFFICIALS_DB_PATH")
           or os.path.join(_BASE, "db", "officials.db"))
SEED_PATH = os.path.join(_BASE, "data", "positions_seed.json")

KINDS = ("京官", "文职", "武职")
CAPITAL_PROVINCE = "北直隶"
CAPITAL_JURISDICTION = "京师"

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    person     TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL CHECK (kind IN ('京官','文职','武职')),
    category   TEXT NOT NULL,
    dept       TEXT NOT NULL DEFAULT '',
    province   TEXT NOT NULL DEFAULT '',
    location   TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date   TEXT,
    status     TEXT NOT NULL DEFAULT 'active',
    arrive_date TEXT,
    faction    TEXT NOT NULL DEFAULT '',
    UNIQUE (title, category)
);
CREATE INDEX IF NOT EXISTS idx_pos_kind ON positions(kind, category);
CREATE INDEX IF NOT EXISTS idx_pos_person ON positions(person);
"""

COLUMNS = ("id", "title", "person", "kind", "category", "dept", "province",
           "location", "start_date", "end_date", "status", "arrive_date", "faction")


# ═════════════════════════ 连接与建库 ═════════════════════════
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(force: bool = False):
    """建表；空表时从 data/positions_seed.json 灌一次。幂等。"""
    conn = _conn()
    conn.executescript(SCHEMA)
    n = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    if force:
        conn.execute("DELETE FROM positions")
        n = 0
    if n == 0 and os.path.exists(SEED_PATH):
        with open(SEED_PATH, encoding="utf-8") as f:
            seed = json.load(f)
        for r in seed:
            conn.execute(
                "INSERT OR IGNORE INTO positions(title,person,kind,category,dept,province,"
                "location,start_date,end_date,status,faction) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (r["title"], r.get("person") or "", r["kind"], r["category"],
                 r.get("dept") or "", r.get("province") or "", r.get("location") or "",
                 r.get("start") or "", r.get("end"),
                 "active" if (r.get("person") or "") else "vacant",
                 r.get("faction") or ""))
    conn.commit()
    conn.close()


# ═════════════════════════ 行 → 字典 ═════════════════════════
def _row(row) -> Dict[str, Any]:
    if row is None:
        return None
    d = {k: row[k] for k in COLUMNS if k in row.keys()}
    d["name"] = d.get("person") or ""            # 旧接口字段名兼容
    d["level"] = "capital" if d.get("kind") == "京官" else "province"
    d["province_id"] = ""                        # 由调用方按需补
    return d


# ═════════════════════════ 查询 ═════════════════════════
def rows(kind: Optional[str] = None, category: Optional[str] = None,
         include_vacant: bool = True, status: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM positions WHERE 1=1"
    args: List[Any] = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if category:
        sql += " AND category=?"
        args.append(category)
    if status:
        sql += " AND status=?"
        args.append(status)
    elif not include_vacant:
        sql += " AND person!=''"
    conn = _conn()
    out = [_row(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]
    conn.close()
    return out


def incumbents(kind: Optional[str] = None, category: Optional[str] = None) -> List[Dict[str, Any]]:
    """在任行（人名非空、状态 active）。"""
    return [r for r in rows(kind=kind, category=category) if r["person"] and r["status"] == "active"]


def view(kind: str) -> Dict[str, List[Dict[str, Any]]]:
    """按分类分组：{辽东: [...], 湖广: [...]}，前端直接按这个分栏。"""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in incumbents(kind=kind):
        groups.setdefault(r["category"], []).append(r)
    return groups


def by_person(person: str) -> List[Dict[str, Any]]:
    if not person:
        return []
    conn = _conn()
    out = [_row(r) for r in conn.execute(
        "SELECT * FROM positions WHERE person=? AND status='active' ORDER BY id",
        (person,)).fetchall()]
    conn.close()
    return out


def occupant(title: str, category: str = "") -> str:
    conn = _conn()
    sql = "SELECT person FROM positions WHERE title=? AND status='active'"
    args: List[Any] = [title]
    if category:
        sql += " AND category=?"
        args.append(category)
    row = conn.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()
    conn.close()
    return (row["person"] if row else "") or ""


def exists(title: str, category: str = "") -> bool:
    conn = _conn()
    sql = "SELECT 1 FROM positions WHERE title=?"
    args: List[Any] = [title]
    if category:
        sql += " AND category=?"
        args.append(category)
    row = conn.execute(sql + " LIMIT 1", args).fetchone()
    conn.close()
    return bool(row)


def categories(kind: str) -> List[str]:
    conn = _conn()
    out = [r[0] for r in conn.execute(
        "SELECT DISTINCT category FROM positions WHERE kind=? ORDER BY category", (kind,))]
    conn.close()
    return out


def kind_of_title(title: str) -> str:
    """某职位名的类型（数据里查，不做字面判断）。查不到返回空串。"""
    if not title:
        return ""
    conn = _conn()
    row = conn.execute("SELECT kind FROM positions WHERE title=? LIMIT 1", (title,)).fetchone()
    conn.close()
    return row["kind"] if row else ""


def find_category(title: str) -> str:
    """某职位名所属分类（京官=部门名，地方=省名）。查不到返回空串。"""
    if not title:
        return ""
    conn = _conn()
    row = conn.execute("SELECT category FROM positions WHERE title=? LIMIT 1", (title,)).fetchone()
    conn.close()
    return row["category"] if row else ""


# ═════════════════════════ 增删改 ═════════════════════════
def add(title: str, person: str = "", kind: str = "京官", category: str = "",
        dept: str = "", province: str = "", location: str = "",
        start: str = "", end: Optional[str] = None, faction: str = "") -> int:
    """新增一个职位槽（可带人）。kind 必须是三值之一，其余字段按类型补缺省。"""
    if not title:
        raise ValueError("职位名不能为空")
    if kind not in KINDS:
        raise ValueError("职位类型必须是 %s 之一，收到：%s" % ("/".join(KINDS), kind))
    category = category or dept or province
    if not category:
        raise ValueError("职位分类不能为空（京官填部门名，地方填省名）")
    if kind == "京官":
        dept = dept or category
        province = province or CAPITAL_JURISDICTION
        location = location or CAPITAL_PROVINCE
    else:
        province = province or category
        location = location or province
    start = start or "1627-08"
    with _lock:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO positions(title,person,kind,category,dept,province,location,"
            "start_date,end_date,status,faction) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (title, person or "", kind, category, dept, province, location, start, end,
             "active" if person else "vacant", faction or ""))
        conn.commit()
        pid = cur.lastrowid
        conn.close()
    return pid


def update(pid: int, **fields) -> bool:
    allowed = {k: v for k, v in fields.items() if k in COLUMNS and k != "id"}
    if not allowed:
        return False
    if "kind" in allowed and allowed["kind"] not in KINDS:
        raise ValueError("职位类型必须是 %s 之一" % "/".join(KINDS))
    if "person" in allowed:
        allowed["status"] = "active" if allowed["person"] else "vacant"
    sets = ", ".join("%s=?" % k for k in allowed)
    with _lock:
        conn = _conn()
        conn.execute("UPDATE positions SET %s WHERE id=?" % sets,
                     list(allowed.values()) + [pid])
        conn.commit()
        conn.close()
    return True


def delete(pid: int) -> bool:
    with _lock:
        conn = _conn()
        cur = conn.execute("DELETE FROM positions WHERE id=?", (pid,))
        conn.commit()
        n = cur.rowcount
        conn.close()
    return n > 0


def remove(title: str, category: str = "") -> bool:
    """裁撤职位：整槽删除。"""
    with _lock:
        conn = _conn()
        sql = "DELETE FROM positions WHERE title=?"
        args: List[Any] = [title]
        if category:
            sql += " AND category=?"
            args.append(category)
        cur = conn.execute(sql, args)
        conn.commit()
        n = cur.rowcount
        conn.close()
    return n > 0


def assign(title: str, person: str, start: str, category: str = "") -> bool:
    """到任：把某槽的人换成 person，旧任记为 removed（保留起止）。"""
    with _lock:
        conn = _conn()
        sql = "SELECT id,person FROM positions WHERE title=? AND status IN ('active','vacant')"
        args: List[Any] = [title]
        if category:
            sql += " AND category=?"
            args.append(category)
        row = conn.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()
        if not row:
            conn.close()
            return False
        conn.execute("UPDATE positions SET person=?, status='active', start_date=?,"
                     " end_date=NULL WHERE id=?", (person, start, row["id"]))
        conn.commit()
        conn.close()
    return True


def vacate(title: str, end: str, category: str = "") -> bool:
    with _lock:
        conn = _conn()
        sql = "SELECT id FROM positions WHERE title=? AND person!=''"
        args: List[Any] = [title]
        if category:
            sql += " AND category=?"
            args.append(category)
        row = conn.execute(sql + " ORDER BY id LIMIT 1", args).fetchone()
        if not row:
            conn.close()
            return False
        conn.execute("UPDATE positions SET person='', status='vacant', end_date=? WHERE id=?",
                     (end, row["id"]))
        conn.commit()
        conn.close()
    return True


def replace_expired(now: str) -> List[Dict[str, Any]]:
    """到期替换：截止时间已到的行换一个人（关键人物 end_date 为空，永不替换）。"""
    import random

    from .officials import _random_name  # 姓名生成仍归 officials 一处

    changes: List[Dict[str, Any]] = []
    with _lock:
        conn = _conn()
        due = conn.execute(
            "SELECT * FROM positions WHERE status='active' AND end_date IS NOT NULL"
            " AND end_date <= ? AND person!='' ORDER BY end_date, id", (now,)).fetchall()
        for r in due:
            new_name = _random_name()
            new_end = _add_months(r["end_date"], random.randint(24, 72))
            conn.execute("UPDATE positions SET person=?, start_date=?, end_date=? WHERE id=?",
                         (new_name, r["end_date"], new_end, r["id"]))
            changes.append({"old": r["person"], "new": new_name, "title": r["title"],
                            "province": r["province"], "old_end": r["end_date"],
                            "kind": r["kind"]})
        conn.commit()
        conn.close()
    return changes


def fill_vacancies(now: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """吏部铨选补官：把缺员槽补上人，写开始时间。

    这是**游戏进程**里的补官，不是读界面的副作用：只在月度结算时调用。
    **不设上限**：缺几个补几个（`limit` 只留给测试用）。
    关键人物（end_date 为空）在任时不会成为缺员，所以不受影响。
    """
    from .officials import _random_name

    out: List[Dict[str, Any]] = []
    with _lock:
        conn = _conn()
        sql = "SELECT * FROM positions WHERE status='vacant' ORDER BY id"
        args: List[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        rows = conn.execute(sql, args).fetchall()
        for r in rows:
            name = _random_name()
            conn.execute(
                "UPDATE positions SET person=?, status='active', start_date=?,"
                " end_date=NULL WHERE id=?", (name, now, r["id"]))
            out.append({"person": name, "title": r["title"], "kind": r["kind"],
                        "category": r["category"], "start": now})
        conn.commit()
        conn.close()
    return out


def recent(limit: int = 40) -> List[Dict[str, Any]]:
    conn = _conn()
    out = [_row(r) for r in conn.execute(
        "SELECT * FROM positions WHERE status='removed' AND end_date IS NOT NULL"
        " ORDER BY end_date DESC, id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return out


def pending(cur: str) -> List[Dict[str, Any]]:
    conn = _conn()
    out = [_row(r) for r in conn.execute(
        "SELECT * FROM positions WHERE status='pending' AND arrive_date IS NOT NULL"
        " AND arrive_date <= ?", (cur,)).fetchall()]
    conn.close()
    return out


def activate(pid: int) -> None:
    with _lock:
        conn = _conn()
        conn.execute("UPDATE positions SET status='active', arrive_date=NULL WHERE id=?", (pid,))
        conn.commit()
        conn.close()


# ═════════════════════════ 断言 ═════════════════════════
def assert_consistent(expected: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """口径守门人：类型计数、分类合法性、唯一性、日期顺序。返回计数。"""
    conn = _conn()
    counts = dict(conn.execute("SELECT kind, COUNT(*) FROM positions GROUP BY kind").fetchall())
    bad_kind = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE kind NOT IN ('京官','文职','武职')").fetchone()[0]
    dup = conn.execute(
        "SELECT COUNT(*) FROM (SELECT title, category FROM positions"
        " GROUP BY title, category HAVING COUNT(*)>1)").fetchone()[0]
    bad_dates = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE end_date IS NOT NULL AND end_date!=''"
        " AND start_date!='' AND end_date < start_date").fetchone()[0]
    no_start = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE person!='' AND (start_date IS NULL OR start_date='')"
    ).fetchone()[0]
    conn.close()
    assert bad_kind == 0, "有 %d 行的职位类型不在三值之内" % bad_kind
    assert dup == 0, "有 %d 组 (职位名, 分类) 重复" % dup
    assert bad_dates == 0, "有 %d 行截止时间早于开始时间" % bad_dates
    assert no_start == 0, "有 %d 名在任者没有开始时间" % no_start
    if expected:
        for k, v in expected.items():
            assert counts.get(k) == v, "%s 应为 %d，实际 %s" % (k, v, counts.get(k))
    return counts


def _add_months(ym: str, n: int) -> str:
    y, m = (int(x) for x in ym.split("-"))
    m += n
    while m > 12:
        m -= 12
        y += 1
    return "%04d-%02d" % (y, m)


init_db()
