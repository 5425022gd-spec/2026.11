"""势力 AI 敌国回合：每季度初为本国决策一次，按决议在季度内逐日发令。

口径 按 `开发文档.md` §3.2 与提示词 `Prompt/diguo_ai.txt`：
1. **只做有中枢的非明势力** 当前＝满清；起义军、土司叛乱走脚本，不接 LLM。
2. 每季度初**单独调用一次**大模型，决定本国本季全部活动；决议存 `state.faction_ai`、随存档，
   同一季度只决策一次 读档不重复决策。
3. 决议里的活动带"本季第几日"，季度内**逐日**发令；指令一律走
   `operations.execute_ai_operation state, cmd, faction=势力` 执行，不另开效果系统。
4. 脏返回 无返回 / 空 / 非法指令 / 超时→ 本季度该势力空过并记日志，不中断推进。
5. 势力名与归属取 `armies.OWNERS` 那一张表 名册里的 `faction` 字段就是势力名的写法。
"""

import os
from typing import Any, Dict, List

from .logger import CONSOLE

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_PATH = os.path.join(_BASE, "Prompt", "diguo_ai.txt")

# 势力 AI 的系统名 config/ai_systems.json 里的键，可在设置界面单独配模型
SYSTEM = "faction_ai"


def _operation_rules_tool():
    """取本国本季度可用的超控指令清单（原来是 {{operation_ai}} 整段塞进提示词）。"""
    from .ai_tools import define_tool
    from .operations import AI_OPERATIONS_DOC

    def _exec(args, state, session):
        return {"text": AI_OPERATIONS_DOC}

    return define_tool(
        "get_faction_rules",
        "取本国本季度可用的系统超控指令清单与写法。发布计划前先调用它；"
        "计划里的每一条活动都必须逐字照清单里的格式写，写错会被引擎拒绝。",
        {"type": "object", "properties": {}, "additionalProperties": False},
        {"type": "object",
         "properties": {"text": {"type": "string"}},
         "required": ["text"], "additionalProperties": False},
        lambda args, value: value["text"],
        _exec,
    )


def _try_operation_tool():
    """试跑一条指令：在状态副本上执行，把成功/失败的回执交给模型。"""
    from .ai_tools import define_tool

    def _exec(args, state, session):
        import copy
        from .operations import execute_ai_operation
        cmd = str(args.get("command") or "").strip()
        faction = str(args.get("faction") or "")
        if not cmd:
            return {"ok": False, "message": "空指令"}
        try:
            ok, msg = execute_ai_operation(copy.deepcopy(state), cmd, faction=faction)
        except Exception as e:                      # 引擎自身异常也要如实回执
            ok, msg = False, "%s: %s" % (type(e).__name__, e)
        return {"ok": bool(ok), "message": str(msg)}

    return define_tool(
        "try_faction_operation",
        "试跑一条超控指令（只校验，不改真实局面）：在状态副本上执行，返回是否可行与原因。"
        "写完一条活动后可以先试跑，被拒绝就照原因改，别把会被拒的指令交上去。",
        {"type": "object",
         "properties": {
             "command": {"type": "string", "description": "一条完整的超控指令，照清单格式写"},
             "faction": {"type": "string", "description": "下令一方，如 满清"},
         },
         "required": ["command"], "additionalProperties": False},
        {"type": "object",
         "properties": {"ok": {"type": "boolean"}, "message": {"type": "string"}},
         "required": ["ok", "message"], "additionalProperties": False},
        lambda args, value: ("可行：" if value["ok"] else "不可行：") + value["message"],
        _exec,
    )


def ai_factions() -> List[str]:
    """跑 AI 的势力名 有中枢且非明者；当前＝满清。

    势力名沿用名册里的 `faction` 字段写法，归属取 `armies.OWNERS`。
    """
    from .armies import OWNERS, load_armies_data, select_units
    units = load_armies_data().get("units") or []
    names: List[str] = []
    for code, info in OWNERS.items():
        if code == "ming" or not info.get("origin"):
            continue
        for u in select_units(units, owner=code):
            fac = (u.get("faction") or "").strip()
            if fac and fac not in names:
                names.append(fac)
    return names


def _store(state: Any) -> Dict[str, Any]:
    return state.faction_ai


def _quarter(state: Any) -> List[int]:
    from .weather import season_key
    return list(season_key(state.date))


def days_into_quarter(state: Any) -> int:
    """本季已过天数 本季第几日：季初第 1 日 = 1。"""
    from .weather import season_of, season_start_month
    month = int(state.date.get("month", 1))
    day = int(state.date.get("day", 1))
    start = season_start_month(season_of(month))
    months_in = (month - start) % 12
    return months_in * 30 + day


def _read_prompt() -> str:
    try:
        with open(PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _context_ai(state: Any, faction: str) -> str:
    """本国国情、军力与相关实录数据 {{context_ai}}。"""
    from .armies import OWNERS, owner_of, select_units, load_armies_data
    from . import localdata
    from .battle import _format_unit_data_for_prompt
    from .char_cards import load_cards

    code = owner_of(faction)
    lines: List[str] = []

    # 1) 本国疆域与民力 与明属各省同一套地方数据读口；疆域按**控制权**判
    lines.append ("【本国疆域与民力】")
    from . import localdata as _ld
    owned = [p for p in state.provinces.values()
             if _ld.controller_of(p) == (OWNERS.get(code) or {}).get("label")]
    if not owned:
        owned = [p for p in state.provinces.values() if p.get("owner") == code]
    for p in owned:
        loc = p.get("local") or {}
        lines.append(
            "%s %s｜基建 %s｜腐败 %s：人口 %.1f万 ｜ 物质产出 %.2f/月 ｜ 需求 %.2f/月 ｜ 供需 %.2f ｜ %s"
            % (p.get("name", p["id"]), loc.get("core", ""), loc.get("infra", 0), loc.get("corruption", 0),
               localdata.population(loc), localdata.material_production(loc),
               localdata.material_demand(loc), localdata.supply_demand_ratio(loc),
               loc.get("corruption_note", ""))
        )

    # 2) 本国军力 与战争裁判同一套部队数据写法
    lines.append("")
    lines.append ("【本国军力】")
    cards_pool = load_cards()
    units = select_units(load_armies_data().get("units") or [], owner=code)
    if not units:
        lines.append (" 本国暫无在册部队")
    for u in units:
        lines.append(_format_unit_data_for_prompt(u, cards_pool))
        lines.append("")

    # 3) 本国近况 世界历史里属于本国的记录由 {{history_12}} 给，这里只报当前态势
    lines.append ("【当前时局】")
    lines.append("本季第一日：%s" % _quarter_first_day(state))
    return "\n".join(lines)


def _quarter_first_day(state: Any) -> str:
    from .weather import season_of, season_start_month
    month = season_start_month(season_of(int(state.date.get("month", 1))))
    y = int(state.date.get("year", 1627))
    if season_of(int(state.date.get("month", 1))) == 3 and int(state.date.get("month", 1)) in (1, 2):
        y -= 1
    return "%d年%d月1日" % (y, month)


def _decide(state: Any, faction: str, key: List[int]) -> Dict[str, Any]:
    """为本国做一次季度决策 一次 LLM 调用，返回可存档的决议记录。"""
    from .llm import get_llm_for_system
    from .session import extract_history_12

    store = _store(state)
    prev = store.get(faction) or {}
    history = list(prev.get("history") or [])
    if prev.get("intent"):
        history.append("%s：%s" % (prev.get("when", ""), prev["intent"]))

    cur = state.date
    stamp = "%04d-%02d-%02d" % (int(cur.get("year", 1627)), int(cur.get("month", 1)), int(cur.get("day", 1)))
    tpl = _read_prompt()
    if not tpl:
        CONSOLE.log("faction_ai", "缺提示词", "未找到 Prompt/diguo_ai.txt，本季空过")
        return {"key": key, "intent": "", "events": [], "history": history, "when": stamp}

    prompt = (tpl.replace("{{ai}}", faction)
                 .replace("{{timestamp}}", stamp)
                 .replace("{{context_ai}}", _context_ai(state, faction))
                 .replace("{{history_ai_12}}", "\n".join(history) or " 本国此前无计划记录")
                 .replace("{{history_12}}", extract_history_12(state)))

    rec: Dict[str, Any] = {"key": key, "intent": "", "events": [], "history": history, "when": stamp}
    llm = get_llm_for_system(SYSTEM)
    if getattr(llm, "mock", False):
        CONSOLE.log("faction_ai", "%s 本季未决策" % faction, "未配置大模型，按空过处理")
        return rec

    from .submit_tools import FINISH_QUARTER, quarter_tool
    from .ai_workflow import run_tool_workflow
    messages = [{"role": "system", "content": "你现在将扮演国家决策 AI，只代表本国一方。"},
                {"role": "user", "content": prompt}]
    result = run_tool_workflow(llm, messages,
                               [_operation_rules_tool(), _try_operation_tool(), quarter_tool()],
                               state=state, session=None, finish_name=FINISH_QUARTER)
    parsed = result.get("value") or {}
    if not parsed:
        CONSOLE.log("faction_ai", "%s 本季未交卷" % faction,
                    str(result.get("error") or "模型没有调用交卷工具"))
        return rec

    rec["intent"] = str(parsed.get("quarter_intent") or "")
    events = []
    for ev in (parsed.get("events") or []):
        if not isinstance(ev, dict):
            continue
        cmd = str(ev.get("operations_ai") or "").strip()
        try:
            day = int(ev.get("day") or 0)
        except (TypeError, ValueError):
            day = 0
        if not cmd or day < 1:
            continue
        events.append({"day": day, "cmd": cmd, "done": False})
    events.sort(key=lambda x: x["day"])
    rec["events"] = events
    CONSOLE.log("faction_ai", "%s 本季决策完成" % faction,
                "方针：%s\n活动 %d 项：%s" % (rec["intent"][:120], len(events),
                                            "；".join("第%d日 %s" % (e["day"], e["cmd"]) for e in events)))
    return rec


def ensure_quarter_plan(state: Any) -> List[str]:
    """季度初：每个 AI 势力决策一次本季活动 同一季度只决策一次。"""
    notes: List[str] = []
    key = _quarter(state)
    store = _store(state)
    for faction in ai_factions():
        rec = store.get(faction) or {}
        if rec.get("key") == key:
            continue
        store[faction] = _decide(state, faction, key)
        intent = (store[faction].get("intent") or "").strip()
        if intent:
            notes.append("【%s本季方针】%s" % (faction, intent))
    return notes


def tick_faction_ai_daily(state: Any, elapsed: int = 1) -> List[str]:
    """季度内逐日发令：本季第几日到了就执行那一日的活动 一次性，不重复。"""
    notes: List[str] = []
    key = _quarter(state)
    store = _store(state)
    today = days_into_quarter(state)
    from .operations import execute_ai_operation

    for faction in ai_factions():
        rec = store.get(faction) or {}
        if rec.get("key") != key:
            continue                      # 本季还没决策 或已跨季，由 ensure_quarter_plan 处理
        for ev in rec.get("events") or []:
            if ev.get("done") or int(ev.get("day", 0)) > today:
                continue
            ok, msg = execute_ai_operation(state, ev["cmd"], faction=faction)
            ev["done"] = True
            line = "【%s第%d日】%s" % (faction, ev["day"], msg)
            notes.append(line)
            CONSOLE.log("faction_ai", "%s 发令" % faction, "%s → %s" % (ev["cmd"], msg))
            if not ok:
                notes.append (" 该项未执行：%s" % msg)
    return notes
