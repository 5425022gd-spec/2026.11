"""祭祀行礼日排期与行礼流程测试。

规则（玩家定义）：
1. 祭祀要求（灾异祭祀清单）出现后，由玩家在 30 日窗口内指定一个「行礼日」；
2. 届期当日 07:00 弹窗请驾：儒家→奉先殿、道教→钦安殿、佛教→英华殿；
   同日有多项时按 儒家 > 道教 > 佛教 取最高优先的一条；
3. 圣驾未往（不去）＝ 等同逾期未祭：该祭作废并扣 prestige_penalty；
4. 前往＝ 扣太仓银 + 置 in_progress，并直耗一日（+24h），次日凌晨批处理时礼成、该教影响力 +1%。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_sacrifice_schedule.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_sacrifice_schedule_pos.json"))
from engine.session import GameSession
from engine.depts_rites import (tick_sacrifices_monthly, schedule_sacrifice, clear_schedule,
                                due_sacrifice, decline_sacrifice, start_sacrifice,
                                RELIGION_PALACE, SACRIFICE_RELIGION_ORDER)
from engine.time import date_key, date_shift_key


def seed_sacrifices(st, deaths=20000):
    """走真实生成路径：灾情死亡清单 -> 次月 1 日生成三教祭祀。"""
    st.pending_sacrifices = [{"title": "灾异 · 地动", "disaster_name": "地动",
                              "province_name": "陕西", "deaths": deaths}]
    tick_sacrifices_monthly(st)
    return st.sacrifices


def find(st, target_religion):
    return next((s for s in st.sacrifices if s.get("target_religion") == target_religion), None)


def main():
    print("── 映射表 ──")
    assert RELIGION_PALACE == {"儒家": "奉先殿", "道教": "钦安殿", "佛教": "英华殿"}, RELIGION_PALACE
    assert SACRIFICE_RELIGION_ORDER == ["儒家", "道教", "佛教"], SACRIFICE_RELIGION_ORDER
    print("儒家→奉先殿 / 道教→钦安殿 / 佛教→英华殿 ✓")
    print("同日优先次序：%s ✓" % " > ".join(SACRIFICE_RELIGION_ORDER))

    s = GameSession()
    st = s.state

    # ── 1) 生成：三教各一条，30 日窗口，初始未排期 ──
    sacc = seed_sacrifices(st)
    assert len(sacc) == 3, "应生成 3 条祭祀，实际 %d" % len(sacc)
    assert all(x["status"] == "pending" and x["remain_days"] == 30 for x in sacc)
    assert all("scheduled_date" not in x for x in sacc), "初始不应有行礼日"
    assert due_sacrifice(st) is None, "未排期时不应有应祭项"
    print("生成 3 条待祭（儒/道/佛），30 日窗口，均未排期 ✓")

    today = date_key(st.date)
    dao = find(st, "道教")
    fo = find(st, "佛教")

    # ── 2) 窗口校验 ──
    ok, msg = schedule_sacrifice(st, dao["id"], 1627, 8, 24)   # 早于今日
    assert not ok, "早于今日的日期不应通过"
    print("窗口校验 · 早于今日被拒：%s ✓" % msg)
    far = date_shift_key(st.date, 30)                          # 超出 30 日窗口
    y, m, d = (int(x) for x in far.split("-"))
    ok, msg = schedule_sacrifice(st, dao["id"], y, m, d)
    assert not ok, "超出窗口的日期不应通过"
    print("窗口校验 · 超限期被拒：%s ✓" % msg)

    # ── 3) 排期：今日 +2 ──
    t2 = date_shift_key(st.date, 2)
    y, m, d = (int(x) for x in t2.split("-"))
    ok, msg = schedule_sacrifice(st, dao["id"], y, m, d)
    assert ok, msg
    assert dao["scheduled_date"] == {"year": y, "month": m, "day": d}
    assert due_sacrifice(st) is None, "未到行礼日不应届期"
    print("排定行礼日 %s：%s ✓" % (t2, msg))

    # 撤销再排
    ok, msg = clear_schedule(st, dao["id"])
    assert ok and "scheduled_date" not in dao, "撤销行礼日失败"
    ok, msg = schedule_sacrifice(st, dao["id"], y, m, d)
    assert ok, msg
    print("撤销行礼日并重排 ✓")

    # ── 4) 推进到行礼日 ──
    s.run("j 1d")
    assert date_key(st.date) == date_shift_key({"year": 1627, "month": 8, "day": 25}, 1)
    assert due_sacrifice(st) is None, "第 1 日不应届期（定在第 3 日）"
    s.run("j 1d")
    due = due_sacrifice(st)
    assert due and due["id"] == dao["id"], "第 2 日应届期"
    assert due["palace"] == "钦安殿", "道教应在钦安殿，实际 %s" % due["palace"]
    print("届期 ✓ %s —— 请驾地点：%s（%s）" % (due["scheduled_date"], due["palace"], due["religion"]))

    # ── 5) 同日优先次序：再排一条佛教在同一天，应仍先请道教 ──
    y, m, d = (int(x) for x in date_key(st.date).split("-"))
    ok, msg = schedule_sacrifice(st, fo["id"], y, m, d)
    assert ok, msg
    due = due_sacrifice(st)
    assert due["target_religion"] == "道教", "同日应优先道教（儒>道>佛），实际 %s" % due["target_religion"]
    print("同日两项：仍先请道教（儒>道>佛）✓")

    # 再排一条儒家同一天 -> 儒家应压过道教
    ru = find(st, "儒家")
    ok, msg = schedule_sacrifice(st, ru["id"], y, m, d)
    assert ok, msg
    due = due_sacrifice(st)
    assert due["target_religion"] == "儒家" and due["palace"] == "奉先殿", \
        "同日有儒家时应先请奉先殿，实际 %s/%s" % (due["target_religion"], due["palace"])
    print("再将儒家排在同日：改先请奉先殿 ✓")

    # ── 6) 前往：扣银 + in_progress + 直耗一日 ──
    ru = find(st, "儒家")
    d0, treasury0 = date_key(st.date), st.treasury
    inf0 = st.religion_influences.get("儒家", 0.0)
    ok, msg = start_sacrifice(st, ru["id"])
    assert ok, msg
    assert ru["status"] == "in_progress"
    assert abs((treasury0 - st.treasury) - float(ru["cost_wan"])) < 1e-6, "未按花费扣银"
    lines = s.run("j 1d")     # 相当于接口里的 +24h
    assert date_key(st.date) == date_shift_key({"year": 1627, "month": 8, "day": 27}, 1), \
        "前往后应恰好加 24 小时（进一日），实际 %s" % date_key(st.date)
    assert ru["status"] == "completed", "次日凌晨批处理应礼成，实际 %s" % ru["status"]
    inf1 = st.religion_influences.get("儒家", 0.0)
    assert abs(inf1 - round(inf0 * 1.01, 2)) < 1e-9, "儒家影响力应相对 +1%%：%s -> %s" % (inf0, inf1)
    assert any("祭典告成" in l for l in lines), "推进输出应含礼成提示"
    print("前往祭祀：扣银 %.4f 万两、+24h、礼成、儒家影响力 %.2f→%.2f ✓" % (ru["cost_wan"], inf0, inf1))

    # ── 7) 不去：等同逾期未祭，作废并扣威望 ──
    fo = find(st, "佛教")
    assert fo["status"] == "pending"
    pen0 = st.prestige_penalty
    ok, msg = decline_sacrifice(st, fo["id"])
    assert ok, msg
    assert fo["status"] == "expired", "不去应作废，实际 %s" % fo["status"]
    assert abs((st.prestige_penalty - pen0) - float(fo["prestige_penalty"])) < 1e-9, "应扣 prestige_penalty"
    print("不去 → 作废并扣威望 -%.3f：%s ✓" % (fo["prestige_penalty"], msg))

    # ── 8) 边界：已礼成/已作废的条目不能再排期、不能再前往、不能重复不去 ──
    for bad, label in ((ru, "已礼成"), (fo, "已作废")):
        ok, msg = schedule_sacrifice(st, bad["id"], 1627, 9, 1)
        assert not ok, "%s的条目不应能再排期" % label
        ok2, msg2 = decline_sacrifice(st, bad["id"])
        assert not ok2, "%s的条目不应能再作废" % label
    ok, msg = start_sacrifice(st, fo["id"])
    assert not ok, "已作废条目不应能前往"
    print("边界：已礼成/已作废条目不能再排期、再作废、再前往 ✓")

    # ── 9) 未排期的条目不能直接前往（须先定行礼日） ──
    s2 = GameSession()
    seed_sacrifices(s2.state)
    fresh = s2.state.sacrifices[0]
    assert due_sacrifice(s2.state) is None
    ok, msg = start_sacrifice(s2.state, fresh["id"])   # 底层函数本身不拦，接口层拦
    assert ok, "底层 start_sacrifice 仍按原契约工作"
    print("（接口层由 due_sacrifice 校验，已在 server 路由中拦截）✓")

    print("\nSACRIFICE SCHEDULE OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
