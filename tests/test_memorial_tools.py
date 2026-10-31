# -*- coding: utf-8 -*-
"""章奏生成（次要事件 m 层）工具与工作流测试。

覆盖本项目所有者定的口径：
1. 提示词里只放提示词与数据变量；变化清单、超控范围、奖惩规则、返回字段规范都在工具里；
2. 交卷工具 finish_memorial 的字段与标签枚举是唯一来源（提示词、工具说明、校验共用）；
3. 参数不合规要报错并写明该怎么传；参数被裹成信封时剥一层；
4. 工作流多轮往返（取清单 → 查范围 → 查规则 → 交卷），跑到交卷为止；
5. generate_side 走工作流产出章奏；没交卷时退回 mock，不编造假章奏。
"""

import io
import json
import os
import re
import sys

# 控制台是 GBK 时，测试里的对勾等字符会编码失败；统一按 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_memorial_tools.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_memorial_tools_pos.json"))
from engine.session import GameSession
from engine import memorial_tools as mt
from engine import memorial_workflow as mw
from engine.memorials import build_side_prompt, generate_side
from engine.variations import record_variation


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _finish_args(vid="var-001"):
    return {
        "variation_id": vid,
        "title": "题本 · 奏报延安府亢旱异常疏",
        "category_label": "题本",
        "content_label": "灾异奏报",
        "location": "陕西延安府",
        "full_text": "臣陕西巡抚谨题，为奏报本季亢旱特甚以恤民隐事。" * 8,
        "draft_processed": "据奏延安灾情深重，著该抚按亲勘，酌议蠲赈。",
        "draft_unprocessed": "灾情尚需核实，原疏暂留候查。",
        "reward_processed": "发仓平粜：缓和民怨，耗支库储",
        "reward_unprocessed": "催科如故：库银无损，民情浮动",
    }


class _FakeLLM:
    """一轮调三个取数工具，下一轮交卷。"""

    mock = False

    def __init__(self, finish_vid="var-001", bad_first=False):
        self.round = 0
        self.finish_vid = finish_vid
        self.bad_first = bad_first

    def chat_once(self, messages, tools=None, think=False):
        self.round += 1
        if self.round == 1:
            if self.bad_first:
                # 参数被截断：不是合法 JSON，应如实报错而不是当成空参数
                return "", "", [{"id": "c0", "type": "function",
                                 "function": {"name": "finish_memorial",
                                              "arguments": '{"variation_id": "var-001", "title": "半'}}]
            return "", "", [
                {"id": "c1", "type": "function",
                 "function": {"name": "list_variations", "arguments": "{}"}},
                {"id": "c2", "type": "function",
                 "function": {"name": "get_variation_scope",
                              "arguments": json.dumps({"variation_id": self.finish_vid})}},
                {"id": "c3", "type": "function",
                 "function": {"name": "get_reward_rules", "arguments": "{}"}},
            ]
        return "", "", [{"id": "c9", "type": "function",
                         "function": {"name": "finish_memorial",
                                      "arguments": json.dumps(_finish_args(self.finish_vid),
                                                              ensure_ascii=False)}}]


def test_tools_shape():
    names = [t["name"] for t in mt.TOOLS]
    ok(names == ["list_variations", "get_variation_scope", "get_reward_rules",
                 "finish_memorial"], names)
    payload = mt.tools_payload()
    for t in payload:
        fn = t["function"]
        ok(set(fn) == {"name", "description", "parameters"},
           "发给模型的只有三个字段，实际 %s" % sorted(fn))
        ok(len(fn["description"]) > 40, "%s 要把用法写进说明" % fn["name"])

        def walk(node, is_root):
            if isinstance(node, dict):
                if node.get("type") == "object" and not is_root:
                    ok(node.get("additionalProperties") is False,
                       "嵌套 object 要声明 openness：%s" % node)
                for v in (node.get("properties") or {}).values():
                    walk(v, False)
                if node.get("items") is not None:
                    walk(node["items"], False)
        walk(fn["parameters"], True)
    print("章奏工具定义 ✓ 四个独立工具、说明齐备、嵌套 object 收口")


def test_prompt_only_data_vars():
    text = io.open("Prompt/zhixian.txt", encoding="utf-8").read()
    used = sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", text)))
    ok(all(u in ("timestamp", "count") for u in used),
       "提示词里只该有数据变量，实际 %s" % used)
    for gone in ("{{event_point}}", "{{formula}}", "{{operation}}"):
        ok(gone not in text, "%s 该由工具提供，不该留在提示词里" % gone)
    # 返回字段规范搬到了交卷工具的说明里
    desc = mt.TOOL_INDEX["finish_memorial"]["description"]
    for must in ("variation_id", "full_text", "draft_processed", "reward_unprocessed",
                 "题本", "灾异奏报", "怎么传参"):
        ok(must in desc, "交卷说明里缺 %s" % must)
    ok(len(desc) > 600, "交卷说明要把字段与写法写全，实际 %d 字" % len(desc))
    print("章奏提示词 ✓ 只留数据变量；字段规范与标签在交卷工具说明里")


def test_tools_data_and_errors():
    s = GameSession()
    st = s.state
    # 空池：给地方风闻三条，并标明不绑定范围
    rows = mt.list_variation_rows(st)
    ok(len(rows) == 3 and all(not r["bound"] for r in rows), "空池要给风闻三条：%s" % rows)
    # 有变化点：窗口内取到
    record_variation(st, "disaster_weather", "【陕西】延安府亢旱，禾苗焦枯", "陕西")
    rows = mt.list_variation_rows(st)
    ok(rows[0]["bound"] is True and rows[0]["province"] == "陕西", rows[0])
    vid = rows[0]["variation_id"]
    txt, scope = mt.dispatch(st, None, "get_variation_scope", {"variation_id": vid})
    ok(scope["variation_type"] == "disaster_weather", scope["variation_type"])
    ok(scope["allowed_commands"], "要给出该变化允许的指令")
    ok(scope["province"] in scope["schema_text"], "范围文本要落实省份")
    # 未知 id：报错并指路
    try:
        mt.dispatch(st, None, "get_variation_scope", {"variation_id": "var-999"})
        raise AssertionError("未知变化点应当报错")
    except mt.ToolArgsError as e:
        ok("list_variations" in str(e), "报错要指路：%s" % e)
    # 交卷：正常 / 信封 / 缺字段
    ev = mt.finish_event(_finish_args(vid), st)
    ok(ev["variation_id"] == vid and ev["Location"] == "陕西延安府", ev)
    env = mt.finish_event({"arguments": json.dumps(_finish_args(vid), ensure_ascii=False)}, st)
    ok(env["title"] == _finish_args(vid)["title"], "信封要剥一层")
    try:
        mt.finish_event({"title": "只有标题"}, st)
        raise AssertionError("缺字段应当报错")
    except mt.ToolArgsError as e:
        ok("不要再包一层" in str(e) and "本次收到的字段" in str(e), "报错要写明怎么传：%s" % e)
    print("章奏工具取数与校验 ✓ 清单/范围/规则/交卷，缺参与未知 id 都如实报错")


def test_workflow_runs_to_finish():
    s = GameSession()
    st = s.state
    record_variation(st, "disaster_weather", "【陕西】延安府亢旱，禾苗焦枯", "陕西")
    prompt = build_side_prompt(st, count=1)
    res = mw.run_memorial_workflow(st, _FakeLLM(), prompt)
    ok(res["event"], "应当交卷：%s" % res.get("error"))
    ok(res["event"]["variation_id"] == "var-001", res["event"]["variation_id"])
    kinds = [e.get("kind") for e in res["log"]]
    ok(kinds[0] == "prompt" and "tool" in kinds, "轨迹要含提示词与工具往返：%s" % kinds)
    names = [e.get("name") for e in res["log"] if e.get("kind") == "tool"]
    ok(names[:3] == ["list_variations", "get_variation_scope", "get_reward_rules"]
       and names[-1] == "finish_memorial", names)

    # 截断的参数要如实记为失败，并且模型下一轮改对后仍能交卷
    res2 = mw.run_memorial_workflow(st, _FakeLLM(bad_first=True), prompt)
    failed = [e for e in res2["log"] if e.get("kind") == "tool" and e.get("ok") is False]
    ok(failed and "不是合法 JSON" in failed[0]["error"], failed[:1])
    ok(res2["event"], "模型改对后要能交卷")
    print("章奏工作流 ✓ 多轮往返到交卷；截断参数如实报错后可恢复")


def test_generate_side_uses_workflow():
    s = GameSession()
    st = s.state
    record_variation(st, "disaster_weather", "【陕西】延安府亢旱，禾苗焦枯", "陕西")
    batch = generate_side(st, _FakeLLM(), count=2, id_fn=s.next_event_id)
    ok(len(batch) == 2, "要两条，实际 %d" % len(batch))
    m = batch[0]
    for key in ("id", "title", "from", "content", "format", "category", "Location",
                "variation_id", "vtype", "draft_processed", "draft_unprocessed",
                "reward_processed", "reward_unprocessed"):
        ok(key in m, "章奏缺字段 %s" % key)
    ok(m["vtype"] == "disaster_weather", m["vtype"])
    ok(m["id"] != batch[1]["id"], "两条的 ID 要不同")
    # 一条都没交卷 → 退回 mock，不编造假章奏
    class _Dead:
        mock = False

        def chat_once(self, messages, tools=None, think=False):
            return "", "", []
    fell = generate_side(st, _Dead(), count=1, id_fn=s.next_event_id)
    ok(fell and fell[0].get("source") == "side", "没交卷时走 mock 兜底")
    print("章奏生成 ✓ 走工作流产出完整章奏；不交卷时退回 mock")


if __name__ == "__main__":
    test_tools_shape()
    test_prompt_only_data_vars()
    test_tools_data_and_errors()
    test_workflow_runs_to_finish()
    test_generate_side_uses_workflow()
    print("── 章奏工具与工作流测试全部通过")
