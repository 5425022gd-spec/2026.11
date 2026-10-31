"""凌晨批处理测试：每天凌晨 0 点三部分逻辑。

规则（玩家定义）：
1. 每天凌晨 0 点：从不重要事件模板库随机抽取 10-20 个不重要事件（n 层）；
2. 凌晨 0 点：支线生成随机 1-5 个次要事件（m 层），生成后间隔 1-5 天再生成；
   例外：开局首日（简化历 08-26 / 公历 1627-10-04）不出支线，首次支线日为 08-27；
3. 每月一号凌晨 0 点：月度总结（Prompt/zongjie.txt，留接口）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_dawn.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_dawn_pos.json"))
from engine.session import GameSession
from engine.time import date_key, date_shift_key


def count_layer(mems, prefix):
    return sum(1 for m in mems if m["id"].startswith(prefix))


def main():
    s = GameSession()
    assert date_key(s.state.date) == "1627-08-25", "开局应为天启七年八月二十五日"
    assert s.state.date["hour"] == 7, "开局时刻应为 07:00"
    assert s.dawn_state["last"] == "1627-08-25", "开局日不执行凌晨批处理"
    assert s.dawn_state["next_side"] == "1627-08-27", "首日（08-26）不出支线"
    n0 = len(s.list_memorials())
    assert n0 == 4, "开局 4 份不重要事件"

    # ── 1) 当天推进数小时（不跨日）：不触发凌晨批处理 ──
    lines = s.run("j 3h")
    assert len(s.list_memorials()) == n0, "未跨日不应触发凌晨批处理"
    print("当日推进 3 时：不触发凌晨批处理 ✓")

    # ── 2) 跨日（凌晨 0 点）：抽 10-20 个不重要事件；开局首日不出支线 ──
    # 用 id 集合求差：凌晨会先把未处理的旧章奏归档出列，
    # 直接数长度会把"归档"误判成"未生成"。
    before_ids = {m["id"] for m in s.list_memorials()}
    lines = s.run("j 1d")
    dawn_lines = [l for l in lines if "通政司" in l or "支线" in l or "月度" in l or "事件树" in l]
    print("跨日推进输出:", dawn_lines)
    mems = s.list_memorials()
    new_ids = {m["id"] for m in mems} - before_ids
    new_n = len([i for i in new_ids if i.startswith("n-")])
    assert 10 <= new_n <= 20, "应抽取 10-20 个不重要事件，实际 %d" % new_n
    assert s.dawn_state["last"] == date_key(s.state.date)
    assert not [i for i in new_ids if i.startswith("m-")], \
        "首日（简化历 08-26）不应生成次要事件（支线）"
    assert s.dawn_state["next_side"] == "1627-08-27", "下次支线日应仍为 08-27"
    print("不重要事件抽取: %d 份 ✓  首日不出支线 ✓  下次支线: %s"
          % (new_n, s.dawn_state["next_side"]))

    # ── 3) 连续推进到每月一号：月度总结（zongjie.txt 缺失时提示）──
    # 从 8月26日 推到 9月1日（5 天），day==1 触发月度总结
    lines3 = s.run("j 5d")
    assert date_key(s.state.date) == "1627-09-01", "应到九月一日"
    mems2 = s.list_memorials()
    assert count_layer(mems2, "m-") > count_layer(mems, "m-"), "跨日应生成次要事件"
    # 月度总结提示（zongjie.txt 缺失时返回提示）应出现在 9月1日 推进输出中
    assert any("月度总结" in l for l in lines3), "每月一号凌晨应执行月度总结"
    print("月度总结输出:", [l for l in lines3 if "月度总结" in l][0])

    # ── 4) 支线间隔：下次支线日之前不生成次要事件 ──
    next_side = s.dawn_state["next_side"]
    if next_side and next_side > date_key(s.state.date):
        m_before = {m["id"] for m in s.list_memorials() if m["id"].startswith("m-")}
        s.run("j 1d")  # 推进一天（若未到 next_side 则不生成支线）
        m_after = {m["id"] for m in s.list_memorials() if m["id"].startswith("m-")}
        if next_side > date_key(s.state.date):
            assert not (m_after - m_before), "未到支线日不应生成次要事件"
            print("支线间隔生效：%s 前不生成 ✓" % next_side)
        else:
            print("推进后到达支线日，正常生成次要事件")

    # ── 5) 每日仅一次：跨日执行一次后，同日再次推进不重复 ──
    s.run("j 1d")   # 跨到新的一天 0 时：执行凌晨
    x = date_key(s.state.date)
    assert s.dawn_state["last"] == x
    s.run("j 1h")   # 同日 1 时：不重复
    assert s.dawn_state["last"] == x, "同一天不应重复凌晨批处理"
    print("每日凌晨批处理仅一次（%s）✓" % x)

    print("\nDAWN OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
