"""章奏系统测试：三级来源生成、标签、已阅/回折、推进出新章奏。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_memorials.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_memorials_pos.json"))
from engine.session import GameSession


def main():
    s = GameSession()
    mems = s.list_memorials()
    print("初始章奏数:", len(mems))
    assert mems, "开局应有章奏"
    for m in mems:
        assert m["source"] == "filler"
        print("  %s %s（%s）" % (m["id"], m["title"], m["Location"]))

    # 推进后：凌晨批处理生成不重要（n-）+ 次要（m-）事件；主要事件（l-）暂未制作
    s.run("j 1m")
    mems2 = s.list_memorials()
    srcs2 = set(m["source"] for m in mems2)
    print("推进后来源:", srcs2)
    assert "filler" in srcs2
    assert any(m["id"].startswith("m-") for m in mems2), "次要事件 ID 应为 m- 前缀"
    assert len(s.memorial_history) == 0  # 推进不自动归档

    # 已阅（不再从目录消失：打上标记并置底）
    first = s.list_memorials()[0]
    n_before = len(s.list_memorials())
    ok, msg = s.memorial_act(first["id"], "read")
    print("已阅:", ok, msg)
    assert ok
    assert len(s.list_memorials()) == n_before, "已阅章奏应保留在目录中（置底），不消失"
    assert first["status"] == "read"
    # 未处理章奏仍在目录前部
    assert s.list_memorials()[0]["status"] in (None, "new")

    # 批注（打字已阅）：只作记录，打上已批标记并置底（章奏回复 AI 已删除）
    second = s.list_memorials()[0]
    ok, msg = s.memorial_act(second["id"], "annotate", "着所司速议具奏。")
    print("批注:", ok, msg)
    assert ok
    assert second["status"] == "annotated"
    # 已处理章奏全部置底（在目录后部），未处理章奏保持在前
    lst = s.list_memorials()
    done_ids = [m["id"] for m in lst if m.get("status") and m["status"] != "new"]
    pending_ids = [m["id"] for m in lst if not (m.get("status") and m["status"] != "new")]
    assert second["id"] in done_ids and first["id"] in done_ids
    assert not set(done_ids) & set(pending_ids)
    assert max(lst.index(m) for m in lst if m["id"] in done_ids) > \
           max((lst.index(m) for m in lst if m["id"] in pending_ids), default=-1)

    # 回折
    third = s.list_memorials()[0]
    ok, msg = s.memorial_act(third["id"], "reply", "卿所奏甚明，著所司议处。")
    print("回折:", ok, msg)
    assert ok
    assert third["status"] == "replied"

    # 不存在的章奏
    ok, _ = s.memorial_act("m-9999", "read")
    assert not ok

    # 时间推进（时/日/组合）：凌晨批处理会先结算并清出已阅/已批/归档章奏再补新批，
    # 目录数量不单调（见 engine/session.py 凌晨批处理），故只校验推进可用与日期格式
    lines = s.run("j 1h")
    print("推进1时:", lines[0])
    lines = s.run("j 5d")
    print("推进5日:", lines[0], "目录章奏数:", len(s.list_memorials()))
    lines = s.run("j 1m 2d 3h")
    print("组合推进:", lines[0])
    # 日期包含 日/时辰
    assert "日" in lines[0] and "时" in lines[0]

    print("\nMEMORIALS OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
