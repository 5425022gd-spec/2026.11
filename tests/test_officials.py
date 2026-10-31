"""官员库测试：硬编码开局、随机生成、罢免、继任调度。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

# 这个用例会 init_db(force=True) 重置名册，必须打在临时库上，
# 否则跑一次测试就把玩家的 db/officials.db 退回开局名单。须在 import engine 之前设好。
_TMP_DB = os.path.join(tempfile.gettempdir(), "dsh_officials_test.db")
for _suffix in ("", "-journal", "-wal", "-shm"):
    try:
        os.remove(_TMP_DB + _suffix)
    except OSError:
        pass
os.environ["OFFICIALS_DB_PATH"] = _TMP_DB

from engine.officials import (list_active, remove_official, schedule_successor,
                              check_appointments, month_key_str, init_db,
                              find_official)


def main():
    init_db(force=True)  # 测试幂等：重置为开局硬编码名单（临时库）
    act = list_active()
    print("在职官员:", len(act))
    assert len(act) >= 20, "开局应硬编码 20+ 名官员"
    for o in act[:5]:
        print("  %s · %s（%s）%s~%s" % (o["name"], o["title"], o["province"],
                                        o["start_date"], o["end_date"] or "至今"))
        assert o["name"] and o["title"] and o["start_date"]

    # 罢免：袁崇焕
    d = remove_official("袁崇焕", end_date="1627-06")
    print("\n罢免:", d["name"], d["title"])
    assert d and d["status"] == "removed"
    assert not find_official("袁崇焕"), "罢免后不应仍在职"

    # 继任：随机 1-6 月后到任
    succ = schedule_successor(d, "1627-06")
    print("继任者:", succ["name"], succ["title"], "预定", succ["arrive_date"], "就任")
    assert succ["status"] == "pending"
    assert succ["title"] == d["title"]

    # 推进：到期的 pending 就任（构造一个已到期时间）
    n = check_appointments("1627-12")  # 足够晚，应全部就任
    print("本次就任:", n)
    assert n >= 1
    ok = find_official(succ["name"])
    assert ok, "到期继任者应已就任"
    print("继任者已就任 ✓")

    # 未到期的仍 pending
    d2 = remove_official("满桂", end_date="1627-06")
    succ2 = schedule_successor(d2, "1627-06", months_range=(5, 5))  # 固定 5 月后
    n2 = check_appointments("1627-08")
    assert find_official(succ2["name"]) is None or n2 == 0, "未到日期不应就任"
    print("未到期继任者仍未就任 ✓")

    print("\nOFFICIALS OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
