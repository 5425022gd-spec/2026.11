# -*- coding: utf-8 -*-
"""AI 工具内核：按本机 DSH 的 `defineTool` 口径实现，供各套工具集共用。

DSH 的做法 本机 node_modules/@deepseek-ai/dsh 里核对过：
- 一份工具 = { name, description, parameters, output: {schema, render}, execute }；
- **只有 name / description / parameters 三个字段发给模型** DSH 的 schemas() 白名单就这三个，
  所以"工具怎么用、返回什么格式"一律写在 description 里，提示词里不再塞一份；
- 调用前按 parameters 校验参数 不合规抛 ToolArgsError；
- execute 只返回规范值，再按 output.schema 校验 不合规抛 ToolOutputError，
  最后交给 output.render 渲染成模型看到的文字；
- 参数根对象**开放** 容忍多余字段，嵌套 object 必须显式声明 openness，本文件按同一口径校验。

各套工具集 剧本、章奏……只写自己的工具定义与执行体，通用部分都在这里，避免各写一套。
"""

import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .logger import CONSOLE


# ═══════════════════════════════════════════════════════════
# 错误类型 与 DSH 同名同义
# ═══════════════════════════════════════════════════════════

class ToolArgsError(Exception):
    """参数不符合 schema DSH 里叫 ToolArgsError，同样在 execute 之前抛。"""


class ToolOutputError(Exception):
    """execute 的返回值不符合 output.schema DSH 里叫 ToolOutputError。"""


class ToolNotFound(Exception):
    """模型点了一个不存在的工具。"""


# ═══════════════════════════════════════════════════════════
# 参数 / 返回值校验
# ═══════════════════════════════════════════════════════════

_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _violations(spec: Dict[str, Any], value: Any, path: str) -> List[str]:
    """不符合 schema 的问题清单 空表＝合规。参数与返回值共用这一套。"""
    out: List[str] = []
    _collect(spec, value, path, out)
    return out


def _collect(spec: Dict[str, Any], value: Any, path: str, out: List[str]) -> None:
    want = spec.get("type")
    if want in _TYPES:
        if want == "integer" and isinstance(value, bool):
            out.append ("%s 应为整数" % path)
            return
        if want == "number" and isinstance(value, bool):
            out.append ("%s 应为数字" % path)
            return
        if not isinstance(value, _TYPES[want]):
            out.append("%s 应为 %s" % (path, want))
            return
    if "enum" in spec and value not in spec["enum"]:
        out.append("%s 只能取 %s" % (path, "、".join(map(str, spec["enum"]))))
    if want == "array" and isinstance(value, list) and spec.get("items"):
        for i, item in enumerate(value):
            _collect(spec["items"], item, "%s[%d]" % (path, i), out)
    if want == "object" and isinstance(value, dict):
        props = spec.get("properties") or {}
        for k in spec.get("required") or []:
            if k not in value:
                out.append("%s 缺必填字段 %s" % (path, k))
        if spec.get("additionalProperties") is False:
            extra = [k for k in value if k not in props]
            if extra:
                out.append("%s 有多余字段 %s" % (path, "、".join(extra)))
        for k, v in value.items():
            if k in props:
                _collect(props[k], v, "%s.%s" % (path, k), out)


def validate_args(parameters: Dict[str, Any], args: Any) -> Dict[str, Any]:
    """校验参数并返回 缺省补空对象。"""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolArgsError ("参数应为对象")
    violations = _violations(parameters, args, "参数")
    if violations:
        raise ToolArgsError("；".join(violations))
    return args


def describe_params(parameters: Dict[str, Any]) -> str:
    """参数名清单 报错时告诉模型该传什么。"""
    return "、".join(parameters.get("properties") or {})


# ═══════════════════════════════════════════════════════════
# 参数被裹成"函数调用外壳"时的剥离
# ═══════════════════════════════════════════════════════════

# 模型偶尔把参数裹成一层信封 形如 {"arguments": ...} 或
# {"name": "finish_script", "arguments": {...}}——这是把"函数调用的外壳"当成参数对象了。
# 只在这一种明确形状上剥一层，并在日志里记下来；不认识的形状照旧报错，不做无原则兜底。
_ENVELOPE_KEYS = {"arguments", "name", "id", "type", "function", "parameters", "tool_calls"}


def unwrap_envelope(args: Any, tool_name: str = "",
                    payload_keys: Optional[Sequence[str]] = None) -> Tuple[Any, bool]:
    """返回  参数, 是否剥过信封。

    payload_keys 给了就要求内层至少含其中一个键 各套工具集的必填字段，
    免得把"恰好有个 arguments 字段"的正常参数误剥。
    """
    if not isinstance(args, dict) or "arguments" not in args:
        return args, False
    if not set(args) <= _ENVELOPE_KEYS:
        return args, False
    inner = args["arguments"]
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            return args, False
    if not isinstance(inner, dict) or not inner:
        return args, False
    if payload_keys and not (set(inner) & set(payload_keys)):
        return args, False
    CONSOLE.log("ai", "工具参数剥了一层信封",
                "%s 收到的参数被裹成 %s，已取其中的 arguments" % (tool_name, sorted(args)))
    return inner, True


# ═══════════════════════════════════════════════════════════
# 工具定义与派发
# ═══════════════════════════════════════════════════════════

def define_tool(name: str, description: str, parameters: Dict[str, Any],
                output_schema: Dict[str, Any],
                render: Callable[[Dict[str, Any], Any], str],
                execute: Callable[[Dict[str, Any], Any, Any], Any]) -> Dict[str, Any]:
    """一份工具定义。字段与 DSH 的 `defineTool` 对齐：

    name / description / parameters 是**发给模型**的三个字段 DSH 的白名单就只有这三个；
    output.schema 约束 execute 的返回值，output.render 把该值渲染成模型看到的文字；
    execute 只返回规范值，不直接拼文字。
    """
    return {
        "name": name,
        "description": description,
        "parameters": parameters,
        "output": {"schema": output_schema, "render": render},
        "execute": execute,
    }


def tool_index(tools: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {t["name"]: t for t in tools}


def tools_payload(tools: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """发给模型的工具包：只有 name / description / parameters。"""
    return [{"type": "function",
             "function": {"name": t["name"],
                          "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in tools]


def dispatch(index: Dict[str, Dict[str, Any]], state: Any, session: Any,
             name: str, args: Any,
             payload_keys: Optional[Sequence[str]] = None) -> Tuple[str, Any]:
    """派发一次调用：剥信封 → 校验参数 → execute → 校验返回值 → render。

    返回  模型看到的文本, execute 的结构化返回值。
    参数不合规抛 ToolArgsError，返回值不合 output.schema 抛 ToolOutputError，
    工具自身出错原样抛出：都由调用方记成失败的调用结果，不吞成一句"成功"。
    """
    tool = index.get(name)
    if tool is None:
        raise ToolNotFound("没有名为 %s 的工具，可用工具：%s"
                           % (name, "、".join(index.keys())))
    args, _unwrapped = unwrap_envelope(args, name, payload_keys)
    try:
        checked = validate_args(tool["parameters"], args)
    except ToolArgsError as e:
        raise ToolArgsError("%s。%s 的参数是：%s；请把它们直接作为本次调用的参数传入。"
                            % (e, name, describe_params(tool["parameters"])))
    value = tool["execute"](checked, state, session)
    violations = _violations(tool["output"]["schema"], value, "返回值")
    if violations:
        raise ToolOutputError("%s 的返回值不符合 output.schema：%s"
                              % (name, "；".join(violations)))
    text = tool["output"]["render"](checked, value)
    return str(text), value


def text_render(get_text: Callable[[Any], str]) -> Callable[[Dict[str, Any], Any], str]:
    """常用渲染器：从规范值里取一段文本。"""
    def _render(_args: Dict[str, Any], value: Any) -> str:
        return get_text(value)
    return _render
