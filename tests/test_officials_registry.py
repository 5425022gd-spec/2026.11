# -*- coding: utf-8 -*-
"""人物注册层回归测试：一份主表 + 一套标识词表 + 统一读接口 + 生成接口。

只读测试：不建库、不重置 db/officials.db、不写任何数据。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 控制台按 UTF-8 输出：本机默认 GBK 时，打印 ✓ 之类字符会 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_officials_registry.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_officials_registry_pos.json"))
from engine import officials
from engine.factions import normalize_faction


def main():
    # ── 1) 主表所有记录都必须落在标识词表内 ──
    rows = officials.list_active()
    assert rows, "主表不应为空"
    bad = []
    for r in rows:
        for key, (allowed, _default) in officials.TAGS.items():
            if r.get(key) not in allowed:
                bad.append("%s.%s=%s" % (r.get("name"), key, r.get(key)))
    assert not bad, "标识越出词表：%s" % bad[:5]
    print("主表 %d 条在职记录，标识全部落在词表内 ✓" % len(rows))

    # ── 2) 位置只到省份：京师诸写法 → 北直隶，省名原样 ──
    assert officials.canonical_location("北京城") == "北直隶"
    assert officials.canonical_location("京师") == "北直隶"
    assert officials.canonical_location("顺天府") == "北直隶"
    assert officials.canonical_location("南直隶") == "南直隶"
    assert officials.canonical_location("陕西") == "陕西"
    assert officials.canonical_location("") == ""
    cap = officials.list_in_capital()
    locs = {r["location"] for r in cap}
    assert locs <= {"北直隶"}, "在京官员位置应已归一为北直隶，实际 %s" % locs
    assert all(r["level"] == "capital" for r in cap), "在京官员一律按 level 属性取"
    all_locs = {r["location"] for r in rows}
    assert all(officials.province_id_of(x) for x in all_locs), \
        "位置应全部是省名，越出省份粒度的：%s" % [x for x in all_locs if not officials.province_id_of(x)]
    print("在京官员 %d 人，位置已归一到省份（%d 个省名，无府县）✓" % (len(cap), len(all_locs)))

    # ── 3) 归属标识：省名 → 省份 id（与超控指令共用同一份对照表）──
    assert officials.province_id_of("南直隶") == "nanzhili"
    assert officials.province_id_of("陕西") == "shaanxi"
    assert officials.province_id_of("") == ""
    v = officials.person_view("黄立极")
    assert v and v.get("province_id") == "beizhili", v and v.get("province_id")
    print("归属标识：黄立极 province=%s → %s ✓" % (v.get("province"), v.get("province_id")))

    # ── 4) 统一读接口：主表记录 + 人物卡合并 ──
    assert v["faction"] in ("东林党", "阉党", "无党派"), v["faction"]
    w = officials.person_view("毛文龙")
    assert w and "card" in w, "已建卡人物应带出人物卡"
    assert officials.person_view("并不存在的人") is None
    print("统一读接口：黄立极党派=%s；毛文龙带人物卡 ✓" % v["faction"])

    # ── 5) 生成接口：产出的标识同样合法，且不落库 ──
    for _ in range(5):
        g = officials.generate_official(level="capital", title="兵部主事")
        assert g["level"] == "capital" and g["status"] == "active"
        assert g["faction"] == normalize_faction(g["faction"])
        assert g["province_id"], "生成记录也要有归属标识：%s" % g
        assert g["location"] == "北直隶"
    g2 = officials.generate_official(level="province", province="湖广", title="湖广知府")
    assert g2["province_id"] == "huguang" and g2["location"] == "湖广"
    print("生成接口：5 名京官 + 1 名地方官，标识合法且归属可解析 ✓")

    print("\nOFFICIALS REGISTRY OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
