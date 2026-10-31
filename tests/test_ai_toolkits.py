# -*- coding: utf-8 -*-
"""各套 AI 工具与工作流测试：战争裁判、会议纪要、人物档案、司礼监中旨。

口径（本项目所有者定义）：
- 提示词里只放提示词与数据变量；固定的返回字段收进交卷工具的参数与说明；
- 该多步的（取规则 → 试跑 → 交卷）走 engine/ai_workflow.run_tool_workflow；
- 参数不合规要报错并写明怎么传；引擎已知的事实由引擎写入，不靠提示词自律；
- 认不出来的指令必须如实失败，不许当成功。
"""

import io
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_ai_toolkits.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_ai_toolkits_pos.json"))
from engine.session import GameSession
from engine import battle_tools as bt
from engine import minute_tools as mt
from engine import person_tools as pt
from engine import zhongzhi_tools as zt
from engine.operations import execute_operation_command, execute_single_command


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _params_closed(node, is_root=True):
    """嵌套 object 必须显式声明 openness（同 DSH：根开放、嵌套收口）。"""
    if isinstance(node, dict):
        if node.get("type") == "object" and not is_root:
            ok(node.get("additionalProperties") is False, "嵌套 object 未收口：%s" % node)
        for v in (node.get("properties") or {}).values():
            _params_closed(v, False)
        if node.get("items") is not None:
            _params_closed(node["items"], False)


def test_tool_kits_shape():
    for mod in (bt, mt, pt, zt):
        payload = mod.tools_payload()
        ok(payload, "%s 要有工具" % mod.__name__)
        for t in payload:
            fn = t["function"]
            ok(set(fn) == {"name", "description", "parameters"},
               "发给模型的只有三个字段，实际 %s" % sorted(fn))
            ok(len(fn["description"]) > 40, "%s 说明太短" % fn["name"])
            _params_closed(fn["parameters"])
    ok("finish_battle" in bt.TOOL_INDEX and "finish_minutes" in mt.TOOL_INDEX
       and "finish_person_card" in pt.TOOL_INDEX and "finish_command" in zt.TOOL_INDEX,
       "四套交卷工具都要在")
    ok([t["name"] for t in zt.TOOLS] == ["get_operation_rules", "try_operation", "finish_command"],
       [t["name"] for t in zt.TOOLS])
    print("各套工具定义 ✓ 只发三个字段、嵌套收口、交卷通道齐备")


def test_prompts_only_data_vars():
    expect = {
        "war.txt": {"military", "enemy forces", "architecture", "nature", "timestamp",
                    "local_weather", "local_classes", "history_12"},
        "meeting.txt": {"topic", "conversation", "members"},
        "meeting_2.txt": {"topic", "conversation", "members"},
        "create_person.txt": {"char_name", "char_title", "char_location", "char_faction"},
        "silijian_ai.txt": {"player_command"},
        "zhixian.txt": {"timestamp", "count"},
    }
    for f, allowed in expect.items():
        text = io.open("Prompt/" + f, encoding="utf-8").read()
        used = set(re.findall(r"\{\{(\w[\w ]*)\}\}", text))
        ok(used <= allowed, "%s 里出现了非数据变量：%s" % (f, used - allowed))
        ok("JSONL" not in text, "%s 里还留着 JSONL 返回要求" % f)
    print("提示词纪律 ✓ 六套提示词只剩数据变量，返回规范都在工具说明里")


def test_battle():
    s = GameSession()
    from engine.armies import load_armies_data
    names = [u["name"] for u in load_armies_data()["units"]][:2]
    args = {"battle_process": "两军相遇，鏖战半日。",
            "units": [{"unit_name": names[0], "deaths": 80, "elite_degradation": "+3",
                       "equipment_loss": [{"equipment": "白蜡杆长矛", "count": 10}]},
                      {"unit_name": names[1], "deaths": 30, "elite_degradation": "-2",
                       "equipment_loss": []}]}
    rep = bt.finish_report(args, s.state)
    ok(rep["timestamp"], "时间戳由引擎补")
    ok(rep["units"][0]["equipment_loss"] == [{"equipment": "白蜡杆长矛", "count": 10}],
       rep["units"][0]["equipment_loss"])
    # 部队名对不上要报错，不许静默不改
    try:
        bt.finish_report(dict(args, units=[{"unit_name": "查无此军", "deaths": 1,
                                            "elite_degradation": "0", "equipment_loss": []}]), s.state)
        raise AssertionError("查无此军应报错")
    except bt.ToolArgsError as e:
        ok("名册里找不到" in str(e), str(e))
    # 交卷参数字段缺失要报错并指路
    try:
        bt.finish_report({"units": []}, s.state)
        raise AssertionError("缺 battle_process 应报错")
    except bt.ToolArgsError as e:
        ok("不要再包一层" in str(e), str(e))
    print("战争裁判 ✓ 战报字段收进工具、部队名校验、时间戳由引擎补")


def test_minutes():
    s = GameSession()
    from engine.characters import list_callable
    s.council.start(None, [c["id"] for c in list_callable()[:2]])
    s.council.player_speak("此事当如何处置？")
    names = s.council.roster_names()
    value = mt.finish_minutes({"entries": [
        {"character": names[0], "topic": "测试", "summary": "主张缓办"},
        {"character": names[1], "topic": "测试", "summary": "主张速办"}]}, s.state, s)
    ok(len(value["entries"]) == 2 and value["entries"][0]["timestamp"], value["entries"])
    ok(not value["summary"].strip().startswith("{"), "归档正文要是人话，不是 JSONL")
    # 名单外的人要退回
    try:
        mt.finish_minutes({"entries": [{"character": "查无此人", "topic": "x", "summary": "y"}]},
                          s.state, s)
        raise AssertionError("名单外的人应退回")
    except mt.ToolArgsError as e:
        ok("不在本次参会名单" in str(e), str(e))
    # 空数组要退回（不是"成功归档 0 条"）
    try:
        mt.finish_minutes({"entries": []}, s.state, s)
        raise AssertionError("空纪要应退回")
    except mt.ToolArgsError as e:
        ok("至少一条" in str(e), str(e))
    # 走一遍出口：mock 下退回模板归纳，且不把 JSONL 存进 summary
    okk, summary = s.council.end()
    ok(okk and summary, summary)
    ok(not summary.strip().startswith("{"), "归档摘要不该是 JSONL")
    print("会议纪要 ✓ 名字与条数由引擎校验、摘要人话化、空纪要退回")


def test_person_card():
    s = GameSession()
    args = {"basic": {"字": "文远", "生年": "万历十八年", "籍贯": "南直隶苏州府昆山县"},
            "state": {"状态": "在任"},
            "性格": [{"标题": "刚方严毅", "述": "遇事敢言。"}],
            "生平": [{"年": "万历四十七年", "事": "登进士第。"},
                     {"年": "天启二年", "事": "擢御史。"},
                     {"年": "天启七年", "事": "升授今职。"}]}
    card = pt.finish_card(args, "张三", "户部郎中", "北直隶", "阉党", s.state, s)
    ok(card["basic"]["姓名"] == "张三" and card["basic"]["阵营"] == "阉党"
       and card["state"]["驻地"] == "北直隶", card)
    ok(card["state"]["现职"] == "户部郎中", card["state"])
    try:
        pt.finish_card(dict(args, 性格=args["性格"] * 5), "张三", "x", "y", "z", s.state, s)
        raise AssertionError("性格超 4 条应退回")
    except pt.ToolArgsError as e:
        ok("最多" in str(e), str(e))
    try:
        pt.finish_card(dict(args, 生平=args["生平"][:2]), "张三", "x", "y", "z", s.state, s)
        raise AssertionError("生平少于 3 条应退回")
    except pt.ToolArgsError as e:
        ok("至少" in str(e), str(e))
    env = pt.finish_card({"arguments": json.dumps(args, ensure_ascii=False)},
                         "张三", "户部郎中", "北直隶", "阉党", s.state, s)
    ok(env["basic"]["字"] == "文远", "信封要剥一层")
    print("人物档案 ✓ 引擎写入已知事实、条数上下限、信封剥离")


def test_zhongzhi():
    s = GameSession()
    st = s.state
    txt, val = zt.dispatch(st, s, "try_operation", {"command_line": "调拨地方窖藏到国库 陕西 5.0"})
    ok(val["ok"] is True and val["dry_run"] is True, val)
    txt2, val2 = zt.dispatch(st, s, "try_operation", {"command_line": "乱写一条不存在的指令 甲乙丙"})
    ok(val2["ok"] is False, "认不出来的指令试跑必须说执行不了：%s" % val2)
    ok("执行不了" in txt2, txt2[:60])
    txt3, val3 = zt.dispatch(st, s, "get_operation_rules", {})
    ok(len(val3["text"]) > 500, "指令全集要能取到")
    ok("command_line" in zt.finish_command(
        {"command_line": "调拨地方窖藏到国库 陕西 5.0"}, st, s), "交卷要收下")
    for bad in ({}, {"command_line": ""}):
        try:
            zt.finish_command(bad, st, s)
            raise AssertionError("空交卷应退回：%s" % bad)
        except Exception:
            pass
    # 底层：未知指令如实失败（原来的实现把它当成功空操作）
    okk, msg = execute_operation_command(st, "乱写一条不存在的指令 甲乙丙")
    ok(okk is False and "无法识别" in msg, (okk, msg))
    ok(execute_single_command(st, "乱写一条不存在的指令 甲乙丙") is None, "认不出来要返回 None")

    class _Fake:
        mock = False

        def __init__(self):
            self.round = 0

        def chat_once(self, messages, tools=None, think=False):
            self.round += 1
            if self.round == 1:
                return "", "", [
                    {"id": "c1", "type": "function",
                     "function": {"name": "try_operation",
                                  "arguments": json.dumps({"command_line": "调拨地方窖藏到国库 陕西 5.0"})}}]
            return "", "", [{"id": "c2", "type": "function",
                             "function": {"name": "finish_command",
                                          "arguments": json.dumps({"command_line": "调拨地方窖藏到国库 陕西 5.0"})}}]

    res = zt.run_zhongzhi_workflow(st, _Fake(), "口谕：着陕西解银五万两入太仓。", session=s)
    ok(res["command_line"] == "调拨地方窖藏到国库 陕西 5.0", res)
    ok([e.get("name") for e in res["log"] if e.get("kind") == "tool"]
       == ["try_operation", "finish_command"], res["log"])
    print("司礼监中旨 ✓ 取规则、快照试跑、交卷；未知指令如实失败")


if __name__ == "__main__":
    test_tool_kits_shape()
    test_prompts_only_data_vars()
    test_battle()
    test_minutes()
    test_person_card()
    test_zhongzhi()
    print("── 各套 AI 工具与工作流测试全部通过")
