# -*- coding: utf-8 -*-
"""战争裁判 AI 交给模型的工具：把"固定字段的战报"收成一个交卷工具。

原来 Prompt/war.txt 里写了整段 JSONL 字段规范与示例，模型返回的文本再被 `parse_jsonl`
猜着解析；现在字段进 `finish_battle` 的 parameters、规范与示例进它的 description，
解析失败不再"静默造一份随机战报"。

复用 `engine/ai_tools.py` 的内核与 `engine/ai_workflow.py` 的往返循环 本套只需一轮往返。
"""

from typing import Any, Dict, List, Tuple

from . import ai_tools as _ai
from .ai_tools import ToolArgsError, define_tool

TOOL_FINISH = "finish_battle"
FINISH_PAYLOAD_KEYS = ("battle_process", "units")


def equipment_pairs(eq_losses: Any) -> List[Tuple[str, int]]:
    """把装备损失规范成 [ 装备名, 数量]。

    新数据是 [{"equipment": "万历二十年式白蜡杆长矛", "count": 10}] 交卷工具产出；
    旧存档里是 ["万历二十年式白蜡杆长矛10"] 名称加尾数——两种都收，
    但只有一处实现 apply_battle_losses 与本模块共用，不再各写一套正则。
    """
    out: List[Tuple[str, int]] = []
    for item in eq_losses or []:
        if isinstance(item, dict):
            name = str(item.get("equipment") or "").strip()
            try:
                count = int(item.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            if name:
                out.append((name, max(0, count)))
        elif isinstance(item, str):
            import re
            text = item.strip()
            m = re.search(r"(\d+)$", text)
            count = int(m.group(1)) if m else 5          # 旧格式没写数量时按 5 计 原口径
            name = re.sub(r"\d+$", "", text).strip()
            if name:
                out.append((name, count))
    return out


def _known_unit_names() -> List[str]:
    from .armies import load_armies_data
    return [u.get("name", "") for u in (load_armies_data().get("units") or []) if u.get("name")]


def _elite_int(value: Any) -> int:
    """精锐度变化：接受 3 / "+3" / "-2" 这类写法，别的写法就是错。"""
    if isinstance(value, bool):
        raise ToolArgsError ("参数.units[].elite_degradation 应为整数或带正负号的数字串")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip().replace("+", "")
        try:
            return int(text)
        except ValueError:
            raise ToolArgsError ("参数.units[].elite_degradation 取不出整数：%r" % value)
    raise ToolArgsError ("参数.units[].elite_degradation 应为整数或带正负号的数字串")


def _execute_finish(args: Dict[str, Any], state: Any, session: Any) -> Dict[str, Any]:
    """整理成战报：时间戳由引擎按当前日期补，不由模型写。"""
    units = []
    for u in args.get("units") or []:
        units.append({
            "unit_name": str(u.get("unit_name") or "").strip(),
            "deaths": int(u.get("deaths") or 0),
            "elite_degradation": "+%d" % _elite_int(u.get("elite_degradation")) 
                                 if _elite_int(u.get("elite_degradation")) > 0
                                 else str(_elite_int(u.get("elite_degradation"))),
            "equipment_loss": [{"equipment": n, "count": c}
                               for n, c in equipment_pairs(u.get("equipment_loss"))],
        })
    arch = args.get("architecture_result") or None
    if arch is not None:
        arch = {
            "architecture_name": str(arch.get("architecture_name") or "无"),
            "control_change": str(arch.get("control_change") or "无变化"),
            "is_damaged": bool(arch.get("is_damaged")),
            "damage_ratio": float(arch.get("damage_ratio") or 0.0),
        }
    date = ""
    if state is not None and getattr(state, "date", None):
        d = state.date
        date = "%s-%02d-%02d" % (d.get("year", 1627), d.get("month", 8), d.get("day", 1))
    return {"timestamp": date,
            "battle_process": str(args.get("battle_process") or "").strip(),
            "units": units,
            "architecture_result": arch}


def _render_finish(args: Dict[str, Any], value: Dict[str, Any]) -> str:
    loss = sum(u["deaths"] for u in value["units"])
    return "已收到战报：参战部队 %d 支，合计阵亡 %d 人。%s" % (
        len(value["units"]), loss,
        "含军事建筑争夺结果。" if value.get("architecture_result") else "")


def _finish_battle() -> Dict[str, Any]:
    desc = (
        "交回这场战役的战报，这是唯一的交卷通道：一旦调用，系统立刻停止调用模型，"
        "按你交回的内容结算双方部队与军事建筑。\n\n"
        "【怎么传参】下面这些字段要**直接**作为本次调用的参数传入，不要再包一层 arguments 或 function。\n"
        "  battle_process：战役过程叙述，含双方背景、调度、客观结果与己方视角的主观测算\n"
        "  units：数组，**所有参战部队各占一项** 己方与敌方都要写，每项含：\n"
        "    unit_name：部队名称，必须与发给你的军队数据里的名字完全一致\n"
        "    deaths：该部队阵亡人数，整数\n"
        "    elite_degradation：精锐度变化，负值下降、正值上升，写成整数或 +3/-2 这样的串；\n"
        "                       结算后的精锐度不会超过一百、不会低于零\n"
        "    equipment_loss：数组，每项 {\"equipment\": 装备名, \"count\": 损失数量}；\n"
        "                    逐个列出损毁、遗失或报废的装备；占优势时可以损失极少，重炮可以不损失\n"
        "  architecture_result：军事建筑争夺结果 仅在战役指令涉及军事建筑时给：\n"
        "    architecture_name：建筑名称\n"
        "    control_change：战后归属名 如 明 / 满清 / 叛军，无变化就写 无变化\n"
        "    is_damaged：是否损坏，true 或 false\n"
        "    damage_ratio：损坏比例，0.0 到 1.0\n"
        "    未涉及军事建筑时，这一项整个不要传。\n"
        "时间戳由引擎按当前日期补上，你不用写。缺字段会被退回并写明原因，按提示补齐后再调一次。\n\n"
        "【示例】\n"
        "  battle_process：己方率先抢占高地，以弓弩压制敌方前锋，随后骑兵侧击成功，"
        "敌方阵型溃散，但己方左翼步兵遭到顽强抵抗，损失较大。\n"
        "  units：白杆兵 阵亡 80、精锐 +3、损 万历二十年式白蜡杆长矛 10 与 嘉靖三十四年式戚家倭刀 5；\n"
        "         奢寅部叛军 阵亡 320、精锐 -5、损 西南老藤篾甲 50 与 川南土司阔刃苗刀 45\n"
        "  architecture_result：永宁城寨，归属 叛军，is_damaged true，damage_ratio 0.35"
    )
    return define_tool(
        TOOL_FINISH, desc,
        {
            "type": "object",
            "properties": {
                "battle_process": {"type": "string", "description": "战役过程叙述"},
                "units": {
                    "type": "array",
                    "description": "所有参战部队的损失明细 己方与敌方都要写",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unit_name": {"type": "string", "description": "部队名称，与军队数据一致"},
                            "deaths": {"type": "integer", "description": "阵亡人数"},
                            "elite_degradation": {"type": "string",
                                                  "description": "精锐度变化，如 +3 或 -2"},
                            "equipment_loss": {
                                "type": "array",
                                "description": "损毁遗失的装备",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "equipment": {"type": "string", "description": "装备名称"},
                                        "count": {"type": "integer", "description": "损失数量"},
                                    },
                                    "required": ["equipment", "count"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["unit_name", "deaths", "elite_degradation", "equipment_loss"],
                        "additionalProperties": False,
                    },
                },
                "architecture_result": {
                    "type": "object",
                    "description": "军事建筑争夺结果；未涉及就不要传这一项",
                    "properties": {
                        "architecture_name": {"type": "string"},
                        "control_change": {"type": "string"},
                        "is_damaged": {"type": "boolean"},
                        "damage_ratio": {"type": "number", "description": "0.0 到 1.0"},
                    },
                    "required": ["architecture_name", "control_change", "is_damaged", "damage_ratio"],
                    "additionalProperties": False,
                },
            },
            "required": ["battle_process", "units"],
        },
        {
            "type": "object",
            "properties": {
                "timestamp": {"type": "string"},
                "battle_process": {"type": "string"},
                "units": {"type": "array", "items": {
                    "type": "object",
                    "properties": {
                        "unit_name": {"type": "string"},
                        "deaths": {"type": "integer"},
                        "elite_degradation": {"type": "string"},
                        "equipment_loss": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"equipment": {"type": "string"},
                                           "count": {"type": "integer"}},
                            "required": ["equipment", "count"],
                            "additionalProperties": False,
                        }},
                    },
                    "required": ["unit_name", "deaths", "elite_degradation", "equipment_loss"],
                    "additionalProperties": False,
                }},
                "architecture_result": {
                    "type": "object",
                    "properties": {
                        "architecture_name": {"type": "string"},
                        "control_change": {"type": "string"},
                        "is_damaged": {"type": "boolean"},
                        "damage_ratio": {"type": "number"},
                    },
                    "required": ["architecture_name", "control_change", "is_damaged", "damage_ratio"],
                    "additionalProperties": False,
                },
            },
            "required": ["timestamp", "battle_process", "units"],
            "additionalProperties": False,
        },
        _render_finish,
        _execute_finish,
    )


TOOLS: List[Dict[str, Any]] = [_finish_battle()]
TOOL_INDEX: Dict[str, Dict[str, Any]] = _ai.tool_index(TOOLS)


def tools_payload() -> List[Dict[str, Any]]:
    return _ai.tools_payload(TOOLS)


def dispatch(state: Any, session: Any, name: str, args: Any) -> Tuple[str, Any]:
    return _ai.dispatch(TOOL_INDEX, state, session, name, args,
                        payload_keys=FINISH_PAYLOAD_KEYS)


def finish_report(args: Any, state: Any = None, session: Any = None) -> Dict[str, Any]:
    """交卷整理：校验部队名与字段后给出规范战报 缺什么就报错，不编造。"""
    args, _unwrapped = _ai.unwrap_envelope(args, TOOL_FINISH, FINISH_PAYLOAD_KEYS)
    try:
        checked = _ai.validate_args(TOOL_INDEX[TOOL_FINISH]["parameters"], args)
    except ToolArgsError as e:
        got = "、".join(sorted(args)) if isinstance(args, dict) and args else "空"
        raise ToolArgsError(
            "%s。本次收到的字段：%s。战报要的字段要**直接**作为 %s 的参数传入，"
            "不要再包一层 arguments 或 function。" % (e, got, TOOL_FINISH))
    report = _execute_finish(checked, state, session)
    known = _known_unit_names()
    unknown = [u["unit_name"] for u in report["units"] if u["unit_name"] not in known]
    if unknown:
        raise ToolArgsError(
            "这些部队名在名册里找不到：%s。请照发给你的军队数据里的名字原样写；"
            "名册里的名字有：%s" % ("、".join(unknown), "、".join(known)))
    return report
