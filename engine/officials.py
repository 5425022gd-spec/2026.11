"""官员名册：官职只认 engine.positions 那张表，本模块只提供人物侧的便利函数。

- 存储：`db/officials.db` 的 `positions` 表（六列口径见 engine/positions.py）
- 一个职位一个人；一个人可占多个职位（同一人在表里多行）
- 罢免：end_date=当日、status=removed -> 随机 1-6 月后新官 pending，到任由
  check_appointments() 推进
- 任期届满：tick_tenures() 按 end_date 换人（关键人物 end_date 为空，不换）
- **不再有**：`officials.title` 自由文本、按关键字判武职、缺员自动生成
"""

import random
from typing import Any, Dict, Optional

from . import positions as _pos

# ── 姓名生成用字（唯一一处） ─────────────────────────────
SURNAMES = list("王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文")
GIVEN_NAMES = ["文远", "伯安", "子明", "仲达", "公谨", "元直", "士元", "孝先",
               "景升", "孟起", "汉升", "子龙", "奉孝", "文若", "公达", "幼平",
               "兴霸", "德谋", "承渊", "文则", "妙才", "元让", "仲康", "公明",
               "子义", "伯符", "仲谋", "子敬", "公瑾", "德润", "子山", "休穆",
               "伯言", "承宗", "元化", "景和", "子实", "用宾", "本初", "公台",
               "文和", "孝直", "伯约", "士季", "文长", "幼常", "公休", "文鸯",
               "元俭", "子初", "季常", "德信", "文虎", "子衡", "公绩", "元放"]

# ═════════════════════════ 人物标识词表 ═════════════════════════
TAGS = {
    "status": (("active", "pending", "removed", "vacant"), "active"),
    "level": (("capital", "province"), "province"),
}
CAPITAL_LOCATIONS = "北京", "北京城", "京师", "顺天府"
CAPITAL_PROVINCE = _pos.CAPITAL_PROVINCE
CAPITAL_JURISDICTION = _pos.CAPITAL_JURISDICTION


def _conn():
    """兼容旧调用：返回 positions 表的连接。"""
    return _pos._conn()


def init_db(force: bool = False):
    _pos.init_db(force=force)


def _row_dict(row):
    """旧接口：sqlite3.Row → 字典。现在统一走 positions._row。"""
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return _pos._row(row)


def _random_name():
    return random.choice(SURNAMES) + random.choice(GIVEN_NAMES)


# ═════════════════════════ 读写 ═════════════════════════
def add_official(data: Dict[str, Any]) -> Dict[str, Any]:
    """新增一条任命。data 需有 title/name；kind 缺省按 level 推（capital→京官，其余文职）。

    category 缺省：京官取 dept，地方取 province。京官没有 dept 时按职位名反查既有槽位。
    """
    title = data.get("title") or ""
    name = data.get("name") or ""
    level = data.get("level") or ("capital" if data.get("location") in CAPITAL_LOCATIONS else "province")
    kind = data.get("kind") or ("京官" if level == "capital" else "文职")
    category = data.get("category") or data.get("dept") or ""
    if not category and kind == "京官":
        category = _pos.find_category(title)
    if not category:
        category = data.get("province") or ""
    if not category:
        raise ValueError("新增官职缺少分类：京官要部门名，地方要省名（%s）" % title)
    pid = _pos.add(title=title, person=name, kind=kind, category=category,
                   dept=data.get("dept") or (category if kind == "京官" else ""),
                   province=data.get("province") or "",
                   location=data.get("location") or "",
                   start=data.get("start_date") or "",
                   end=data.get("end_date"),
                   faction=data.get("faction") or "")
    if data.get("status") == "pending" and data.get("arrive_date"):
        _pos.update(pid, status="pending", arrive_date=data["arrive_date"])
    return get_official(pid)


def update_official(oid: int, **fields) -> bool:
    if "name" in fields:                       # 旧接口用 name 表示人名
        fields["person"] = fields.pop("name")
    return _pos.update(oid, **fields)


def delete_official(oid: int) -> bool:
    return _pos.delete(oid)


def get_official(oid):
    conn = _pos._conn()
    row = conn.execute("SELECT * FROM positions WHERE id=?", (oid,)).fetchone()
    conn.close()
    return _pos._row(row)


def spawn_official(title, province="", level="", start_date="", arrive_date=None,
                   location="", faction="", category="", kind=""):
    """随机生成一名官员接任某职位。职位必须已存在（或给出 category 新建）。"""
    k = kind or ("京官" if level == "capital" else "文职")
    cat = category or _pos.find_category(title) or province or CAPITAL_JURISDICTION
    rec = add_official({
        "name": _random_name(), "title": title, "kind": k, "category": cat,
        "province": province or (CAPITAL_JURISDICTION if k == "京官" else cat),
        "location": location or (CAPITAL_PROVINCE if k == "京官" else cat),
        "start_date": start_date, "faction": faction,
        "status": "pending" if arrive_date else "active",
        "arrive_date": arrive_date,
    })
    return rec


# ═════════════════════════ 查询 ═════════════════════════
def list_active(level=None):
    out = _pos.incumbents()
    if level:
        out = [o for o in out if o["level"] == level]
    return out


def current_office(name: str) -> str:
    """该人当前在任的官职：只认官职表的在任行。"""
    if not name:
        return ""
    return "、".join(r["title"] for r in _pos.by_person(name))


def has_active_office(name: str) -> bool:
    return bool(_pos.by_person(name))


def civil_roster(include_capital: bool = False):
    """文官名册：kind=文职（可选并入京官），按辖区分组。

    没有布尔开关式的「排除武官」参数——武官是另一类，由 list_military_by_region 读。
    """
    kinds = ("文职", "京官") if include_capital else ("文职",)
    groups: Dict[str, list] = {}
    for kind in kinds:
        for o in _pos.incumbents(kind=kind):
            key = o["province"] or "未知"
            groups.setdefault(key, []).append(o)
    for k in groups:
        groups[k].sort(key=lambda o: (rank_of(o["title"]), o["title"], o["person"]))
    return groups


MILITARY_ORDER = ["辽东", "北直隶", "南直隶", "山东", "浙江", "福建", "广东",
                  "广西", "四川", "湖广", "江西", "河南", "陕西", "贵州", "云南"]


def list_military_by_region():
    """兵部武官人事：按分类（省）分组，辽东单列在前。"""
    groups = _pos.view("武职")
    for k in groups:
        groups[k].sort(key=lambda o: (rank_of(o["title"]), o["title"], o["person"]))
    ordered = {}
    for k in MILITARY_ORDER:
        if groups.get(k):
            ordered[k] = groups[k]
    for k, v in groups.items():
        if k not in ordered and v:
            ordered[k] = v
    return ordered


def recent_history(limit=40):
    return _pos.recent(limit)


def list_in_capital():
    return [normalize_official(o) for o in _pos.incumbents(kind="京官")]


def find_official(name):
    return _pos.by_person(name)


def remove_official(name_or_id, end_date="", reason=""):
    """罢免：该人所有在任行标记 removed 并记在位末。"""
    rows = []
    if isinstance(name_or_id, int) or str(name_or_id).isdigit():
        rec = get_official(int(name_or_id))
        rows = [rec] if rec else []
    else:
        rows = _pos.by_person(str(name_or_id))
    if not rows:
        return None
    for r in rows:
        _pos.update(r["id"], status="removed", end_date=end_date)
    d = dict(rows[0])
    d["status"] = "removed"
    d["end_date"] = end_date
    return d


def schedule_successor(d, end_date, months_range=(1, 6)):
    """罢免后随机 1-6 月安排继任者 pending，到日期就任。"""
    month_num = random.randint(*months_range)
    arrive = _pos._add_months(end_date, month_num)
    return spawn_official(d["title"], d.get("province") or "", d.get("level") or "",
                          start_date=end_date, arrive_date=arrive,
                          location=d.get("location") or CAPITAL_PROVINCE,
                          category=d.get("category") or "")


def check_appointments(cur_date):
    rows = _pos.pending(cur_date)
    for r in rows:
        _pos.activate(r["id"])
    return len(rows)


def tick_tenures(cur_date):
    return _pos.replace_expired(cur_date)


def month_key_str(state):
    return "%04d-%02d" % (state.date["year"], state.date["month"])


def rank_of(title):
    """职位显示排序：总督/经略 > 巡抚 > 布政使/府尹 > 按察使 > 总兵/副总兵/都指挥使
    > 盐运使 > 道员 > 知府。只用于排序，不用于判类型。"""
    for kw, r in (("总督", 1), ("经略", 1), ("巡抚", 2), ("布政使", 3), ("府尹", 3),
                  ("按察使", 4), ("总兵", 5), ("副总兵", 5), ("都指挥使", 6),
                  ("宣抚使", 6), ("都督", 6), ("参将", 6), ("游击", 6),
                  ("盐运使", 7), ("兵备道", 8), ("分守道", 8), ("分巡道", 8),
                  ("提学道", 8), ("知府", 9)):
        if kw in title:
            return r
    return 10


# ═════════════════════════ 人物标识归一 ═════════════════════════
def canonical_location(location: str) -> str:
    loc = (location or "").strip()
    if not loc:
        return ""
    if loc in CAPITAL_LOCATIONS:
        return CAPITAL_PROVINCE
    pid = province_id_of(loc)
    if pid:
        from .distances import PROVINCE_NAMES
        return PROVINCE_NAMES.get(pid, loc)
    return loc


def is_capital_location(location: str) -> bool:
    return (location or "").strip() in CAPITAL_LOCATIONS


def province_id_of(province: str) -> str:
    from .operations import PROV_MAP
    name = (province or "").strip()
    if not name:
        return ""
    if name in PROV_MAP:
        return PROV_MAP[name]
    for pname, pid in PROV_MAP.items():
        if pname in name or name in pname:
            return pid
    return ""


def normalize_official(rec: Dict[str, Any]) -> Dict[str, Any]:
    from .factions import normalize_faction
    if not rec:
        return rec
    out = dict(rec)
    for key, (allowed, default) in TAGS.items():
        if out.get(key) not in allowed:
            out[key] = default if not out.get(key) else out[key]
    raw_loc = out.get("location")
    out["location"] = canonical_location(raw_loc)
    if out.get("level") not in TAGS["level"][0] and is_capital_location(raw_loc):
        out["level"] = "capital"
    out["province_id"] = province_id_of(out.get("province") or out.get("location"))
    if out.get("faction"):
        out["faction"] = normalize_faction(out["faction"])
    return out


def person_view(name_or_id) -> Optional[Dict[str, Any]]:
    rec = None
    if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
        rec = get_official(int(name_or_id))
    else:
        rows = _pos.by_person(str(name_or_id))
        rec = rows[0] if rows else None
    if rec is None:
        return None
    rec = normalize_official(rec)
    try:
        from .char_cards import get_card
        card = get_card(rec.get("person") or "")
        if card:
            rec["card"] = card
    except Exception:
        pass
    return rec


def generate_official(level: str = "province", province: str = "",
                      title: str = "", faction: str = "",
                      location: str = "", start_date: str = "") -> Dict[str, Any]:
    from .factions import normalize_faction
    lvl = level if level in TAGS["level"][0] else "province"
    conn = _pos._conn()
    rows = conn.execute(
        "SELECT province, COUNT(*) FROM positions WHERE province!='' GROUP BY province"
        " ORDER BY 2 DESC").fetchall()
    conn.close()
    provs = [r[0] for r in rows] or [CAPITAL_PROVINCE]
    if not province:
        province = random.choice(provs)
    if not title:
        title = "%s知府" % province
    loc = canonical_location(location) or (CAPITAL_PROVINCE if lvl == "capital" else canonical_location(province))
    if not start_date:
        start_date = "1627-08"
    rec = {
        "person": _random_name(),
        "title": title,
        "province": province,
        "kind": "京官" if lvl == "capital" else "文职",
        "category": province if lvl != "capital" else "",
        "level": lvl,
        "start_date": start_date,
        "end_date": None,
        "status": "active",
        "arrive_date": None,
        "location": loc,
        "faction": normalize_faction(faction) if faction else random.choice(
            ["无党派", "无党派", "东林党", "阉党"]),
    }
    rec["name"] = rec["person"]
    return normalize_official(rec)
