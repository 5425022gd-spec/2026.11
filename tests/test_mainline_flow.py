"""主线剧本流程与"调用数据"的测试（v0.5.4 口径）：

1. 只用两套提示词：首次＝meeting_pizou_free.txt，改写＝meeting_pizou_replan.txt；
   旧的四份阶段提示词必须已删除。
2. **提示词只放提示词**：规则（大纲/事件展示/超控）以变量形式嵌在提示词里，
   变量都有生产方；提示词里**不得出现工具名**（工具说明写在工具自己的 description 里）。
3. **工具彼此独立、一次全给**：全部工具由 script_tools.TOOLS 一处定义，
   没有分轮解锁、没有"取规则"这类假工具；只有 finish_script 收交卷字段。
4. 调用数据：默认全国宏观，主线界面勾选的那套会被记录，并成为下次廷议的默认。
"""

import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_mainline_flow.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_mainline_flow_pos.json"))
from engine.session import GameSession
from engine import mainline_script as ml
from engine import script_workflow as sw
from engine import script_tools as st


# 代码会填的变量（提示词里出现的都必须在这张表上，免得写错名字漏成原文发给 AI）
FILLED = ["topic", "conversation", "context_data", "history", "history_12",
          "history_phases_summary", "members", "book", "report", "timestamp", "toolA"]


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_prompt_files():
    for name in ("meeting_pizou_free.txt", "meeting_pizou_replan.txt"):
        path = os.path.join("Prompt", name)
        ok(os.path.exists(path), "缺提示词：%s" % path)
        text = io.open(path, encoding="utf-8").read()
        used = sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", text)))
        bad = [u for u in used if u not in FILLED]
        ok(not bad, "%s 里有代码不填的变量 %s" % (path, bad))
        for must in ("{{topic}}", "{{conversation}}", "{{context_data}}"):
            ok(must in text, "%s 缺 %s" % (path, must))
        # 提示词只填数据：写作与返回格式归工具说明，不许拿变量当规则
        for rule in ("outline_rules", "output_rules", "operation_rules"):
            ok(rule not in text, "%s 里还有当规则用的变量 %s" % (path, rule))
        ok('""' not in text, "%s 里还有没填的空引号" % path)
        ok(len(text) > 300, "%s 内容太少" % path)

    # 旧的四份阶段提示词必须删干净（分阶段机制已废除）
    for p in (1, 2, 3, 4):
        gone = os.path.join("Prompt", "meeting_pizou_phase%d.txt" % p)
        ok(not os.path.exists(gone), "分阶段已废除，%s 不该还在" % gone)
    print("主线提示词 ✓ 只有 free/replan 两套，变量只填数据")


def test_rules_live_in_tool_description():
    """写作与返回规则写在 finish_script 的 description 里，不是提示词变量。"""
    desc = st.TOOL_INDEX["finish_script"]["description"]
    for must in ("剧情名称与介绍", "剧情大纲", "展示事件", "超控影响",
                 "display_type", "即时触发"):
        ok(must in desc, "finish_script 说明里缺规则：%s" % must)
    # 归档里模型连续 8 次交卷失败都是把内容裹进 arguments；说明里必须写明怎么传参
    for must in ("怎么传参", "不要再包一层", "name", "introduction", "outline", "events"):
        ok(must in desc, "说明里要写清传参形状，缺 %s" % must)
    ok(len(desc) > 2000, "说明要把规则写全，实际 %d 字" % len(desc))
    # 引擎里不该再有那三个规则取数口
    for gone in ("get_outline_rules_text", "get_event_rules_text", "get_operation_rules_text"):
        ok(not hasattr(sw, gone), "%s 该删" % gone)
    print("规则归位 ✓ 写作与返回规则在 finish_script 的 description 里")


def test_llm_request_has_max_tokens_and_no_silent_empty_args():
    """两个真问题的回归：
    1. 请求必须带 max_tokens（否则剧本那种大参数会被截断成非法 JSON）；
    2. 参数不是合法 JSON 时**不许吞成空参数**，要如实记为失败并把原文带给模型。
    """
    import json as _json
    import urllib.request
    from engine.llm import LLMClient

    # 1) 请求体里要有 max_tokens
    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def _fake_urlopen(req, timeout=None):
        captured["body"] = _json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    client = LLMClient()
    client.mock = False
    client.api_key = "test-key"
    orig = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen
    try:
        client._request([{"role": "user", "content": "hi"}], tools=None, think=False)
    finally:
        urllib.request.urlopen = orig
    ok(captured["body"].get("max_tokens"), "请求体里必须带 max_tokens：%s" % captured["body"].keys())

    # 2) 截断/非法 JSON 的工具调用 → 如实报错，不变成空参数
    from engine import script_workflow as _sw

    class _FakeLLM:
        mock = False

        def __init__(self):
            self.round = 0

        def chat_once(self, messages, tools=None, think=False):
            self.round += 1
            if self.round == 1:
                # 参数被截断：以 { 开头但不是合法 JSON
                return "", "", [{"id": "c1", "type": "function",
                                 "function": {"name": "finish_script",
                                              "arguments": '{"name": "半截", "outline": "未写完'}}]
            return "", "", [{"id": "c2", "type": "function",
                             "function": {"name": "finish_script",
                                          "arguments": _json.dumps({
                                              "name": "补好的稿", "introduction": "试",
                                              "outline": "大纲",
                                              "events": [{"event_id": "t-1",
                                                          "trigger": "剧本开始后1天",
                                                          "display_type": "memorial",
                                                          "title": "题本",
                                                          "full_text": "正文"}]},
                                              ensure_ascii=False)}}]

    session = GameSession()
    res = _sw.run_script_workflow(session.state, session, mainline={"id": "probe"},
                                  topic="试", context_data="", book_text="", report_text="",
                                  members="", conversation="", llm=_FakeLLM())
    tools_log = [e for e in res["log"] if e.get("kind") == "tool"]
    ok(tools_log and tools_log[0]["ok"] is False, "截断的调用要记为失败：%s" % tools_log[:1])
    ok("不是合法 JSON" in tools_log[0]["error"], "失败原因要写明：%s" % tools_log[0]["error"])
    ok("截断" in tools_log[0]["result"], "回给模型的话要说清怎么改：%s" % tools_log[0]["result"][:60])
    ok(res["script"] and res["script"]["name"] == "补好的稿", "模型改对后仍要正常落库")
    print("请求与参数 ✓ max_tokens 已带、截断参数如实报错不吞成空")


def test_finish_accepts_and_rejects():
    """交卷参数：正常收；被裹成信封（字符串或对象）时剥一层；其它形状报错并给出改法。"""
    import json as _json
    payload = {"name": "试稿", "introduction": "试", "outline": "试大纲",
               "events": [{"event_id": "t-1", "trigger": "剧本开始后1天",
                           "display_type": "memorial", "title": "题本", "full_text": "正文"}]}
    ok(st.finish_payload(payload)["name"] == "试稿", "正常传参要收下")
    ok(st.finish_payload({"arguments": _json.dumps(payload, ensure_ascii=False)})["name"] == "试稿",
       "信封是 JSON 字符串时要剥一层")
    ok(st.finish_payload({"arguments": payload})["name"] == "试稿",
       "信封是对象时要剥一层")
    for bad in ({}, {"foo": 1}, {"arguments": {"bar": 1}}):
        try:
            st.finish_payload(bad)
            raise AssertionError("这种参数应当被退回：%s" % bad)
        except st.ToolArgsError as e:
            ok("直接" in str(e) and "不要再包一层" in str(e),
               "退回要写明怎么传参：%s" % e)
    print("交卷参数 ✓ 正常收、信封剥一层、错形状退回并写明改法")


def test_tools_are_independent_and_complete():
    """工具＝函数调用：各自一份定义，一次全给，只有一个交卷通道。"""
    names = [t["name"] for t in st.TOOLS]
    # 结束剧本的工具只在「没有数值硬判定」时随提示词下发，但仍要在 TOOLS 里登记
    ok(names == ["search_database", "list_database_items", "finish_script",
                 "submit_script_end"], names)
    payload = st.tools_payload()
    ok(len(payload) == len(st.TOOLS), "payload 与定义数量一致")
    for t in payload:
        fn = t["function"]
        ok(set(fn) == {"name", "description", "parameters"},
           "发给模型的只有 name/description/parameters，实际 %s" % sorted(fn))
        ok(fn["description"], "%s 缺工具说明" % fn["name"])
        for pname, pspec in (fn["parameters"].get("properties") or {}).items():
            ok(pspec.get("description"),
               "%s.%s 缺参数说明" % (fn["name"], pname))
        # 嵌套 object 必须显式声明 openness（同 DSH：参数根开放，嵌套收口）
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and node is not fn["parameters"]:
                    ok("additionalProperties" in node,
                       "%s 的嵌套 object 要显式声明 additionalProperties" % fn["name"])
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(fn["parameters"])
    # 每个工具都有 output.schema 与 render（execute 返回结构化值，文字由 render 出）
    for t in st.TOOLS:
        ok(t["output"]["schema"], "%s 缺 output.schema" % t["name"])
        ok(callable(t["output"]["render"]), "%s 缺 render" % t["name"])
        ok(callable(t["execute"]), "%s 缺 execute" % t["name"])    # 没有"取规则"这类假工具
    for fake in ("get_outline_rules", "get_event_rules", "get_operation_rules"):
        ok(fake not in st.TOOL_INDEX, "%s 是假工具，不该存在" % fake)
    print("工具定义 ✓ 三个独立工具、说明在 description 里、只有 finish_script 收交卷")


def test_tool_call_and_validation():
    """真调一次：搜索/列目录返回结构化值 + 渲染文本；参数不合规必须报错。"""
    s = GameSession()
    text, value = st.dispatch(s.state, s, "search_database",
                              {"queries": [{"keywords": "陕西", "domain": "地方情况"}]})
    ok(isinstance(value, dict) and "blocks" in value, "搜索要返回结构化结果")
    ok(text and isinstance(text, str), "搜索要渲染出文本")

    text2, value2 = st.dispatch(s.state, s, "list_database_items", {"domain": "六部"})
    ok(value2["items"] and len(value2["items"]) == 6, "六部目录应有 6 个条目")

    for bad, why in (({"domain": "不存在的域"}, "域不在枚举内"),
                     ({}, "缺必填参数")):
        try:
            st.dispatch(s.state, s, "list_database_items", bad)
            raise AssertionError("应当拦住：%s" % why)
        except st.ToolArgsError:
            pass
    # 参数根是开放对象（同 DSH）：多余字段容忍；但嵌套对象收口，多给字段要拦住
    st.dispatch(s.state, s, "list_database_items", {"domain": "六部", "extra": 1})
    try:
        st.dispatch(s.state, s, "search_database",
                    {"queries": [{"keywords": "陕西", "多余": 1}]})
        raise AssertionError("嵌套对象多给字段应当拦住")
    except st.ToolArgsError:
        pass
    try:
        st.dispatch(s.state, s, "nope", {})
        raise AssertionError("应当拦住不存在的工具")
    except st.ToolNotFound:
        pass

    payload = st.finish_payload({
        "name": "试稿", "introduction": "试", "outline": "试大纲",
        "events": [{"event_id": "t-1", "trigger": "剧本开始后1天",
                    "display_type": "memorial", "title": "题本", "full_text": "正文"}],
    })
    ok(payload["name"] == "试稿" and len(payload["events"]) == 1, "交卷要整理成落库结构")
    print("工具调用 ✓ 结构化返回 + 渲染、参数校验、交卷整理")


def test_prompt_to_workflow():
    """两套提示词的选择：首次撰写走 free，已经有剧本则走 replan。"""
    used = []
    from engine import council as council_mod
    orig = council_mod.read_prompt

    def fake(name):
        used.append(name)
        return orig(name)

    council_mod.read_prompt = fake
    try:
        session = GameSession()
        council = session.council
        mid = ml.load_mainlines_data()["mainlines"][0]["id"]

        council.start(mid, ["魏忠贤", "黄立极"], mode="court", mainline_id=mid)
        council.player_speak("此事当如何处置？")
        council.end_court_with_pizou()
        ok(used.count("meeting_pizou_free.txt") == 1,
           "首次撰写应走 free，实际 %s" % used)

        used.clear()
        council.start(mid, ["魏忠贤", "黄立极"], mode="court", mainline_id=mid)
        council.player_speak("就按此议行。")
        council.end_court_with_pizou()
        ok(used.count("meeting_pizou_replan.txt") == 1,
           "已有剧本时应走 replan，实际 %s" % used)
    finally:
        council_mod.read_prompt = orig
    print("提示词选择 ✓ 首次 free、已有剧本 replan")


def test_script_switches():
    session = GameSession()
    council = session.council
    mid = ml.load_mainlines_data()["mainlines"][0]["id"]

    ok(ml.get_script_switches({}) == ["macro"], "没记录时默认只有全国宏观")
    ok(ml.set_script_switches(mid, ["hubu", "henan"]), "记下勾选")
    ok(ml.get_script_switches(ml.get_mainline(mid)) == ["macro", "hubu", "henan"],
       "宏观强制在前，其余照勾选")

    council.start(mid, ["魏忠贤"], mode="court", mainline_id=mid)
    ok(council.data_switches == ["macro", "hubu", "henan"],
       "廷议默认数据＝上次留下的那套，实际 %s" % council.data_switches)

    council.set_data_switches(["libu", "shaanxi"])
    council.player_speak("就按此议行。")
    council.end_court_with_pizou()
    ok(ml.get_script_switches(ml.get_mainline(mid)) == ["macro", "libu", "shaanxi"],
       "廷议结束要把这次的清单留作下次默认")
    print("调用数据 ✓ 默认宏观、勾选记录、廷议继承、结束写回")


def test_event_tree_records_workflow():
    """事件树记录：一次廷议撰写 = 一条 script_sessions 记录，含对话轨迹。"""
    session = GameSession()
    council = session.council
    mid = ml.load_mainlines_data()["mainlines"][0]["id"]
    council.start(mid, ["魏忠贤"], mode="court", mainline_id=mid)
    council.player_speak("此事当如何处置？")
    council.end_court_with_pizou()
    s = session.list_script_sessions()
    ok(len(s) == 1, "应记下 1 条事件树记录，实际 %d" % len(s))
    ok(s[0]["mainline_id"] == mid, "记录要挂到该要紧事物上")
    ok(s[0]["log"], "记录里要有对话轨迹")
    ok(s[0]["script"] and s[0]["script"].get("events"), "记录里要有成稿剧本")
    print("事件树 ✓ 一次廷议撰写记一条，含对话轨迹与成稿剧本")


def test_event_tree_export():
    """事件树归档导出为 txt：整条归档与单条展示事件都能导出，落在项目根目录 default-user/。"""
    import shutil
    from engine import event_export as ee
    session = GameSession()
    council = session.council
    mid = ml.load_mainlines_data()["mainlines"][0]["id"]
    council.start(mid, ["魏忠贤"], mode="court", mainline_id=mid)
    council.player_speak("此事当如何处置？")
    council.end_court_with_pizou()
    sess = session.list_script_sessions()[0]

    written = []
    try:
        p_all = ee.export_session_txt(sess)
        written.append(p_all)
        ok(os.path.dirname(p_all) == ee.EXPORT_DIR, "要落在 default-user 下：%s" % p_all)
        text = io.open(p_all, encoding="utf-8").read()
        ok("工作流转录" in text and "展示事件" in text, "整条归档要有转录与展示事件")
        ok(sess["title"] in text, "归档要写清是哪条要紧事物")

        p_ev = ee.export_session_txt(sess, event_index=0)
        written.append(p_ev)
        ev_text = io.open(p_ev, encoding="utf-8").read()
        ok("第 1 条" in ev_text and "事件编号" in ev_text, "单条事件要带字段")
        ok("工作流转录" not in ev_text, "单条事件不该带整段转录")
        ok("事件1" in os.path.basename(p_ev), "单条事件的文件名要标序号")

        for bad in (-1, 99):
            try:
                ee.export_session_txt(sess, event_index=bad)
                raise AssertionError("越界序号应当报错：%s" % bad)
            except IndexError:
                pass
    finally:
        for p in written:
            if os.path.exists(p):
                os.remove(p)
        # 若 default-user 是本次新建且已空，一并清掉，别留空目录扰民
        if os.path.isdir(ee.EXPORT_DIR) and not os.listdir(ee.EXPORT_DIR):
            shutil.rmtree(ee.EXPORT_DIR, ignore_errors=True)
    print("事件树导出 ✓ 整条归档与单条事件各导一份 txt，越界报错")


if __name__ == "__main__":
    test_prompt_files()
    test_rules_live_in_tool_description()
    test_llm_request_has_max_tokens_and_no_silent_empty_args()
    test_finish_accepts_and_rejects()
    test_tools_are_independent_and_complete()
    test_tool_call_and_validation()
    test_prompt_to_workflow()
    test_script_switches()
    test_event_tree_records_workflow()
    test_event_tree_export()
    print("── 主线剧本流程与调用数据测试全部通过")
