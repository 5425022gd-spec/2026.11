"""国家 AI 专用超控指令测试（Prompt/diguo_ai.txt 的 {{operation_ai}}）。

五类指令：宣战 / 求和 / 贸易 / 军队调动 / 修改军队战役指令。
重点覆盖**各种军队的调用**：满清八旗、蒙古盟军、地方部落兵/牛录、明属机动与镇军，
并跨新地图（盛京／海西／嫩科尔沁／辽东／察哈尔等）验证调动可行与规则边界。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_ai_operations.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_ai_operations_pos.json"))
from engine.session import GameSession
from engine.operations import (execute_ai_operation, execute_ai_operations,
                               get_ai_operations_schema, AI_OPERATIONS_DOC)
from engine.armies import load_armies_data, get_army_by_id, tick_armies_daily
from engine import depts_rites
from engine.time import date_key

FAILS = []


def ok(cond, label, extra=""):
    print(("PASS  " if cond else "FAIL  ") + label + (("  -> " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(label)


def army(aid):
    """从军册（world/armies.json 运行时数据）取某支部队（一份名册，按 id 过滤）。"""
    d = load_armies_data()
    return next((u for u in (d.get("units") or []) if u.get("id") == aid), {})


def main():
    s = GameSession()
    st = s.state

    # ── 0) 清单硬编码存在，且与设置界面/提示词口径一致 ──
    for kw in ("宣战", "求和", "贸易", "军队调动", "修改军队战役指令"):
        ok(kw in AI_OPERATIONS_DOC, "清单含「%s」" % kw)
    ok(AI_OPERATIONS_DOC == get_ai_operations_schema(), "清单函数与常量一致")
    ok("决战／巡逻／守备" in AI_OPERATIONS_DOC, "清单写明任务指令取值")

    # ── 1) 宣战 / 求和 / 贸易 ──
    def rel(name):
        c = next((x for x in depts_rites.DIPLOMATIC_COUNTRIES if x["name"] == name), None)
        return (c or {}).get("relation"), (c or {}).get("trade_status")

    ok(execute_ai_operation(st, "宣战 朝鲜")[0], "宣战 朝鲜 执行成功")
    ok(rel("朝鲜") == ("战争", "禁贸"), "宣战后朝鲜为战争/禁贸", rel("朝鲜"))
    ok(execute_ai_operation(st, "求和 朝鲜")[0], "求和 朝鲜 执行成功")
    ok(rel("朝鲜") == ("和平", "禁贸"), "求和后朝鲜为和平/禁贸", rel("朝鲜"))
    ok(execute_ai_operation(st, "贸易 朝鲜")[0], "贸易 朝鲜 执行成功")
    ok(rel("朝鲜") == ("和平", "贸易"), "贸易后朝鲜为和平/贸易", rel("朝鲜"))
    ok(execute_ai_operation(st, "宣战 满清")[0], "宣战 满清 执行成功")
    ok(not execute_ai_operation(st, "宣战 不存在的国")[0], "对不存在的国家宣战被拒")

    # ── 2) 军队调动：满清八旗 盛京 → 辽东（满清 AI 下令，只能调本国部队）──
    ok(army("mobile_baqi")["current_location"] == "shengjing", "满清八旗开局驻盛京")
    okc, msg = execute_ai_operation(st, "军队调动 满清八旗 辽东")
    ok(not okc, "明方通道不得调他国部队", msg)
    ok(execute_ai_operation(st, "军队调动 满清八旗 辽东", faction="满清")[0], "满清八旗 调往辽东 成功")
    baqi = army("mobile_baqi")
    ok(baqi["target_location"] == "liaodong", "目标为辽东", baqi.get("target_location"))
    ok(baqi["status"] in ("行军中", "候旨开拔"), "已进入行军态", baqi.get("status"))
    ok(float(baqi.get("total_distance_km") or 0) > 0, "算了行军距离", baqi.get("total_distance_km"))
    ok(float(baqi.get("total_days") or 0) > 0, "算了行军天数", baqi.get("total_days"))
    path_names = baqi.get("path_names") or []
    ok(path_names[:1] == ["盛京"], "路径自盛京起", path_names)
    ok(path_names[-1:] == ["辽东"], "路径至辽东终", path_names)
    print("      满清八旗路径:", " → ".join(path_names), "| 里程 %.0f km | %.1f 日"
          % (baqi.get("total_distance_km", 0), baqi.get("total_days", 0)))

    # 推进到抵达（引擎的「令箭送达」与「行军推进」各占一次每日推进，故多推几次）
    days = int(float(baqi.get("total_days") or 1)) + 2
    for _ in range(4):
        tick_armies_daily(st, days)
    baqi = army("mobile_baqi")
    ok(baqi["current_location"] == "liaodong", "推进 %d 日后已进驻辽东" % days, baqi.get("current_location"))
    ok(baqi["status"] == "驻防", "抵达后转为驻防", baqi.get("status"))

    # ── 3) 军队调动：蒙古盟军 嫩科尔沁 → 海西（跨新省）+ 各新省可达性 ──
    ok(execute_ai_operation(st, "军队调动 蒙古盟军 海西", faction="满清")[0], "蒙古盟军 调往海西 成功")
    mg = army("mobile_mongol_alliance")
    ok(mg["target_location"] == "haixi" and float(mg.get("total_days") or 0) > 0,
       "蒙古盟军已定海西行程", (mg.get("target_location"), mg.get("total_days")))

    print("      各新省调动可达性：")
    for prov in ("盛京", "海西", "嫩科尔沁", "兀良哈", "察哈尔", "土默特", "鄂尔多斯",
                 "索伦", "东海", "苦兀", "辽东", "北直隶"):
        okc, msg = execute_ai_operation(st, "军队调动 满清八旗 %s" % prov, faction="满清")
        ok(okc, "可调往 %s" % prov, msg)

    # ── 4) 军队调动的边界 ──
    ok(not execute_ai_operation(st, "军队调动 满清八旗 不存在的省", faction="满清")[0], "未找到目标省份被拒")
    ok(not execute_ai_operation(st, "军队调动 不存在的军队 辽东", faction="满清")[0], "未找到军队被拒")
    # 地方镇军（部落兵/牛录）不可跨省调动
    okc, msg = execute_ai_operation(st, "军队调动 盛京地区部落兵 辽东", faction="满清")
    ok(not okc, "地方镇军不可跨省调动", msg)
    print("      镇军调动被拒原因:", msg)

    # ── 5) 修改军队战役指令：四个项目 ──
    cases = [
        ("修改军队战役指令 满清八旗 任务指令 决战", "mission", "决战"),
        ("修改军队战役指令 满清八旗 任务指令 巡逻", "mission", "巡逻"),
        ("修改军队战役指令 满清八旗 任务指令 守备", "mission", "守备"),
        ("修改军队战役指令 满清八旗 交战风险 逢敌必战", "engagement_risk", "逢敌必战"),
        ("修改军队战役指令 满清八旗 交战风险 避战", "engagement_risk", "避战"),
        ("修改军队战役指令 满清八旗 撤退优先级 从不撤退", "retreat_priority", "从不撤退"),
        ("修改军队战役指令 满清八旗 撤退优先级 高撤退优先级", "retreat_priority", "高撤退优先级"),
        ("修改军队战役指令 满清八旗 战役指令 寻机决战", "battle_order", "寻机决战"),
    ]
    for cmd, field, expect in cases:
        okc, msg = execute_ai_operation(st, cmd, faction="满清")
        v = army("mobile_baqi").get(field)
        ok(okc and v == expect, "%s → %s" % (cmd.replace("修改军队战役指令 满清八旗 ", ""), expect), (okc, v))

    # 兼容说法：交战倾向 / 撤退倾向 / 任务
    for field, val in (("交战倾向", "中风险交战"), ("撤退倾向", "低撤退优先级"), ("任务", "守备")):
        okc, msg = execute_ai_operation(st, "修改军队战役指令 满清八旗 %s %s" % (field, val), faction="满清")
        ok(okc, "兼容项目名「%s」" % field, msg)

    # ── 6) 修改指令的边界 ──
    bad = [
        ("修改军队战役指令 满清八旗 任务指令 撤退", "非法取值"),
        ("修改军队战役指令 满清八旗 交战风险 中等风险", "非法风险值"),
        ("修改军队战役指令 满清八旗 撤退优先级 随便撤", "非法撤退值"),
        ("修改军队战役指令 满清八旗 士气 高涨", "未知项目"),
        ("修改军队战役指令 不存在的军队 任务指令 决战", "未知军队"),
    ]
    for cmd, label in bad:
        okc, msg = execute_ai_operation(st, cmd, faction="满清")
        ok(not okc, "%s 被拒" % label, msg)

    # 对蒙古盟军、明属机动部队、地方镇军同样可改（各由本国下令）
    for aid, nm, fac in (("mobile_mongol_alliance", "蒙古盟军", "满清"),
                         ("mobile_baigan", "白杆兵", "明"),
                         ("gar_shengjing", "盛京地区部落兵", "满清")):
        okc, msg = execute_ai_operation(st, "修改军队战役指令 %s 任务指令 守备" % nm, faction=fac)
        ok(okc, "可修改 %s 的战役指令" % nm, msg)

    # ── 7) 乱指令 / 空指令不崩 ──
    for cmd in ("", "   ", "随便写点什么", "军队调动", "军队调动 满清八旗", "宣战", "贸易 朝鲜 多余参数"):
        okc, msg = execute_ai_operation(st, cmd)
        ok(not okc, "非法规格被拒：%r" % cmd, msg)

    # ── 8) 批量执行返回逐条明细 ──
    logs = execute_ai_operations(st, ["修改军队战役指令 满清八旗 任务指令 决战",
                                      "军队调动 满清八旗 盛京",
                                      "打酱油"], faction="满清")
    ok(len(logs) == 3 and logs[0].startswith("✓") and logs[2].startswith("✗"),
       "批量执行逐条给明细", logs)

    print()
    print("=== 全部通过 ===" if not FAILS else "=== 失败 %d 项：%s ===" % (len(FAILS), "；".join(FAILS)))
    return 1 if FAILS else 0


def test_main():
    rc = main()
    if rc:
        raise SystemExit(rc)


if __name__ == "__main__":
    raise SystemExit(main())
