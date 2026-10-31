# -*- coding: utf-8 -*-
"""重建官职库：从 0 生成 data/positions_seed.json（570 行）并重建 db 里的 positions 表。

口径（唯一一处，玩家定义）：
  一张表六列 = 职位名称 / 人名 / 职位类型（京官·文职·武职）/ 职位分类（部门或省）/
               开始时间 / 截止时间

三个来源合成一次，之后运行期只读这张表：
  1. 京官编制：engine/depts.py 的 DEPTS（六部+外诸司 21 个部门）与
     engine/depts_neiting.py 的 DEPTS（内廷 24 个部门）里的 staff 职位名，共 232 槽。
     engine/depts_waizhusi.py 的 DEPTS 与 depts.DEPTS 重复定义同一批 15 个部门，
     不再读它——重复定义是旧口径分叉的根源。
  2. 京官人名：data/positions.json 的既有绑定（按「部门+职位名」或职位名匹配）。
  3. 地方官职：data/officials_roster.json 的 338 行地方官（288 文职 + 50 武职），
     类型由下面的武职名单一次性标注，不再用关键字现算。

运行：python tools/rebuild_positions.py
"""

import json
import os
import sqlite3
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE)

SEED_IN = os.path.join(_BASE, "data", "officials_roster.json")
POS_IN = os.path.join(_BASE, "data", "positions.json")
SEED_OUT = os.path.join(_BASE, "data", "positions_seed.json")
DB_PATH = os.path.join(_BASE, "db", "officials.db")

# ── 武职名单：一次性人工标注，之后固化进 positions_seed.json ──
# 判据是「明代武职官名」，不是关键字：兵备道/分守道/提学道/巡抚/总督/布政使/按察使
# 全是文职，一个都不能进来；御用监里外监把总这种内廷职也不在这里（它在京官编制里）。
MILITARY_TITLES = {
    "总兵官（宁远）", "总兵官（锦州）", "副总兵官（锦州）", "东江总兵官", "南澳副总兵",
    "中左所游击", "石砫宣抚使",
    "辽东都指挥使", "蓟镇总兵官", "宣府总兵官", "昌平总兵官", "密云总兵官",
    "山海关总兵官", "天津总兵官", "保定总兵官", "江南总兵官", "狼山总兵官",
    "淮安总兵官", "登莱总兵官", "山东都指挥使", "大同总兵官", "山西总兵官",
    "山西都指挥使", "河南总兵官", "河南都指挥使", "延绥总兵官", "宁夏总兵官",
    "甘肃总兵官", "固原总兵官", "临洮总兵官", "陕西都指挥使", "四川总兵官",
    "四川都指挥使", "湖广总兵官", "郧阳总兵官", "湖广都指挥使", "江西总兵官",
    "江西都指挥使", "浙江总兵官", "浙江都指挥使", "福建总兵官", "福建都指挥使",
    "广东总兵官", "广东都指挥使", "广西总兵官", "广西都指挥使", "镇守云南总兵官",
    "云南都指挥使", "贵州总兵官", "贵州都指挥使",
}

CAPITAL_START = "1627-08"      # 京官缺省到任时间（开局）


def dept_establishment():
    """45 个部门 → [{"dept": 部门名, "title": 职位名}]，共 232 槽。"""
    from engine import depts, depts_neiting
    slots = []
    seen = set()
    for D in (depts.DEPTS, depts_neiting.DEPTS):
        for dept, cfg in D.items():
            if dept in seen:
                continue
            seen.add(dept)
            for spec in cfg.get("staff") or []:
                slots.append({"dept": dept, "title": spec["title"]})
    return slots


def load_bindings():
    """京官人名的来源，按优先级取：

    1. 已有的 data/positions_seed.json（重跑时保持人名不变，脚本幂等）；
    2. 旧编制文件 data/positions.json（只在新库第一次生成时存在）。
    """
    by_dept, by_title = {}, {}
    if os.path.exists(SEED_OUT):
        with open(SEED_OUT, encoding="utf-8") as f:
            for row in json.load(f):
                if row.get("kind") != "京官" or not row.get("person"):
                    continue
                by_dept[(row["category"], row["title"])] = row["person"]
                by_title.setdefault(row["title"], row["person"])
        if by_dept:
            return by_dept, by_title
    if not os.path.exists(POS_IN):
        return by_dept, by_title
    with open(POS_IN, encoding="utf-8") as f:
        reg = json.load(f)
    for dept, cfg in (reg.get("departments") or {}).items():
        for p in cfg.get("positions") or []:
            name = (p.get("name") if isinstance(p, dict) else p) or ""
            person = (p.get("person") if isinstance(p, dict) else "") or ""
            if not name:
                continue
            by_dept[(dept, name)] = person
            by_title.setdefault(name, person)
    return by_dept, by_title


def build_rows():
    with open(SEED_IN, encoding="utf-8") as f:
        roster = json.load(f)
    by_dept, by_title = load_bindings()

    # 种子里有名字的京官：按职位名精确匹配
    capital_named = {}
    for r in roster:
        if r.get("level") == "capital":
            capital_named[r["title"]] = r

    rows = []
    # ── 京官 232 槽 ──
    for slot in dept_establishment():
        dept, title = slot["dept"], slot["title"]
        person = by_dept.get((dept, title)) or by_title.get(title) or ""
        start, end = CAPITAL_START, None
        # 种子里有同名京官（如「内阁首辅、武英殿大学士」黄立极）时，取其为关键人物
        named = capital_named.get(title)
        if not named:
            for k, v in capital_named.items():
                if title and title in k:
                    named = v
                    break
        if named:
            person = person or named["name"]
            start = named.get("start") or start
            end = named.get("end")
        rows.append({
            "title": title,
            "person": person,
            "kind": "京官",
            "category": dept,
            "dept": dept,
            "province": "京师",
            "location": "北直隶",
            "start": start,
            "end": end,
        })

    # ── 地方 338 行 ──
    for r in roster:
        if r.get("level") != "province":
            continue
        title = r["title"]
        rows.append({
            "title": title,
            "person": r["name"],
            "kind": "武职" if title in MILITARY_TITLES else "文职",
            "category": r["province"],
            "dept": "",
            "province": r["province"],
            "location": r.get("location") or r["province"],
            "start": r.get("start") or CAPITAL_START,
            "end": r.get("end"),
        })
    return rows


SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,                  -- 职位名称
    person     TEXT NOT NULL DEFAULT '',       -- 人名（空 = 缺员）
    kind       TEXT NOT NULL,                  -- 职位类型：京官 / 文职 / 武职
    category   TEXT NOT NULL,                  -- 职位分类：京官填部门名，地方填省名
    dept       TEXT NOT NULL DEFAULT '',       -- 京官所属部门（冗余，便于按部门查）
    province   TEXT NOT NULL DEFAULT '',       -- 辖区：京官 = 京师
    location   TEXT NOT NULL DEFAULT '',       -- 驻地：京官 = 北直隶
    start_date TEXT NOT NULL DEFAULT '',       -- 开始时间
    end_date   TEXT,                           -- 截止时间（空 = 在任 / 关键人物不替换）
    status     TEXT NOT NULL DEFAULT 'active', -- active / vacant / removed
    arrive_date TEXT,                          -- 候任到任日（pending）
    faction    TEXT NOT NULL DEFAULT '',       -- 党派
    UNIQUE (title, category)
);
CREATE INDEX IF NOT EXISTS idx_pos_kind ON positions(kind, category);
CREATE INDEX IF NOT EXISTS idx_pos_person ON positions(person);
"""


def rebuild_db(rows, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS positions")
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM positions")
    for r in rows:
        status = "active" if r["person"] else "vacant"
        conn.execute(
            "INSERT INTO positions(title,person,kind,category,dept,province,location,"
            "start_date,end_date,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (r["title"], r["person"], r["kind"], r["category"], r["dept"],
             r["province"], r["location"], r["start"], r["end"], status))
    conn.commit()
    conn.close()


def counts(rows):
    out = {}
    for r in rows:
        out[r["kind"]] = out.get(r["kind"], 0) + 1
    return out


def main():
    rows = build_rows()
    c = counts(rows)
    print("生成行数：", len(rows), c)
    assert c.get("京官") == 232, "京官应为 232 槽，实际 %s" % c.get("京官")
    assert c.get("文职") == 288, "文职应为 288，实际 %s" % c.get("文职")
    assert c.get("武职") == 50, "武职应为 50，实际 %s" % c.get("武职")
    assert len(rows) == 570

    with open(SEED_OUT, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("已写入", SEED_OUT)

    rebuild_db(rows)
    print("已重建", DB_PATH, "的 positions 表")

    conn = sqlite3.connect(DB_PATH)
    print("库内统计：",
          conn.execute("SELECT kind, COUNT(*) FROM positions GROUP BY kind").fetchall())
    print("缺员槽：", conn.execute(
        "SELECT COUNT(*) FROM positions WHERE person=''").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
