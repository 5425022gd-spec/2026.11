# -*- coding: utf-8 -*-
"""司礼监中旨转化交给模型的工具与工作流。

原来 `Prompt/silijian_ai.txt` 里塞着整份 18 类超控指令目录 `{{operation}}`，约两三千字，
还要求模型回 JSONL，解析不出来就把**玩家原话**当成命令行硬塞给引擎执行——错了也无人知晓。

现在：指令目录、合法取值靠 `get_operation_rules` 取；写好的指令可以先用 `try_operation`
在状态快照上试跑一次 不产生副作用，错了把原因回给模型改；最后用 `finish_command` 交卷。
"""

import copy
from typing import Any, Dict, List, Tuple

from . import ai_tools as _ai
from .ai_tools import define_tool
from .ai_workflow import run_tool_workflow
from .logger import CONSOLE

TOOL_FINISH = "finish_command"
FINISH_PAYLOAD_KEYS = ("command_line",)

MAX_MODEL_CALLS = 8
SYSTEM_TEXT = ("你现在是司礼监掌印太监，负责把皇上的口谕转成系统可执行的命令行。"
               "先用 get_operation_rules 看有哪些指令，写好可以用 try_operation 试跑，"
               "确认能执行后用 finish_command 交回。")


def _execute_rules(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    from .operations import get_all_operations_schema
    return {"text": get_all_operations_schema()}


def _get_operation_rules() -> Dict[str, Any]:
    return define_tool(
        "get_operation_rules",
        "取系统超控指令全集：每一类指令的写法、合法取值与示例。"
        "把口谕转成命令行时，只能用这里面的指令；口谕若不对应任何指令，"
        "就照口谕原文写成一条可执行的文本命令。",
        {"type": "object", "properties": {}, "additionalProperties": False},
        {"type": "object", "properties": {"text": {"type": "string"}},
         "required": ["text"], "additionalProperties": False},
        lambda args, value: value["text"],
        _execute_rules,
    )


def _execute_try(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    """在状态快照上试跑 deepcopy，不产生任何副作用。"""
    from .operations import execute_operation_command
    cmd = str(args.get("command_line") or "").strip()
    try:
        snapshot = copy.deepcopy(state)
    except Exception as e:                                  # 快照失败就不试跑，如实说
        return {"ok": False, "msg": "取状态快照失败，无法试跑：%r" % e, "dry_run": False}
    try:
        ok, msg = execute_operation_command(snapshot, cmd)
    except Exception as e:
        return {"ok": False, "msg": "%s: %s" % (type(e).__name__, e), "dry_run": False}
    return {"ok": bool(ok), "msg": str(msg), "dry_run": True}


def _render_try(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    if value.get("dry_run") is False:
        return "试跑不了：%s" % value["msg"]
    if value["ok"]:
        return "这条指令可以执行 试跑结果，未真正生效：%s" % value["msg"]
    return "这条指令执行不了：%s。请照 get_operation_rules 的写法改，或换一条。" % value["msg"]


def _try_operation() -> Dict[str, Any]:
    return define_tool(
        "try_operation",
        "在状态快照上试跑一条命令行，看它能不能被执行、会有什么效果；**不会真的改动游戏**。"
        "写完指令先试跑一次，失败了照返回的原因改，改到能执行再交卷。",
        {
            "type": "object",
            "properties": {
                "command_line": {"type": "string", "description": "要试跑的命令行，如 调拨地方窖藏到国库 陕西 5.0"},
            },
            "required": ["command_line"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
                "msg": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["ok", "msg", "dry_run"],
            "additionalProperties": False,
        },
        _render_try,
        _execute_try,
    )


def _execute_finish(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    return {"command_line": str(args.get("command_line") or "").strip()}


def _finish_command() -> Dict[str, Any]:
    desc = (
        "交回最终的命令行，这是唯一的交卷通道：一旦调用，系统立刻停止调用模型并执行这条命令行。\n\n"
        "【怎么传参】command_line 要**直接**作为本次调用的参数传入，不要再包一层 arguments 或 function，"
        "也不要写成 JSONL 文本。\n"
        "  command_line：一条命令行，如 修改人物位置 魏忠贤 南直隶 / 调拨地方窖藏到国库 陕西 5.0\n\n"
        "【要求】\n"
        "- 只能用 get_operation_rules 里列出的指令与其合法取值；\n"
        "- 口谕若对应某条指令，就转成那条指令；口谕若不对应任何指令，就把口谕原文当作一条文本命令交回；\n"
        "- 交卷前务必用 try_operation 确认这条指令能执行。"
    )
    return define_tool(
        TOOL_FINISH, desc,
        {
            "type": "object",
            "properties": {"command_line": {"type": "string", "description": "最终的命令行"}},
            "required": ["command_line"],
        },
        {
            "type": "object",
            "properties": {"command_line": {"type": "string"}},
            "required": ["command_line"],
            "additionalProperties": False,
        },
        lambda args, value: "已收到命令行：%s" % value["command_line"],
        _execute_finish,
    )


TOOLS: List[Dict[str, Any]] = [_get_operation_rules(), _try_operation(), _finish_command()]
TOOL_INDEX: Dict[str, Dict[str, Any]] = _ai.tool_index(TOOLS)


def tools_payload() -> List[Dict[str, Any]]:
    return _ai.tools_payload(TOOLS)


def dispatch(state: Any, session: Any, name: str, args: Any) -> Tuple[str, Any]:
    return _ai.dispatch(TOOL_INDEX, state, session, name, args,
                        payload_keys=FINISH_PAYLOAD_KEYS)


def finish_command(args: Any, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """交卷整理：校验 command_line 非空。"""
    from .ai_tools import ToolArgsError
    args, _unwrapped = _ai.unwrap_envelope(args, TOOL_FINISH, FINISH_PAYLOAD_KEYS)
    try:
        checked = _ai.validate_args(TOOL_INDEX[TOOL_FINISH]["parameters"], args)
    except ToolArgsError as e:
        got = "、".join(sorted(args)) if isinstance(args, dict) and args else "空"
        raise ToolArgsError(
            "%s。本次收到的字段：%s。命令行要放进 command_line **直接**作为 %s 的参数传入。"
            % (e, got, TOOL_FINISH))
    value = _execute_finish(checked, state, session)
    if not value["command_line"]:
        raise ToolArgsError ("command_line 是空的：请给出最终要执行的命令行。")
    return value


def run_zhongzhi_workflow(state: Any, llm: Any, prompt: str,
                          session: Any = None) -> Dict[str, Any]:
    """跑一次中旨转化工作流，返回 {command_line, log, error}。"""
    messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_TEXT},
                                      {"role": "user", "content": prompt}]
    log: List[Dict[str, Any]] = [{"kind": "prompt", "file": "silijian_ai.txt", "text": prompt},
                                 {"kind": "tools", "names": [t["name"] for t in TOOLS]}]
    res = run_tool_workflow(llm, messages, TOOLS, state=state, session=session,
                            finish_name=TOOL_FINISH, max_calls=MAX_MODEL_CALLS,
                            payload_keys=FINISH_PAYLOAD_KEYS,
                            finish_handler=finish_command, log=log)
    if res["value"] is None:
        CONSOLE.log("action", "司礼监未交回命令行", res["error"])
    return {"command_line": (res["value"] or {}).get("command_line", ""),
            "log": log, "error": res["error"]}
