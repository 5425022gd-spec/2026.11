# -*- coding: utf-8 -*-
"""接口契约测试（【v0.3.7】标准化）：前端读到的每一处，后端必须真的给。

做法：从 web/index.html 每个渲染函数体内，找出 `const 别名 = 某全局数据 || {...}` 的绑定，
只在**该函数体内、且别名未被回调参数遮蔽的位置**抠 `别名.a.b` 形式的数据访问，
逐条到后端对应接口核对。这类检查专治"界面有、后端从不返回"
（v0.3.5 修的地方产能 ⓘ 就是这一类）。
"""

import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_data_contract.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_data_contract_pos.json"))
from engine.session import GameSession


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


HTML = io.open("web/index.html", encoding="utf-8").read()
FUNC_RE = re.compile(r"\nfunction\s+\w+\s*\(")


def _dept_payloads(st):
    from engine.depts import build_dept
    out = []
    for n in ["吏部", "户部", "礼部", "兵部", "刑部", "工部", "太仆寺", "内阁", "都察院"]:
        try:
            out.append(build_dept(n, st))
        except Exception:
            pass
    return out


LOADERS = {
    "HUBU_DATA": lambda st: [__import__("engine.depts_hubu", fromlist=["x"]).get_hubu_overview(st)],
    "LI_DATA": lambda st: [__import__("engine.depts_libu", fromlist=["x"]).get_libu_overview(st)],
    "RI_DATA": lambda st: [__import__("engine.depts_rites", fromlist=["x"]).get_rites_overview(st)],
    "XING_DATA": lambda st: [__import__("engine.depts_xingbu", fromlist=["x"]).get_xingbu_overview(st)],
    "WORKS_DATA": lambda st: [__import__("engine.depts_works", fromlist=["x"]).get_works_overview(st)],
    "WAR_DATA": lambda st: [__import__("engine.depts_war", fromlist=["x"]).get_war_overview(st)],
    "TAIPU_DATA": lambda st: [__import__("engine.depts_taipusi", fromlist=["x"]).get_taipusi_overview(st)],
    "DEPT_BASE_DATA": _dept_payloads,
}

GLOBALS = "|".join(LOADERS)
BIND_RE = re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(" + GLOBALS + r")\b")
SHADOW_RES = [
    re.compile(r"\.(?:map|filter|forEach|find|some|every|sort)\(\s*\(?\s*(\w+)\s*(?:[,)]|=>)"),
    re.compile(r"for\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\b"),
    re.compile(r"function\s*\(\s*(\w+)"),
]


def _func_slice(pos: int) -> str:
    """绑定所在的那一段函数体：从上一个 function 到下一个 function。"""
    starts = [m.start() for m in FUNC_RE.finditer(HTML) if m.start() < pos]
    begin = starts[-1] if starts else 0
    after = [m.start() for m in FUNC_RE.finditer(HTML) if m.start() > pos]
    end = after[0] if after else len(HTML)
    return HTML[begin:end]


def collect_paths(alias: str, body: str, global_name: str):
    """在函数体内抠 `别名.a[.b]`（跳过被遮蔽之后的访问与被调用的方法名）。

    遮蔽＝该函数体里另有 `const/let/var 别名 =`（不是本绑定那次）、或别名被用作回调参数。
    """
    out = set()
    access_re = re.compile(r"(?<![\w.])" + re.escape(alias) + r"\.([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?")
    events = []
    for m in access_re.finditer(body):
        events.append((m.start(), "path", m.group(1), m.group(2)))
    redecl = re.compile(r"(?:const|let|var)\s+" + re.escape(alias) + r"\s*=\s*([^\n;]*)")
    for m in redecl.finditer(body):
        if m.group(1).strip().startswith(global_name):
            continue                      # 这一次就是本绑定，不算遮蔽
        events.append((m.start(), "shadow", None, None))
    for rx in SHADOW_RES:
        for m in rx.finditer(body):
            if m.group(1) == alias:
                events.append((m.start(), "shadow", None, None))
    events.sort()
    shadowed = False
    skipped = {"map", "filter", "forEach", "find", "some", "every", "sort", "length", "join",
               "slice", "toFixed", "push", "includes", "trim", "replace", "split",
               "addEventListener", "querySelector", "getElementById"}
    for _, kind, a, b in events:
        if kind == "shadow":
            shadowed = True
            continue
        if shadowed or a in skipped or (b and b in skipped):
            continue
        out.add(a if not b else a + "." + b)
    return out


def resolve(payload, path: str):
    """按路径取值；数组取第一项继续走。"""
    cur = payload
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def test_contract():
    st = GameSession().state
    bindings = [(m.group(1), m.group(2), _func_slice(m.start())) for m in BIND_RE.finditer(HTML)]
    ok(bindings, "要从前端解析出部门数据的别名绑定")
    checked = 0
    problems = []
    payload_cache = {}
    for alias, global_name, body in bindings:
        if global_name not in payload_cache:
            try:
                payload_cache[global_name] = LOADERS[global_name](st)
            except Exception as exc:
                payload_cache[global_name] = []
                problems.append("%s 构造失败：%s" % (global_name, exc))
        payloads = payload_cache[global_name] or []
        if not payloads:
            problems.append("%s 构造结果为空" % global_name)
            continue
        for path in sorted(collect_paths(alias, body, global_name)):
            checked += 1
            if not any(resolve(pl, path) for pl in payloads):
                problems.append("%s（%s）.%s ← 后端一处也没返回" % (alias, global_name, path))
    ok(not problems, "前端读的字段后端必须给，缺 %d 处：\n  %s"
       % (len(problems), "\n  ".join(problems[:25])))
    print("接口契约 ✓ 前端 %d 条字段访问全部能在后端构造结果里取到（部门数据 %d 组绑定）"
          % (checked, len(bindings)))


def test_province_payload_contract():
    """地方详情页读的字段必须都在后端同一份载荷里（server 与测试共用 build_province_view）。"""
    from engine.localdata import build_province_view

    st = GameSession().state
    view = build_province_view(st, "shaanxi")
    ok(view, "载荷函数要能构造出省详情")
    for field in ("corruption", "corruption_calc", "carrying", "base_carrying", "carrying_calc",
                  "controller", "population", "population_exact", "material_production",
                  "material_demand", "supply_demand_ratio", "supply_status", "class_material_stock",
                  "local_market_stock", "tax_total", "support_total", "classes_info", "liaoxiang",
                  "neighbors_distances", "path_to_beijing", "modifiers", "province_armies",
                  "allocated_surcharge_wan", "tax_matter_loss_total", "price_index"):
        ok(field in view, "/api/province 必须返回 %s" % field)
    for field in ("breakdown", "mult_sum", "add_sum", "value", "base", "calculation_text"):
        ok(field in view["corruption_calc"], "corruption_calc 要有 %s" % field)
        ok(field in view["carrying_calc"], "carrying_calc 要有 %s" % field)
    ok(view["classes_info"], "classes_info 不能为空")
    for c_name, info in view["classes_info"].items():
        for field in ("formula_text", "religion_prod_buff", "disaster_penalty", "tax_matter_loss",
                      "net_multiplier", "capacity", "demand", "base_prod", "base_demand"):
            ok(field in info, "classes_info[%s] 要有 %s" % (c_name, field))
    # 各阶级需求之和要与总需求同源（宗教需求修正两边都要算）
    total = sum(info["demand"] for info in view["classes_info"].values())
    ok(abs(total - view["material_demand"]) < 0.51,
       "各阶级需求之和应等于物质总需求：%.2f vs %.2f" % (total, view["material_demand"]))
    print("地方详情契约 ✓ 省份字段、腐败/承载力 breakdown、各阶级明细齐备，各阶级需求之和与总需求同源")


if __name__ == "__main__":
    test_contract()
    test_province_payload_contract()
    print("── 接口契约测试全部通过")
