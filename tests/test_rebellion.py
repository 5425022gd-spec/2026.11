"""阶级叛乱测试（v0.3.6）：装备池卡规范、点数折算、抽卡、人数分配、人口实扣、装备复制、日级判定。"""

import json
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_rebellion.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_rebellion_pos.json"))
from engine.session import GameSession
from engine import rebellion as RB
import engine.armies as A


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def reset_roster():
    """军队名册是内存名册（save_armies_data 不落盘，磁盘上恒为开局名单），重置即可回到开局。"""
    A._RUNTIME_ARMIES = None
    return A.load_armies_data()


def _check_roster(unit_id, tag):
    units = A.load_armies_data().get("units") or []
    ok(any(x["id"] == unit_id for x in units), "%s：叛军要进军队名册" % tag)


def test_card_pool():
    """卡里只有三样：人数、点数、装备清单；其余一概不写。"""
    for kind in ("民变", "私兵"):
        cards = RB.pool_cards(kind)
        ok(cards, "%s池不能为空" % kind)
        for c in cards:
            ok(set(c.keys()) <= {"id", "men", "points", "equipment"},
               "%s 卡只能有人数/点数/装备清单（id 仅作引用）：多写了 %s"
               % (kind, set(c.keys()) - {"id", "men", "points", "equipment"}))
            ok(float(c.get("men") or 0) > 0, "卡要有明确人数")
            ok(float(c.get("points") or 0) > 0, "卡要有明确点数")
            eq = c.get("equipment") or {}
            ok(eq.get("melee"), "卡里至少要有近战械")
            for slot, items in eq.items():
                for it in items:
                    ok(set(it.keys()) <= {"name", "count"}, "装备只写名目与数量：%s" % it)
                    nm = it.get("name") or ""
                    ok(not any(k in nm for k in ("年式", "万历", "嘉靖", "天启", "永乐", "隆庆")),
                       "叛乱装备不得写年代制式：%s" % nm)
    liang = [float(c["men"]) / float(c["points"]) for c in RB.pool_cards("民变")]
    ok(max(liang) >= 1000, "要有人多装备差的卡（每点千人以上）：%.0f" % max(liang))
    ok(min(liang) <= 500, "要有人少且精锐的卡（每点五百人以下）：%.0f" % min(liang))
    print("卡规范 ✓ 两池共 %d 张卡，只有人数/点数/装备，档次从每点 %.0f 人拉到 %.0f 人"
          % (len(RB.pool_cards("民变")) + len(RB.pool_cards("私兵")), max(liang), min(liang)))


def test_points_and_draw():
    s = GameSession()
    st = s.state
    loc = st.provinces["huguang"]["local"]
    pts = RB.rebellion_points(st, loc, "农民")
    ok(3.0 <= pts <= 6.0, "湖广农民点数应在 3~6 点：%.2f" % pts)
    mens = []
    for seed in range(12):
        drawn = RB.draw_cards(RB.pool_cards("民变"), pts, random.Random(seed))
        mens.append(sum(d["men"] for d in drawn))
        ok(mens[-1] > 0, "抽卡要有人")
    avg = sum(mens) / len(mens)
    ok(1800 <= avg <= 6000, "湖广农民抽卡平均应在三五千量级：平均 %.0f 人（前五个样本 %s）"
       % (avg, [int(m) for m in mens[:5]]))
    cheap = min(RB.pool_cards("民变"), key=lambda c: RB.card_points(c))
    half = RB.scale_card(cheap, 0.5)
    ok(abs(half["men"] - RB.card_men(cheap) * 0.5) < 1.0, "残批人数按比例")
    ok(abs(half["points"] - RB.card_points(cheap) * 0.5) < 1e-9, "残批点数按比例")
    print("点数与抽卡 ✓ 湖广农民 %.2f 点，12 次抽卡平均 %.0f 人（三五千量级）" % (pts, avg))


def test_split_by_attitude():
    classes = {
        "农民": {"attitude": 0, "pop_exact": 1000000},
        "百工": {"attitude": 40, "pop_exact": 100000},
        "百役": {"attitude": 60, "pop_exact": 80000},
        "流民": {"attitude": 20, "pop_exact": 50000},
    }
    alloc = RB.split_men_by_attitude(classes, 4000, "农民")
    ok(sum(alloc.values()) == 4000, "分配人数要等于总人数：%s" % alloc)
    ok(alloc["农民"] == max(alloc.values()), "起事阶级名额 ×2，应出人最多：%s" % alloc)
    ok(alloc["百役"] < alloc["百工"] < alloc["农民"], "态度越低出人越多：%s" % alloc)
    print("分配 ✓ 按 (100−态度) 比值、起事阶级 ×2：%s" % alloc)


def test_raise_rebellion():
    reset_roster()
    s = GameSession()
    st = s.state
    pid, cls = "huguang", "农民"
    classes = st.provinces[pid]["local"]["classes"]
    for c in RB.LEVY_CLASSES:
        classes[c]["attitude"] = 0 if c == cls else 50
    before = {c: RB._class_pop_exact(classes[c]) for c in RB.LEVY_CLASSES}
    res = RB.raise_rebellion(st, pid, cls, random.Random(7))
    ok(res["ok"], "应能成军：%s" % res)
    u = res["unit"]
    ok(u["name"] == "湖广农民叛军", "队名统一 xxx叛军：%s" % u["name"])
    ok(u["owner"] == "叛军", "归属要是叛军")
    ok(u["count"] == res["men"] and u["count"] > 0, "人数要对：%s" % u["count"])
    ok(1 <= u["elite"] <= 25, "民变精锐度 1~25 随机：%s" % u["elite"])
    ok(u["equipment"].get("melee"), "要有装备")
    for slot, items in u["equipment"].items():
        key = "grade" if slot == "horses" else "era_model"
        for it in items:
            ok(key in it, "%s 槽要用 %s 键：%s" % (slot, key, it))
    after = {c: RB._class_pop_exact(classes[c]) for c in RB.LEVY_CLASSES}
    deducted = {c: before[c] - after[c] for c in RB.LEVY_CLASSES}
    ok(sum(deducted.values()) == res["men"], "人口实扣总数应等于成军人数：%s" % deducted)
    for c in RB.LEVY_CLASSES:
        if res["alloc"].get(c):
            ok(deducted[c] == res["alloc"][c], "%s 扣人与分配一致：%s" % (c, deducted))
    _check_roster(u["id"], "民变")
    print("民变 ✓ %s（%d 人，精锐 %d，抽卡 %s）｜扣人 %s"
          % (u["name"], u["count"], u["elite"], res["cards"], deducted))


def test_retinue_copies_garrison():
    reset_roster()
    s = GameSession()
    st = s.state
    pid = "shaanxi"
    g_elite = RB._garrison_elite(st, pid)
    res = RB.raise_rebellion(st, pid, "宗室", random.Random(5))
    ok(res["ok"], "私兵应能成军：%s" % res)
    u = res["unit"]
    ok(u["name"] == "陕西宗室叛军", "队名统一：%s" % u["name"])
    ok(0.70 <= (res["copied_share"] or 0) <= 0.80, "宗室复制驻军装备 70%%~80%%：%s" % res["copied_share"])
    ok(u["elite"] == g_elite, "私兵精锐度随驻军：%s vs %s" % (u["elite"], g_elite))
    ok(res["alloc"] == {"宗室": res["men"]}, "私兵只从本阶级出人：%s" % res["alloc"])
    r2 = RB.raise_rebellion(st, pid, "商人", random.Random(6))
    ok(0.10 <= (r2["copied_share"] or 0) <= 0.30, "商人 10%%~30%%：%s" % r2["copied_share"])
    r3 = RB.raise_rebellion(st, pid, "士绅", random.Random(6))
    ok(0.20 <= (r3["copied_share"] or 0) <= 0.40, "士绅 20%%~40%%：%s" % r3["copied_share"])
    ok(RB.pool_kind_of("商人") == RB.pool_kind_of("士绅") == RB.pool_kind_of("宗室") == "私兵",
       "商人士绅宗室共抽私兵池")
    ok(RB.pool_kind_of("农民") == RB.pool_kind_of("流民") == "民变", "民变四阶级共抽民变池")
    print("私兵 ✓ 宗室 %.0f%%／商人 %.0f%%／士绅 %.0f%% 复制驻军装备，精锐随驻军 %d，三阶级同抽一池"
          % (res["copied_share"] * 100, r2["copied_share"] * 100, r3["copied_share"] * 100, u["elite"]))


def test_daily_trigger():
    reset_roster()
    s = GameSession()
    st = s.state
    for p in st.provinces.values():
        for c_data in ((p.get("local") or {}).get("classes") or {}).values():
            c_data["attitude"] = 80.0
    notes = RB.tick_rebellion_daily(st, random.Random(3))
    ok(not notes, "态度不为 0 时不得起事：%s" % notes)

    class AlwaysFire(random.Random):
        def random(self):
            return 0.0

    st.provinces["huguang"]["local"]["classes"]["农民"]["attitude"] = 0.0
    notes = RB.tick_rebellion_daily(st, AlwaysFire())
    ok(len(notes) == 1 and "起事" in notes[0], "态度归零后应起事：%s" % notes)

    class NeverFire(random.Random):
        def random(self):
            return 0.5

    st.provinces["huguang"]["local"]["classes"]["农民"]["attitude"] = 0.0
    ok(not RB.tick_rebellion_daily(st, NeverFire()), "日概率 1%%：0.5 不应触发")
    ok(RB.REBEL_CHANCE_DAILY == 0.01, "日概率就是 1%")
    print("日级判定 ✓ 态度=0 且 1%% 命中才起事：" + notes[0])


if __name__ == "__main__":
    test_card_pool()
    test_points_and_draw()
    test_split_by_attitude()
    test_raise_rebellion()
    test_retinue_copies_garrison()
    test_daily_trigger()
    print("── 阶级叛乱测试全部通过")
