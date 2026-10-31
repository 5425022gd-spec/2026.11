"""修正模型测试（v0.5.5 口径）：

1. **唯一公式**：值 = 基础值 × (1 + Σ乘法修正) + Σ加法修正。
   玩家给的样例必须算对：1 × (1 + 1% + 2% + 3%) + 1 + 2 + 3 = 7.06。
2. **修正数据化**：静态修正只在 world/modifiers.json 一处；
   两个部门列表（行政纪律、现行法律）由它投影而来，代码里不再写死。
3. **修正挂哪与影响谁无关**：一个修正可同时打多个 target（如 党争工具 同时打中央与地方腐败）。
4. **每个被影响的系统都给出贡献明细**（breakdown），面板据此列出「谁加了多少」。
5. **建筑是特殊修正**：工程数据在 world/provinces.json 的 buff 里，不进 modifiers.json。
"""

import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_modifier_model.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_modifier_model_pos.json"))
from engine.session import GameSession
from engine import modifier_registry as mr
from engine.depts_libu import calc_central_corruption, ADMIN_DISCIPLINE_BUFFS
from engine.depts_xingbu import ACTIVE_LAWS
from engine.depts_rites import calc_prestige


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_formula():
    ok(abs(mr.apply_formula(1.0, [0.01, 0.02, 0.03], [1, 2, 3]) - 7.06) < 1e-9,
       "玩家样例应等于 7.06，实际 %r" % mr.apply_formula(1.0, [0.01, 0.02, 0.03], [1, 2, 3]))
    ok(mr.apply_formula(50.0, [0.65], []) == 82.5, "中央腐败样例")
    ok(mr.apply_formula(60.0, [], []) == 60.0, "没有修正时就是基础值")
    # 全站只有这一个公式实现：引擎里不许再出现同款算式
    hits = []
    for root, _dirs, files in os.walk("engine"):
        for fn in files:
            if not fn.endswith(".py") or fn == "modifier_registry.py":
                continue
            text = io.open(os.path.join(root, fn), encoding="utf-8").read()
            if "(1.0 + pct_sum) + flat_sum" in text or "(1.0 + rel_prestige_pct" in text:
                hits.append(fn)
    ok(not hits, "这些文件还自己写了一遍公式：%s" % hits)
    print("唯一公式 ✓ 样例 7.06、中央腐败 82.5，引擎里无第二处同款算式")


def test_modifier_data():
    data = json.load(io.open("world/modifiers.json", encoding="utf-8"))
    mods = data["modifiers"]
    ok(len(mods) >= 6, "静态修正应有 6 条以上，实际 %d" % len(mods))
    for m in mods:
        ok(m.get("name") and m.get("intro") and m.get("effects"),
           "修正 %s 要齐名称/介绍/效果" % m.get("id"))
        for e in m["effects"]:
            ok(e["target"] in mr.TARGETS, "target 未登记：%s" % e["target"])
            ok(e["op"] in mr.OPS, "op 只能是乘法/加法，实际 %s" % e["op"])

    # 部门列表由 JSON 投影而来（数值与旧口径一致）
    discipline = {b["id"]: b for b in ADMIN_DISCIPLINE_BUFFS}
    ok(discipline["dangzheng"]["central_corruption_pct"] == 0.2, discipline["dangzheng"])
    ok(discipline["dangzheng"]["local_corruption_pct"] == 0.02, discipline["dangzheng"])
    ok(discipline["jingcha_dead"]["carrying_pct"] == -0.1, discipline["jingcha_dead"])
    ok(discipline["dangzheng"]["central_desc"] == "+20%", discipline["dangzheng"])
    laws = {l["id"]: l for l in ACTIVE_LAWS}
    ok(laws["daming_code"]["central_corruption_pct"] == 0.15, laws["daming_code"])
    ok(laws["daming_code"]["category"] == "根本祖制律典", laws["daming_code"])
    ok(laws["daming_code"]["effect_desc"], "法律要带效果说明")
    print("修正数据化 ✓ %d 条静态修正只在 JSON 一处，两个部门列表由它投影" % len(mods))


def test_one_modifier_many_targets():
    """一个修正可以同时打多个系统，挂在哪不影响它影响谁。"""
    dang = [m for m in mr.load_static() if m["id"] == "dangzheng"][0]
    targets = {e["target"] for e in dang["effects"]}
    ok(targets == {"central_corruption", "local_corruption"},
       "党争工具应同时打中央与地方腐败，实际 %s" % targets)
    s = GameSession()
    for t in targets:
        names = [c["name"] for c in mr.contributions(s.state, t)]
        ok("党争工具" in names, "%s 的贡献里应有党争工具" % t)
    print("挂哪与影响谁无关 ✓ 党争工具同时进中央腐败与地方腐败两处")


def test_breakdown_per_system():
    """每个被影响的系统都给贡献明细（谁、什么运算、多少）。"""
    s = GameSession()
    cc = calc_central_corruption(s.state)
    ok(cc["value"] == 82.5, "中央腐败应是 82.5，实际 %s" % cc["value"])
    got = {b["name"]: b for b in cc["breakdown"]}
    for must in ("党争工具", "风闻言事", "吏律实亡", "《大明律》", "《问刑条例》"):
        ok(must in got, "中央腐败明细缺 %s" % must)
    ok(cc["mult_sum"] == 0.65, "百分比合计应是 0.65，实际 %s" % cc["mult_sum"])

    r = mr.value(s.state, "prestige", 60.0)
    ok(abs(r["value"] - 66.9) < 1e-6, "威望应是 66.9，实际 %s" % r["value"])
    ok(any(b["name"] == "宗教影响" for b in r["breakdown"]), "威望明细要有宗教影响")
    ok(r["formula"] == mr.FORMULA_TEXT, "明细要带公式口径")

    # 超控设立的修正也进同一份明细
    s.state.department_modifiers["t1"] = {
        "id": "t1", "name": "大萧条", "dept": "礼部",
        "effects": [{"target": "prestige", "is_pct": True, "delta": -20, "desc": "萧条"}]}
    p2 = calc_prestige(s.state)
    ok(abs(p2["value"] - 54.9) < 1e-6, "加大萧条后威望应 54.9，实际 %s" % p2["value"])
    ok(any(b["name"] == "大萧条" for b in p2["breakdown"]), "明细要列出大萧条")
    print("贡献明细 ✓ 中央腐败 5 项、威望含宗教与超控项")


def test_capacity_goes_through_registry():
    """产能类修正也走登记处：宗教/科技/教育/超控/地方独立修正都在 contributions 里，
    建筑（工程）不在其中；加法类产能修正要真进产能。"""
    from engine.modifiers import get_province_local_modifiers
    from engine.economy import class_capacity
    s = GameSession()
    names = [c["name"] for c in mr.contributions(s.state, "capacity", "shaanxi")]
    ok(any("宗教" in n for n in names), "产能贡献里应有宗教影响：%s" % names)
    proj_names = [n for n in names if "塘" in n or "堤" in n or "运河" in n]
    ok(not proj_names, "建筑不该出现在产能贡献里：%s" % proj_names)

    pop = 819.9995
    loc = s.state.provinces["shaanxi"]["local"]
    before = class_capacity("农民", pop, loc, get_province_local_modifiers(s.state, "shaanxi"))
    s.state.department_modifiers["add1"] = {
        "id": "add1", "name": "垦荒令", "dept": "户部",
        "effects": [{"target": "capacity", "is_pct": False, "delta": 5.0,
                     "classes": ["农民"], "desc": "开垦荒田"}]}
    after = class_capacity("农民", pop, loc, get_province_local_modifiers(s.state, "shaanxi"))
    ok(abs((after - before) - 5.0) < 1e-6,
       "加法类产能修正要真加 5，实际 %s -> %s" % (before, after))

    # 地方独立修正（主线在地方刷的限时修正）也进产能贡献
    loc.setdefault("custom_modifiers", []).append(
        {"name": "西北大旱", "effect_pct": -0.05, "affected_classes": ["农民"],
         "note": "主线旱情"})
    got = [c for c in mr.contributions(s.state, "capacity", "shaanxi")
           if c["name"] == "西北大旱"]
    ok(got and got[0]["classes"] == ["农民"], "地方独立修正要带阶级：%s" % got)
    print("产能走登记处 ✓ 宗教/超控/地方修正齐备，建筑不在其中，加法项生效")


def test_religion_prestige_not_in_province():
    """宗教影响威望只出现在皇室威望的数值下面，不进各省地区修正表。"""
    from engine.modifiers import get_province_all_modifiers
    s = GameSession()
    rows = [r["item"] for r in get_province_all_modifiers(s.state, "shaanxi")["modifier_rows"]
            if "宗教" in r["item"]]
    ok("宗教影响威望" not in rows, "省表里不该有宗教影响威望：%s" % rows)
    ok(not any("宗教影响" in r for r in rows), "省表里不该再有宗教专属行：%s" % rows)
    pres = calc_prestige(s.state)
    ok(any("宗教" in b["name"] for b in pres["breakdown"]),
       "威望明细里该有宗教影响：%s" % pres["breakdown"])
    print("宗教显示位置 ✓ 威望只在威望面板，省表只留产能与需求")


def test_buildings_are_separate():
    """建筑（工程）是特殊修正，数据不进 modifiers.json。"""
    proj_names = set()
    world = json.load(io.open("world/provinces.json", encoding="utf-8"))
    provinces = world["provinces"]
    rows = provinces.values() if isinstance(provinces, dict) else provinces
    for p in rows:
        for pj in ((p.get("local") or {}).get("projects") or []):
            proj_names.add(pj["name"])
    mod_names = {m["name"] for m in mr.load_static()}
    ok(proj_names, "world/provinces.json 里应能取到工程名")
    ok(not (proj_names & mod_names), "工程名不该出现在静态修正表：%s" % (proj_names & mod_names))
    print("建筑单独处理 ✓ %d 个工程不进静态修正表，数据仍在 world/provinces.json" % len(proj_names))


if __name__ == "__main__":
    test_formula()
    test_modifier_data()
    test_one_modifier_many_targets()
    test_breakdown_per_system()
    test_capacity_goes_through_registry()
    test_religion_prestige_not_in_province()
    test_buildings_are_separate()
    print("── 修正模型测试全部通过")
