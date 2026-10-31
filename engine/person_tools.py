# -*- coding: utf-8 -*-
"""人物档案生成交给模型的工具：把"单行/多行 JSON 档案"收成一个交卷工具。

原来 `Prompt/create_person.txt` 用 `<output_format_rules>` 写整段 JSON 结构，
服务端再把模型返回的文本去围栏后 `json.loads`。现在结构进 `finish_person_card` 的
parameters、写法规范进它的 description；**引擎已知的事实** 姓名、阵营、官职、驻地、现职
由引擎覆盖写入，AI 只拟字、生年、籍贯、性格与生平，不靠提示词自律。
"""

from typing import Any, Dict, List, Tuple

from . import ai_tools as _ai
from .ai_tools import ToolArgsError, define_tool

TOOL_FINISH = "finish_person_card"
FINISH_PAYLOAD_KEYS =  "basic", "性格", "生平"

MAX_TRAITS = 4          # 性格条数上限 原提示词口径：不超过四个
MIN_EVENTS = 3          # 生平条数下限 原提示词口径：不少于三个


def _person_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "级": None,      # 占位不用，保持结构直观
        },
    }


def _finish_card(args: Dict[str, Any], name: str, title: str, loc: str,
                 faction: str, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """整理成一张人物卡：引擎已知的事实一律以引擎给的为准。"""
    basic = args.get("basic") or {}
    st = args.get("state") or {}
    traits = [t for t in (args.get ("性格" or [])) if isinstance(t, dict)]
    events = [e for e in (args.get ("生平" or [])) if isinstance(e, dict)]
    if len(traits) > MAX_TRAITS:
        raise ToolArgsError("性格给了 %d 条，最多 %d 条：请挑最贴切的那几条。"
                            % (len(traits), MAX_TRAITS))
    if len(events) < MIN_EVENTS:
        raise ToolArgsError("生平只给了 %d 条，至少要 %d 条：从出生写到当今。"
                            % (len(events), MIN_EVENTS))
    return {
        "basic": {
            "姓名": name,
            "字": str(basic.get("字") or "无").strip(),
            "生年": str(basic.get("生年") or "").strip(),
            "籍贯": str(basic.get("籍贯") or "").strip(),
            "阵营": faction,
            "官职": title,
        },
        "state": {
            "驻地": loc,
            "状态": str(st.get("状态") or "在任").strip(),
            "现职": title,
        },
        "性格": [{"标题": str(t.get ("标题" or "").strip()),
                  "述": str(t.get ("述" or "").strip())} for t in traits],
        "生平": [{"年": str(e.get ("年" or "").strip()),
                  "事": str(e.get ("事" or "").strip())} for e in events],
    }


def _render_card(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    return "已收到人物档案：%s，字 %s，性格 %d 条，生平 %d 条。" % (
        value["basic"]["姓名"], value["basic"]["字"],
        len(value["性格"]), len(value["生平"]))


def _finish_person_card() -> Dict[str, Any]:
    desc = (
        "交回这位官员的人物档案，这是唯一的交卷通道：一旦调用，系统立刻停止调用模型并落库。\n\n"
        "【怎么传参】把档案直接作为本次调用的参数传入，不要再包一层 arguments 或 function。\n"
        "  basic：{字, 生年, 籍贯} —— 姓名、阵营、官职由引擎按已知事实写入，你不用写也别改\n"
        "    字：按姓名另拟一个贴切的字号；无字者填 无\n"
        "    生年：只写明代纪年，不附公历括注，如 万历十八年\n"
        "    籍贯：写清府县，如 南直隶苏州府昆山县\n"
        "  state：{状态} —— 驻地与现职由引擎写入；状态如 在任\n"
        "  性格：数组，**最多 4 条**，每条 {标题, 述}；标题四字上下不加解释，"
        "解释一律写在同条的 述 里，述约 30 字\n"
        "  生平：数组，**至少 3 条**，每条 {年, 事}；年份只写明代纪年，事件直接叙述\n\n"
        "【写法】一律不用括号括注，不用小字注解；风格半文半白，辞章凝练典雅；"
        "人物要契合职位品阶、驻防辖地与明代历史背景。\n"
        "性格超过 4 条或生平少于 3 条会被退回并说明原因，改好后再调一次。"
    )
    params = {
        "type": "object",
        "properties": {
            "basic": {
                "type": "object",
                "description": "人物基本项；姓名/阵营/官职由引擎写入",
                "properties": {
                    "字": {"type": "string", "description": "字号；无字填 无"},
                    "生年": {"type": "string", "description": "明代纪年，如 万历十八年"},
                    "籍贯": {"type": "string", "description": "籍贯，写清府县"},
                },
                "required": ["字", "生年", "籍贯"],
                "additionalProperties": False,
            },
            "state": {
                "type": "object",
                "description": "任职状态；驻地/现职由引擎写入",
                "properties": {"状态": {"type": "string", "description": "如 在任"}},
                "required": ["状态"],
                "additionalProperties": False,
            },
            "性格": {
                "type": "array", "description": "性格，最多 4 条",
                "items": {
                    "type": "object",
                    "properties": {
                        "标题": {"type": "string", "description": "四字上下的标题"},
                        "述": {"type": "string", "description": "具体行事作风，约 30 字"},
                    },
                    "required": ["标题", "述"],
                    "additionalProperties": False,
                },
            },
            "生平": {
                "type": "array", "description": "生平大事，至少 3 条",
                "items": {
                    "type": "object",
                    "properties": {
                        "年": {"type": "string", "description": "明代纪年"},
                        "事": {"type": "string", "description": "事件叙述"},
                    },
                    "required": ["年", "事"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["basic", "state", "性格", "生平"],
    }
    out = {
        "type": "object",
        "properties": {
            "basic": {"type": "object", "properties": {
                "姓名": {"type": "string"}, "字": {"type": "string"},
                "生年": {"type": "string"}, "籍贯": {"type": "string"},
                "阵营": {"type": "string"}, "官职": {"type": "string"},
            }, "required": ["姓名", "字", "生年", "籍贯", "阵营", "官职"],
                "additionalProperties": False},
            "state": {"type": "object", "properties": {
                "驻地": {"type": "string"}, "状态": {"type": "string"}, "现职": {"type": "string"},
            }, "required": ["驻地", "状态", "现职"], "additionalProperties": False},
            "性格": {"type": "array", "items": {
                "type": "object", "properties": {"标题": {"type": "string"}, "述": {"type": "string"}},
                "required": ["标题", "述"], "additionalProperties": False}},
            "生平": {"type": "array", "items": {
                "type": "object", "properties": {"年": {"type": "string"}, "事": {"type": "string"}},
                "required": ["年", "事"], "additionalProperties": False}},
        },
        "required": ["basic", "state", "性格", "生平"],
        "additionalProperties": False,
    }
    return define_tool(TOOL_FINISH, desc, params, out, _render_card,
                       lambda a, s, ss: _finish_card(a, "", "", "", "", s, ss))


TOOLS: List[Dict[str, Any]] = [_finish_person_card()]
TOOL_INDEX: Dict[str, Dict[str, Any]] = _ai.tool_index(TOOLS)


def tools_payload() -> List[Dict[str, Any]]:
    return _ai.tools_payload(TOOLS)


def dispatch(state: Any, session: Any, name: str, args: Any) -> Tuple[str, Any]:
    return _ai.dispatch(TOOL_INDEX, state, session, name, args,
                        payload_keys=FINISH_PAYLOAD_KEYS)


def finish_card(args: Any, name: str, title: str, loc: str, faction: str,
                state: Any = None, session: Any = None) -> Dict[str, Any]:
    """交卷整理：校验字段与条数，并写入引擎已知的事实。"""
    args, _unwrapped = _ai.unwrap_envelope(args, TOOL_FINISH, FINISH_PAYLOAD_KEYS)
    try:
        checked = _ai.validate_args(TOOL_INDEX[TOOL_FINISH]["parameters"], args)
    except ToolArgsError as e:
        got = "、".join(sorted(args)) if isinstance(args, dict) and args else "空"
        raise ToolArgsError(
            "%s。本次收到的字段：%s。档案要的 basic、state、性格、生平 要**直接**作为 %s 的参数传入，"
            "不要再包一层 arguments 或 function。" % (e, got, TOOL_FINISH))
    return _finish_card(checked, name, title, loc, faction, state, session)
