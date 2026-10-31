# -*- coding: utf-8 -*-
"""地区修正表（地方详情第 6 节）回归测试。

规格（玩家定义）：一条修正 = **修正项目 + 修正范围 + 修正效果 + 介绍**，一条一行，四列。
1. 表里的行只有 item / scope / effect / intro 四个字段，缺一个即报错；
2. **范围＝全国 的条目写进地方表** —— 全国 = **当前明属控制的省份**，
   所以明属控制省有、外邦省份（盛京/海西/嫩科尔沁等）一条都没有；
3. 范围＝中央 的条目不进地方表（中央腐败、威望、替代率在主界面【ⓘ】那一层）；
4. 范围由解析出的 target 判定（operations._SCOPE_OF_TARGET 唯一一处），不给词条写特判；
5. 范围＝别的省 的部门修正只写进那一个省，不往别省复制；
6. 超控增删部门修正／独立地区修正后，表与产能实时同步；
7. 地方工程不进本表（它们在"地方工程修正"那一段，按五类子栏列出）；
8. **文字纪律**：修正项目与介绍里不许出现括号、"合计"、"·"、数学符号、百分号算式；
   修正表模板必须是四列且不许小字号（介绍与别的列同号字）。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_modifier_rows.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_modifier_rows_pos.json"))
from engine.session import GameSession
from engine import modifiers as M
from engine import depts_rites as R
from engine.economy import class_capacity
from engine.depts_libu import ADMIN_DISCIPLINE_BUFFS, get_libu_overview
from engine.depts_xingbu import ACTIVE_LAWS, get_xingbu_overview
from engine.modifiers import merge_modifier_rows
from engine.depts_works import get_province_project_rows
from engine.operations import (create_modifier, delete_modifier, _SCOPE_OF_TARGET)


def rows_of(st, pid="nanzhili"):
    return M.get_province_all_modifiers(st, pid)["modifier_rows"]


def farmer_cap(st, pid="nanzhili"):
    loc = st.provinces[pid]["local"]
    live = M.get_province_local_modifiers(st, pid)
    pop = float((loc.get("classes") or {}).get("农民", {}).get("pop") or 0.0)
    return class_capacity("农民", pop, loc, live)


def main():
    s = GameSession()
    st = s.state
    rows = rows_of(st)

    # ── 1) 四列必有；id 视来源而定（法律条文有 id，行政纪律只有名称）──
    for r in rows:
        assert {"item", "scope", "effect", "intro"} <= set(r.keys()), \
            "修正行必带 item/scope/effect/intro，实际 %s" % r.keys()
        assert set(r.keys()) <= {"id", "item", "scope", "effect", "intro"}, \
            "修正行不得多出别的字段，实际 %s" % r.keys()
    print("修正行字段 item/scope/effect/intro（可选 id），共 %d 行 ✓" % len(rows))

    # ── 2) 中央条目不在地方表内；全国条目在（明属控制省）──
    assert not [r for r in rows if r["scope"] == "中央"], "中央项不得进地区修正表"
    assert _SCOPE_OF_TARGET["central_corruption"] == "中央"
    assert _SCOPE_OF_TARGET["all_local_corruption"] == "全国"
    items = {r["item"] for r in rows}
    for b in ADMIN_DISCIPLINE_BUFFS:
        has_local = (float(b.get("local_corruption_pct") or 0.0) != 0.0
                     or float(b.get("carrying_pct") or 0.0) != 0.0)
        if has_local:
            assert b["name"] in items, "缺全国政纪（明属控制省应写）：%s" % b["name"]
        else:
            assert b["name"] not in items, "只有中央项的政纪不应在表内：%s" % b["name"]
    zheng = [r for r in rows if r["item"] == "党争工具"]
    assert len(zheng) == 1 and zheng[0]["scope"] == "全国" \
        and zheng[0]["effect"] == "地区腐败程度+2%" and zheng[0]["intro"], zheng
    for l in ACTIVE_LAWS:
        got = [r for r in rows if r["item"] == l["name"]]
        assert len(got) == 1 and got[0]["scope"] == "全国", got
    print("明属控制省（南直隶）：全国政纪/法律在表内、中央项不在 ✓（%d 行）" % len(rows))

    # ── 3) 外邦省份：明的全国政纪法律一条都不写；控制权变了立刻跟着变 ──
    for pid in ("shengjing", "haixi", "nenkeerqin", "chahar"):
        f_rows = rows_of(st, pid)
        leaked = [r for r in f_rows if r["scope"] in ("全国", "中央")]
        assert not leaked, "%s 不该有明全国/中央条目：%s" % (pid, leaked)
        assert not ({r["item"] for r in f_rows} & {b["name"] for b in ADMIN_DISCIPLINE_BUFFS}), \
            "%s 混进吏部政纪" % pid
    # 辽东丢掉控制权（核心仍是宣称不动）→ 全国项立刻消失；海西归明控制（核心仍是非核）→ 全国项立刻出现
    from engine import localdata
    st.provinces["liaodong"]["local"]["controller"] = "满清"
    assert not [r for r in rows_of(st, "liaodong") if r["scope"] == "全国"], "控制权已失仍有全国项"
    st.provinces["haixi"]["local"]["controller"] = "明"
    assert [r for r in rows_of(st, "haixi") if r["scope"] == "全国"], "控制权归明仍无全国项"
    assert localdata.under_ming_control(st.provinces["haixi"]), "非核但明控应算全国"
    st.provinces["liaodong"]["local"]["controller"] = "明"
    st.provinces["haixi"]["local"]["controller"] = "满清"
    print("全国＝控制权＝明 ✓（辽东丢控制权即停写、海西非核但明控即照写；与核心无关）")

    # ── 4) 中央表（主界面【ⓘ】）：只收中央项；教育修正也要在这里看得见 ──
    central = M.get_central_modifier_rows(st)
    assert central and all(r["scope"] == "中央" for r in central), central
    edu_items = [r["item"] for r in central if "年增" in r["effect"]]
    assert edu_items == ["教育腐败", "考试僵化", "北京国子监", "南京国子监", "捐纳泛滥"], edu_items
    assert "受教育人口年增" in next(r for r in central if r["item"] == "教育腐败")["effect"]
    assert "受高等教育人口年增" in next(r for r in central if r["item"] == "北京国子监")["effect"]
    print("中央表 %d 行（含教育修正 %d 条）：%s ✓"
          % (len(central), len(edu_items), "、".join(r["item"] for r in central)))

    # ── 4.5) 修正行＝四列：修正项目 / 修正范围 / 修正效果 / 介绍，缺一即报错 ──
    def check_four_cols(rows, where):
        bad = []
        for r in rows:
            for key, label in (("item", "修正项目"), ("scope", "修正范围"),
                               ("effect", "修正效果"), ("intro", "介绍")):
                if not str(r.get(key) or "").strip():
                    bad.append("%s 的【%s】缺 %s：%r" % (r.get("item") or "?", where, label, r))
        assert not bad, "修正行必须四列齐全：\n  " + "\n  ".join(bad[:12])

    check_four_cols(central, "中央修正表")
    prov_rows = M.get_province_all_modifiers(st, "shaanxi").get("modifier_rows") or []
    check_four_cols(prov_rows, "陕西地区修正表")
    check_four_cols(get_province_project_rows(st, "beizhili"), "北直隶工程修正行")
    check_four_cols(get_province_project_rows(st, "shaanxi"), "陕西工程修正行")
    print("修正四列 ✓ 中央 %d 行、陕西 %d 行、工程 %d+%d 行都齐备（修正项目/修正范围/修正效果/介绍）"
          % (len(central), len(prov_rows),
             len(get_province_project_rows(st, "beizhili")),
             len(get_province_project_rows(st, "shaanxi"))))

    # ── 4.6) 文字纪律：修正项目/介绍里不得出现 括号、"合计"、"·"、数学符号与百分号算式 ──
    BANNED_CHARS = "（）()·"
    MATH_CHARS = "^×÷=≈≥≤±"
    MATH_PAT = re.compile(r"[0-9０-９]\s*[%+\-／/]\s*[0-9０-９]|[%％]")

    def check_text(rows, where):
        bad = []
        for r in rows:
            for key, label in (("item", "修正项目"), ("intro", "介绍")):
                t = str(r.get(key) or "")
                why = ["括号 %s" % c for c in BANNED_CHARS if c in t]
                why += ["数学符号 %s" % c for c in MATH_CHARS if c in t]
                if "合计" in t:
                    why.append("字样「合计」")
                if MATH_PAT.search(t):
                    why.append("百分号/算式")
                if why:
                    bad.append("%s 的【%s】= %r → %s" % (where, label, t, "、".join(why)))
        assert not bad, "修正文字违规：\n  " + "\n  ".join(bad[:12])

    all_rows = list(central)
    for pid in ("shaanxi", "nanzhili", "yunnan", "beizhili", "liaodong"):
        all_rows += list(M.get_province_all_modifiers(st, pid).get("modifier_rows") or [])
        all_rows += list(get_province_project_rows(st, pid))
    check_text(all_rows, "修正行文字")
    edu = R.calc_education_summary(st)
    for lvl, ed in (("受教育人口", edu["local_education"]), ("受高等教育人口", edu["higher_education"])):
        for m in ed.get("edu_modifiers") or []:
            check_text([{"item": m.get("name"), "intro": m.get("desc")}], "%s修正" % lvl)
    check_text([{"item": "受教育人口加成", "intro": "读书人多了，全国各地区各阶级的生产力跟着提高"},
                {"item": "结构性失业", "intro": "受高等教育的人太多而无用武之地，挤压生产；触发即刷新结构性失业修正，占比回落就撤去"}],
               "教育加成")
    print("修正文字纪律 ✓ %d 行：修正项目/介绍无括号、无「合计」、无「·」、无数学符号、无百分号算式"
          % len(all_rows))

    # ── 4.7) 界面侧：修正表只有一个来源（modTable4），且不许小字号 ──
    src = io.open("web/index.html", encoding="utf-8").read()
    # 修正表表头只许出现在 modTable4 一处；工程建筑表（worksTable8）是另一张八列表，
    # 它的前四列沿用同一套列名，因此按「连续四列」按序核对：
    # 全文应恰好是 modTable4 的四列，加 worksTable8 的八列（前四列同四列名）。
    heads = re.findall(r"<th[^>]*>(修正项目|修正范围|修正效果|修正介绍|介绍|建筑类型|工程状态|造价 万两|年维护费 万两)</th>", src)
    want = ["修正项目", "修正范围", "修正效果", "介绍",
            "修正项目", "修正范围", "修正效果", "修正介绍",
            "建筑类型", "工程状态", "造价 万两", "年维护费 万两"]
    assert heads == want, \
        "修正表只许由 modTable4 生成、工程表只许由 worksTable8 生成，实际：%s" % heads
    assert "function modTable4(" in src, "缺少统一的修正表函数 modTable4"
    assert "function worksTable8(" in src, "缺少统一的工程建筑表函数 worksTable8"
    for bad in ("修正项目</th><th", ):
        pass
    # 六部面板里不许再自造"修正"列（表头含"修正"的只能是标准列名之一）
    for th in re.findall(r"<th[^>]*>([^<]{0,30})</th>", src):
        if "修正" in th and th not in ("修正项目", "修正范围", "修正效果", "修正介绍"):
            raise AssertionError("六部面板里出现自造的修正列：%r" % th)
    idx = src.find("${esc(r.intro || \"\")}")
    while idx > 0:
        cell = src[max(0, idx - 80):idx]
        assert "font-size" not in cell, "修正表介绍列不许小字号：%r" % cell
        idx = src.find("${esc(r.intro || \"\")}", idx + 1)
    print("修正表模板 ✓ 全文只有 modTable4 一处表头（修正项目/修正范围/修正效果/介绍），无自造修正列、无小字号")

    # ── 4.8) 同一张表里，一个修正项目只许一行：同名即报错 ──
    def check_unique(rows, where):
        seen = {}
        for r in rows:
            it = r.get("item")
            assert it not in seen, \
                "同一张修正表里「%s」出现两行（同一修正项目不许分两行），%s：\n  %s\n  %s" \
                % (it, where, seen[it], r)
            seen[it] = r

    # 合并函数本身：同名合成一行，范围用「、」连接，效果用「；」连接，介绍取第一处非空
    merged_probe = merge_modifier_rows([
        {"item": "探针修正", "scope": "中央", "effect": "腐败程度+20%", "intro": "说明"},
        {"item": "探针修正", "scope": "全国", "effect": "地区腐败程度+2%", "intro": ""},
    ])
    assert len(merged_probe) == 1, merged_probe
    assert merged_probe[0]["scope"] == "中央、全国", merged_probe
    assert merged_probe[0]["effect"] == "腐败程度+20%；地区腐败程度+2%", merged_probe
    assert merged_probe[0]["intro"] == "说明", merged_probe

    check_unique(central, "中央修正表")
    for pid in ("shaanxi", "nanzhili", "yunnan", "beizhili", "liaodong", "shengjing"):
        check_unique(M.get_province_all_modifiers(st, pid).get("modifier_rows") or [],
                     "%s 地区修正表" % pid)
        check_unique(get_province_project_rows(st, pid), "%s 工程修正行" % pid)
    lib_rows = get_libu_overview(st)["discipline_rows"]
    xing_rows = get_xingbu_overview(st)["law_rows"]
    check_unique(lib_rows, "吏部行政纪律")
    check_unique(xing_rows, "刑部现行法律")
    edu_now = R.calc_education_summary(st)
    for lvl, key in (("受教育人口修正", "local_education"), ("受高等教育人口修正", "higher_education")):
        check_unique([{"item": m.get("name")} for m in (edu_now[key].get("edu_modifiers") or [])], lvl)
    # 面板上的同名项目必须已经合成一行（原来 党争工具/吏律实亡/京察瘫痪 各占两行）
    zheng = [r for r in lib_rows if r["item"] == "党争工具"]
    assert len(zheng) == 1 and zheng[0]["scope"] == "中央、全国" and "；" in zheng[0]["effect"], zheng
    jing = [r for r in lib_rows if r["item"] == "京察瘫痪"]
    assert len(jing) == 1 and jing[0]["scope"] == "全国" and "承载力" in jing[0]["effect"], jing
    for l in ACTIVE_LAWS:
        got = [r for r in xing_rows if r["item"] == l["name"]]
        assert len(got) == 1, "刑部面板同一法律占两行：%s" % got
    print("修正表收口 ✓ 同表内同名项目只一行（中央 %d / 陕西 %d / 吏部 %d / 刑部 %d 行）"
          % (len(central), len(prov_rows), len(lib_rows), len(xing_rows)))

    # ── 4.9) 存量数值不得进修正表：受教育人口／受高等教育人口不是修正 ──
    for bad in ('item: "受教育人口"', 'item: "受高等教育人口"'):
        assert bad not in src, "存量数值被写进修正表（修正没有存量数值）：%s" % bad
    assert "今年预计增加" in src, "存量人口要在自己的普通表里给出「现有／今年预计增加」"
    print("存量数值不进修正表 ✓ 受教育人口／受高等教育人口改用普通表（现有／今年预计增加）")


    # ── 5) 地方工程不进本表；工程修正行按五类分好 ──
    proj_names = {pj.get("name") for pj in (st.provinces["nanzhili"]["local"].get("projects") or [])}
    assert not (proj_names & items), "地方工程不得进地区修正表：%s" % (proj_names & items)
    bz = {r["item"]: r for r in get_province_project_rows(st, "beizhili")}
    assert bz["紫禁城"]["subclass_name"] == "国家建筑" and "全国中央所在" in bz["紫禁城"]["effect"], bz["紫禁城"]
    assert bz["居庸关长城"]["subclass_name"] == "军事建筑", bz["居庸关长城"]
    assert bz["天津水田屯田"]["subclass_name"] == "工程建筑", bz["天津水田屯田"]
    sx = {r["item"]: r for r in get_province_project_rows(st, "shaanxi")}
    assert sx["灵州黄河石堤"]["scope"] == "全国" and sx["灵州黄河石堤"]["subclass_name"] == "灾害建筑", sx["灵州黄河石堤"]
    # 灾害效果要出数值（数据在 buff 里），而不是把介绍原样当效果
    assert "减产减轻 20%" in sx["灵州黄河石堤"]["effect"] and "发生机会减少 20%" in sx["灵州黄河石堤"]["effect"], sx["灵州黄河石堤"]
    assert sx["灵州黄河石堤"]["intro"] != sx["灵州黄河石堤"]["effect"], "效果与介绍不得是同一句"
    # 加产能的水利已归「工程建筑」：效果列照样出数值
    assert sx["秦渠长堤"]["subclass_name"] == "工程建筑" and "农民产能+5%" in sx["秦渠长堤"]["effect"], sx["秦渠长堤"]
    assert sx["汉渠芦洞"]["subclass_name"] == "工程建筑", sx["汉渠芦洞"]
    bz_nz = {r["item"]: r for r in get_province_project_rows(st, "nanzhili")}
    for nm in ("通济新河", "泇河运河", "南旺分水枢纽"):
        assert bz_nz[nm]["subclass_name"] == "工程建筑", bz_nz[nm]
    assert bz_nz["通济新河"]["intro"] != "运河，全阶级产能提高，商人也在内", "介绍要写建筑本身的历史"
    qx = {r["item"]: r for r in get_province_project_rows(st, "zhejiang")}
    assert qx["海宁海塘"]["subclass_name"] == "灾害建筑" and "减轻 70%" in qx["海宁海塘"]["effect"], qx["海宁海塘"]
    yn = {r["item"]: r for r in get_province_project_rows(st, "yunnan")}
    assert yn["云南银矿群"]["scope"] == "云南" and yn["云南银矿群"]["effect"] == "白银 28 万两/年", yn["云南银矿群"]
    print("地方工程：北直隶 %s ｜ 陕西灾害建筑 %s ｜ 云南矿场 %s ✓"
          % ({r["subclass_name"] for r in bz.values()},
             [r["item"] for r in sx.values() if r["subclass_name"] == "灾害建筑"],
             [r["item"] for r in yn.values() if r["subclass_name"] == "矿场"]))

    # ── 6) 超控部门修正：本省项进本省表、别省不复制、全国项只进明属控制省 ──
    cap0 = farmer_cap(st)
    ok, _ = create_modifier(st, "户部", "探针部门修正",
                                       "南直隶地区腐败程度-5%，农民产能-3%，中央腐败程度+20%")
    assert ok
    got = [r for r in rows_of(st) if r["item"] == "探针部门修正"]
    assert got and "南直隶" in got[0]["scope"] and "地区腐败程度-5%" in got[0]["effect"], got
    assert not [r for r in rows_of(st, "shaanxi")
                if r["item"] == "探针部门修正" and "南直隶" in r["scope"]], "别省范围的项混进本表"
    cap_cn = [r for r in rows_of(st, "beizhili") if r["item"] == "探针部门修正" and r["scope"] == "全国"]
    assert cap_cn and "农民产能-3.00%" in cap_cn[0]["effect"], cap_cn
    assert not [r for r in rows_of(st, "shengjing") if r["item"] == "探针部门修正"], "全国项写进了外邦省份"
    assert not [r for r in M.get_central_modifier_rows(st)
                if r["item"] == "探针部门修正" and r["scope"] == "全国"], "全国项混进中央表"
    cap1 = farmer_cap(st)
    assert cap1 < cap0, "部门修正未实时计入产能：%.2f → %.2f" % (cap0, cap1)
    delete_modifier(st, "探针部门修正")
    assert "探针部门修正" not in {r["item"] for r in rows_of(st)}
    assert abs(farmer_cap(st) - cap0) < 0.01, "删除后产能未复原"
    print("部门修正：本省表 %s ｜ 全国项进 %s ｜ 产能 %.2f → %.2f → %.2f ✓"
          % (got[0]["effect"], cap_cn[0]["effect"], cap0, cap1, farmer_cap(st)))

    # ── 7) 独立地区修正：范围=本省，效果逐阶级，实时计入产能 ──
    ok, _ = create_modifier(st, "南直隶", "探针地区修正", "全国所有阶级产能-5.00%", duration_months=6)
    assert ok
    got = [r for r in rows_of(st) if r["item"] == "探针地区修正"]
    assert got and got[0]["scope"] == "南直隶", got
    assert "农民产能-5.00%" in got[0]["effect"] and "，" in got[0]["effect"], got
    cap2 = farmer_cap(st)
    assert cap2 < cap0, "独立地区修正未实时计入产能"
    delete_modifier(st, "探针地区修正")
    assert "探针地区修正" not in {r["item"] for r in rows_of(st)}
    assert abs(farmer_cap(st) - cap0) < 0.01
    print("独立地区修正：南直隶 ｜ %s ｜ 产能 %.2f → %.2f → %.2f ✓"
          % (got[0]["effect"][:28] + "…", cap0, cap2, farmer_cap(st)))

    print("\nMODIFIER ROWS OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
