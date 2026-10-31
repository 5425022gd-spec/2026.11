"""工程六个属性与造价机制的测试：名称/修正/范围/建筑类型/状态/造价，年维护费＝造价×1%，月工费＝造价÷工期。"""

import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_works_projects.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_works_projects_pos.json"))
from engine.session import GameSession
from engine.depts_works import (get_works_overview, get_province_project_rows,
                                annual_maintenance_wan, monthly_maintenance_wan, project_class)
from engine.operations import create_project, delete_project, tick_construction_monthly

ATTRS = {"name", "buff", "scope", "kind", "status", "cost"}


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def all_projects():
    with open("world/provinces.json", encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for p in data["provinces"]:
        for pj in ((p.get("local") or {}).get("projects") or []):
            out.append((p.get("name"), pj))
    return out


def test_six_attributes():
    projs = all_projects()
    ok(len(projs) == 37, "工程 37 条，实际 %d" % len(projs))
    for pn, pj in projs:
        ok(set(pj.keys()) == ATTRS, "%s的【%s】属性应为 %s，实际 %s" % (pn, pj.get("name"), ATTRS, set(pj.keys())))
        ok(float(pj["cost"]) > 0, "%s的【%s】要有造价" % (pn, pj["name"]))
        ok(pj["scope"] in ("本省", "全国"), "范围只能是本省/全国")
        ok(pj["status"] in ("运营中", "未完工"), "状态只能是运营中/未完工")
        ok("category" not in pj and "note" not in pj and "annual_silver" not in pj,
           "旧的 category/note/annual_silver 不该还在")
    ok(not any("工期" in k for _, pj in projs for k in pj), "工期不是工程的常驻属性")
    # 全国范围的那一条
    scopes = {pj["name"]: pj["scope"] for _, pj in projs}
    ok(scopes["灵州黄河石堤"] == "全国", "灵州黄河石堤范围写全国")
    print("六属性 ✓ 37 条工程都是 名称/修正/范围/建筑类型/状态/造价，造价齐备")


def test_maintenance():
    projs = {pj["name"]: pj for _, pj in all_projects()}
    zj = projs["紫禁城"]
    ok(abs(annual_maintenance_wan(zj) - 595.7 * 0.01) < 1e-9, "年维护费 = 造价 × 1%")
    ok(abs(monthly_maintenance_wan(zj) - round(595.7 * 0.01 / 12, 4)) < 1e-9, "月维护 = 年维护 ÷ 12")
    # 旧的按名字猜月费的机制应已删除
    import engine.depts_works as W
    src = io.open(W.__file__, encoding="utf-8").read()
    ok("三大殿" not in src and "def get_maintenance_cost" not in src, "旧的维护费估算机制要删干净")

    ov = get_works_overview(GameSession().state)
    for p in ov["engineering"]["projects"]:
        ok(abs(p["maintenance_annual"] - float(p["cost"]) * 0.01) < 1e-9,
           "%s 的年维护费应为造价的 1%%" % p["item"])
    ok("waterworks" not in ov, "水利管理表已并入工程建设表（灾害建筑）")
    print("维护费 ✓ 年维护费＝造价×1%%、月维护＝÷12，旧的估算机制已删")


def test_construction_flow():
    s = GameSession()
    st = s.state
    okk, msg = create_project(st, "陕西", "榆林新边墙", kind="军事建筑", desc="遮蔽延绥东路", cost=18.0, build_months=12)
    ok(okk, "新建工程：" + msg)
    pj = next(p for p in st.provinces["shaanxi"]["local"]["projects"] if p["name"] == "榆林新边墙")
    ok(set(pj.keys()) == ATTRS, "新建工程也要六属性")
    ok(pj["status"] == "未完工", "有工期的是未完工")
    item = next(c for c in st.under_construction_projects if c["name"] == "榆林新边墙")
    ok(item["total_months"] == 12 and "monthly_cost" not in item, "工期是临时属性，月款由造价÷工期算")

    t0 = st.treasury
    tick_construction_monthly(st)
    ok(abs((t0 - st.treasury) - 18.0 / 12) < 0.02, "月度扣款 = 造价 ÷ 工期 = 1.5 万两")
    for _ in range(11):
        tick_construction_monthly(st)
    pj = next(p for p in st.provinces["shaanxi"]["local"]["projects"] if p["name"] == "榆林新边墙")
    ok(pj["status"] == "运营中", "工期归零转运营中")
    ok(not st.under_construction_projects, "竣工后不再留在建造清单")

    okk, msg = delete_project(st, "陕西", "榆林新边墙")
    ok(okk, "删除工程：" + msg)
    print("营建 ✓ 新建六属性+工期临时、月款＝造价÷工期、竣工转运营中")


def test_mine_income_and_rows():
    s = GameSession()
    st = s.state
    from engine.economy import tick_money_monthly
    ys = next(p for p in st.provinces["yunnan"]["local"]["projects"] if p["name"] == "云南银矿群")
    ann = float(ys["buff"]["silver"])

    ys["status"] = "运营中"
    t0 = st.treasury
    tick_money_monthly(st)
    with_mine = round(st.treasury - t0, 4)

    ys["status"] = "未完工"
    t0 = st.treasury
    tick_money_monthly(st)
    without_mine = round(st.treasury - t0, 4)

    ok(abs((with_mine - without_mine) - ann / 12.0) < 0.01,
       "只有运营中的才产银：差 %.4f，应为 %.4f" % (with_mine - without_mine, ann / 12.0))
    ys["status"] = "运营中"

    rows = get_province_project_rows(st, "shaanxi")
    ling = next(r for r in rows if r["item"] == "灵州黄河石堤")
    ok(ling["scope"] == "全国", "石堤的修正行范围写全国")
    ok(project_class({"kind": "矿场"}) == "矿场" and project_class({"kind": "军事建筑"}) == "军事建筑",
       "建筑类型即分类")
    ok(project_class({"kind": "银矿"}) == "矿场", "旧细分名也要能兜底归到五类")
    print("修正行与矿税 ✓ 范围按数据、分类由建筑类型推导、只有运营中的矿产银")


def test_five_commands():
    """五条新建指令都要把六属性写全（军事建筑无产能修正，只写介绍）。"""
    from engine.operations import execute_operation_command
    s = GameSession()
    st = s.state
    cmds = [
        ("新建军事建筑 陕西 榆林新边墙 遮蔽延绥东路 18 12", "榆林新边墙", "军事建筑"),
        ("新建国家建筑 南直隶 南京外城 全国所有阶级产能+3% 增筑外郭 200 24", "南京外城", "国家建筑"),
        ("新建工程建筑 北直隶 天津水田新垦 农民产能+10% 开田三千亩 8 6", "天津水田新垦", "工程建筑"),
        ("新建灾害建筑 河南 开封新石堤 全国所有阶级产能+5% 御黄汛护城 16 8", "开封新石堤", "灾害建筑"),
        ("新建矿场 云南 个旧锡矿 白银6万两/年 官办锡场 30 10", "个旧锡矿", "矿场"),
    ]
    def find(nm):
        for pid in st.provinces:
            for p in (st.provinces[pid].get("local") or {}).get("projects") or []:
                if p["name"] == nm:
                    return p
        return None

    for cmd, nm, kind in cmds:
        okk, msg = execute_operation_command(st, cmd)
        ok(okk, "指令应可执行：%s（%s）" % (cmd, msg))
        pj = find(nm)
        ok(pj is not None, "新建的 %s 应出现在省份工程里" % nm)
        ok(set(pj.keys()) == ATTRS, "%s 也是六属性" % nm)
        ok(pj["kind"] == kind, "%s 建筑类型应为 %s" % (nm, kind))
        ok(float(pj["cost"]) > 0 and pj["status"] == "未完工", "有工期 → 未完工，且带造价")
        ok(pj["buff"].get("desc"), "%s 要落介绍" % nm)
    kj = find("个旧锡矿")
    ok(abs(kj["buff"]["silver"] - 6.0) < 1e-9, "矿场的『白银6万两/年』要落进修正")
    ttx = find("天津水田新垦")
    ok(abs(ttx["buff"]["pct"]["农民"] - 0.1) < 1e-9, "『农民产能+10%』要落进修正")
    print("五条新建指令 ✓ 六个属性写全，修正与介绍都落库，工期只作临时")


if __name__ == "__main__":
    test_six_attributes()
    test_maintenance()
    test_construction_flow()
    test_mine_income_and_rows()
    test_five_commands()
    print("── 工程测试全部通过")
