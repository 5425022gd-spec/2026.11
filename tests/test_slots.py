"""数据空位替换测试（新规则：{{时间+地点+数据类}} 三要素强制）。

省份数据类来自地方数据（local）派生：地方人口/镇军人数/
基建程度/税收总额/扶持总额（见 engine/localdata.py）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_slots.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_slots_pos.json"))
from engine.slots import (fill_slots, parse_slot, resolve, build_element_text,
                          build_province_text, ELEMENTS)

# 快照序列（时间有序，模拟推进记录；字段 = record_snapshot 输出）
SNAPS = [
    {"year": 1627, "month": 5, "day": 1, "treasury": 240.0, "population": 11800.0,
     "army": 13.0, "navy": 0.0,
     "provinces": {"陕西": {"population": 900.0,
                            "garrison_count": 1.0, "infra": 45,
                            "tax": 5.3, "support": 12000.0},
                   "江西": {"population": 1200.0,
                            "garrison_count": 0.5, "infra": 70,
                            "tax": 9.7, "support": 7500.0}}},
    {"year": 1627, "month": 6, "day": 1, "treasury": 238.0, "population": 11803.0,
     "army": 13.1, "navy": 0.0,
     "provinces": {"陕西": {"population": 901.0,
                            "garrison_count": 1.0, "infra": 45,
                            "tax": 5.4, "support": 12000.0},
                   "江西": {"population": 1201.0,
                            "garrison_count": 0.5, "infra": 70,
                            "tax": 9.8, "support": 7500.0}}},
]


def main():
    # 1) 三要素解析（逗号分隔）
    p = parse_slot("1627年6月1日，国家，内库余额")
    assert p == ((1627, 6, 1), "国家", "内库余额"), p
    p = parse_slot("1627年6月1日，江西，镇军人数")
    assert p == ((1627, 6, 1), "江西", "镇军人数"), p
    print("三要素解析 OK")

    # 2) 缺要素不解析
    assert parse_slot("1627年6月1日，国家") is None            # 缺数据类
    assert parse_slot("1627年6月1日，内库余额") is None        # 缺地点
    assert parse_slot("1627年6月1日，广东，内库余额") is None  # 全局类不能带省名
    assert parse_slot("1627年6月1日，国家，内库") is None      # 数据类名不对
    assert parse_slot("当前，国家，内库余额") is None           # 时间格式不对
    print("缺要素拒绝 OK")

    # 3) 精确日期 + 单位（全局类 + 省份派生类）
    r = resolve("1627年6月1日，国家，内库余额", SNAPS)
    print("6月1日国家内库余额 ->", r)
    assert r == "238.0万两", r
    r = resolve("1627年6月1日，江西，镇军人数", SNAPS)
    print("6月1日江西镇军人数 ->", r)
    assert r == "0.5万人", r
    r = resolve("1627年6月1日，江西，地方人口", SNAPS)
    assert r == "1201万", r
    r = resolve("1627年6月1日，陕西，基建程度", SNAPS)
    assert r == "45", r
    r = resolve("1627年6月1日，江西，税收总额", SNAPS)
    assert r == "9.8万两", r
    r = resolve("1627年6月1日，江西，扶持总额", SNAPS)
    assert r == "7500两", r
    print("精确日期+单位 OK（含省份派生类）")

    # 4) 回溯：中间日期用最近实际记录
    r = resolve("1627年5月10日，江西，镇军人数", SNAPS)
    print("5月10日江西镇军人数（回溯）->", r)
    assert r == "0.5万人", r
    r = resolve("1627年7月15日，国家，内库余额", SNAPS)  # 晚于全部快照 -> 最新
    print("7月15日国家内库余额（超最新）->", r)
    assert r == "238.0万两", r
    # 早于全部快照 -> None
    assert resolve("1626年1月1日，国家，内库余额", SNAPS) is None
    print("回溯取值 OK")

    # 5) fill_slots 整体替换 + 无法解析保留
    text = ("伏查{{1627年5月1日，国家，内库余额}}，而{{1627年5月1日，陕西，基建程度}}，"
            "{{1627年5月1日，国家，内库}}。")
    out = fill_slots(text, SNAPS)
    print("整体替换 ->", out)
    assert "240.0万两" in out and "45" in out
    assert "{{1627年5月1日，国家，内库}}" in out  # 无法解析保留
    print("fill_slots OK")

    # 6) element / province 注入文本
    et = build_element_text()
    print("element 文本:", et.replace("\n", " | "))
    assert "内库余额" in et and "镇军人数" in et and "地方人口" in et and "全局数据类" in et
    assert "存粮石数" not in et and "税负" not in et  # 旧引擎数据类已移除
    pt = build_province_text()
    assert "江西" in pt and "辽东" in pt
    print("注入文本 OK")

    # 7) 数据字典留接口：全部数据类都能解析到字段
    for name, meta in ELEMENTS.items():
        assert meta["field"] and meta["fmt"], name
    print("数据字典完整（%d 个数据类）OK" % len(ELEMENTS))

    print("\nSLOTS OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
