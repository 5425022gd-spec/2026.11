"""要紧事物 主线：数据、结束条件与奖励、西北大灾的干旱阶段机制。

【v0.5.0 口径变更】
1. **主线剧本不再分阶段**：一套剧本从头写到尾，只用两套提示词
    首次＝`Prompt/meeting_pizou_free.txt`，改写＝`Prompt/meeting_pizou_replan.txt`。
   剧本由 AI 工作流撰写 见 engine/script_workflow.py，落在本模块的
   `long_term_script` / `Introduction` / `events` 三个字段上。
2. **每条主线都有结束条件与奖励** 写在 world/mainlines.json 的 `end` / `reward_points`，
   判定与发点在 `evaluate_mainlines()` 一处；奖励只发一次，记 `state.mainline_state`。
3. **西北大灾**改为特殊机制：按年份分四阶段刷新陕/晋/豫干旱地区修正 减产 + 温偏 + 降水倍率，
   与常规灾害系统独立、可叠加、不改变常规灾害的刷新概率，也不参与人口死亡判定。
   旧有的「额外增加旱灾概率」机制已从 weather.py 删除。
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .logger import CONSOLE

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAINLINES_FILE = os.path.join(_BASE, "world", "mainlines.json")

# 剧本撰写默认调用的数据 全国宏观强制，其余由玩家在主线界面勾选
DEFAULT_SCRIPT_SWITCHES = ["macro"]


def get_script_switches(mainline: Dict[str, Any]) -> List[str]:
    """该要紧事物撰写剧本时要发给 AI 的数据清单 上次留下的选择即默认。"""
    sw = list((mainline or {}).get("script_switches") or [])
    if "macro" not in sw:
        sw.insert(0, "macro")
    return sw


def set_script_switches(mainline_id: str, switches: List[str]) -> bool:
    """记下这次用的数据清单，作为下次撰写剧本的默认。"""
    data = load_mainlines_data()
    for m in data.get("mainlines", []):
        if m.get("id") == mainline_id:
            sw = [str(x) for x in (switches or [])]
            if "macro" not in sw:
                sw.insert(0, "macro")
            m["script_switches"] = sw
            save_mainlines_data(data)
            return True
    return False


def get_output_definitions() -> str:
    """生成 {{output}} 变量说明：剧本事件的四维结构与自动标签。

    四维正交：时间 × 地点 × 人物 × 事件类型。引擎只判定时间与地点，
    其余（谁来说、说什么、怎么演）全交模型；标签由引擎在固定时机自动发布。
    """
    return """
【事件结构】
每条事件由四个正交维度组成，缺哪个维度就写空对象 {}：

1. 时间（time）
   • {"type": "none"}                                     无时间条件
   • {"type": "after_start", "days": 3, "hours": 2, "minutes": 30}   剧本开始后
   • {"type": "after_tag", "tag": "西北大旱", "days": 1}    获得某标签后
   • {"type": "at", "at": "1627-09-01 08:30"}              固定时刻，精确到分钟
   时间一律用游戏内日期（年-月-日 时:分）。

2. 地点（place）
   • {"type": "none"}                                     无地点条件
   • {"type": "in_building", "place": "乾清宫"}             玩家在该建筑内
   • {"type": "tag_absent", "tag": "已赈灾"}                某标签不存在

3. 人物（cast）
   • []                                                    无参与人物
   • ["魏忠贤", "骆养性"]                                   任选一至数人，由你决定谁到场

4. 事件类型（type）
   • "memorial"          发布一个章奏
   • "request_dialogue"  角色获取剧本，向玩家请求开始对话
   • "force_dialogue"    角色获取剧本，强制玩家开始对话
   • "letter"            发布一个信件

5. 对话类事件的剧本（script）
   "request_dialogue" / "force_dialogue" 必须带 script 字段：写**这一段戏**的剧本——
   此人为何而来、他知道什么、想要什么、底线在哪、什么不能提。
   它不是整条主线的大纲，而是主线里的一个预期事件；会话 AI 拿它现场扮演，不要预先写台词。

6. 系统事件（只填 time 与 type，不解析 place / cast）
   • "rewrite_script"    重新撰写一次剧本（引擎会改用 meeting_pizou_replan_2.txt）
   • "broadcast_tag"     广播一个标签（标签名写进 tag 字段）

【自动标签】（不用你选，引擎在固定时机发布）
• 事件结束 → 事件结束:<event_id>
• 信件被阅读 → 信件已读:<event_id>
• 请求对话被拒 → 对话被拒:<event_id>
• 角色未达目的 → 未达目的:<event_id>:<说明>
• 章奏按剧本执行 → 章奏按剧本:<event_id>
• 章奏偏离剧本 → 章奏偏离剧本:<event_id>
• 地点条件判定失败 → 地点判定失败:<event_id>

【通用字段】
• "event_id": 事件唯一标识 如 "pe-1-1"
• "impact": 对应 <impact> 规范中的超控指令 无则填 ""
""".strip()


def get_impact_definitions() -> str:
    """生成 {{impact}} 变量说明：时间触发奖励与惩罚规则 即时触发 / 延时触发。"""
    return """
【事件影响清单 (impact)】
每个事件的 impact 字段用于定义该事件引发的实际系统状态变动，所有指令必须来自 <operation> 清单：

1. 即时触发 事件发生/曝光时立即执行：
   - 格式：即时触发：<超控指令>
   - 示例：即时触发：减少国库银两到地方窖藏 陕西 3.0

2. 延时触发 事件发生后经过指定物理天数触发：
   - 格式：事件后XX天触发：<超控指令>
   - 示例：事件后15天触发：降低地方各阶级满意度 陕西 农民 5.0
   - 示例：事件后30天触发：增加地方腐败 陕西 2.0
""".strip()


_RUNTIME_MAINLINES = None


def load_mainlines_data() -> Dict[str, Any]:
    """读取主线数据 纯内存，不写回文件，重启自动恢复初始状态。

    读不到或结构不对就是错，不静默给空表 原来文件损坏会被当成「没有主线」。
    """
    global _RUNTIME_MAINLINES
    if _RUNTIME_MAINLINES is None:
        with open(MAINLINES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("mainlines"), list):
            raise TypeError("world/mainlines.json: 'mainlines' must be a list")
        _RUNTIME_MAINLINES = data
    return _RUNTIME_MAINLINES


def save_mainlines_data(data: Dict[str, Any]) -> bool:
    """保存到当前进程运行时内存 不持久化写入磁盘文件。"""
    global _RUNTIME_MAINLINES
    _RUNTIME_MAINLINES = data
    return True


def get_mainline(mainline_id: str) -> Optional[Dict[str, Any]]:
    """根据 ID 获取要紧事物结构。"""
    for m in load_mainlines_data().get("mainlines", []):
        if m.get("id") == mainline_id:
            return m
    return None


def create_custom_mainline(title: str, desc: str = "", category: str = "") -> Dict[str, Any]:
    """自由廷议：由 AI 提炼标题创建新的要紧事物。

    ID 由已有主线的最大序号 +1 得出，**不再用 len(mls)+1** 内存重置后旧写法会撞号。
    """
    data = load_mainlines_data()
    mls = data.setdefault("mainlines", [])
    used = set()
    for m in mls:
        mid = str(m.get("id") or "")
        if mid.startswith("custom_mainline_"):
            tail = mid.rsplit("_", 1)[-1]
            if tail.isdigit():
                used.add(int(tail))
    seq = max(used) + 1 if used else 1
    ml_id = "custom_mainline_%d" % seq
    new_entry = {
        "id": ml_id,
        "title": title,
        "desc": desc or "朝堂廷辩立案 %s" % title,
        "category": category or "朝堂特议",
        "status": "active",
        "is_custom": True,
        "long_term_script": "",
        "Introduction": "",
        "script_name": "",
        "events": [],
        "occurred_events": [],
        "script_switches": list(DEFAULT_SCRIPT_SWITCHES),
    }
    mls.append(new_entry)
    save_mainlines_data(data)
    return new_entry


def get_history_script_summary(mainline: Dict[str, Any]) -> str:
    """改写剧本时给 AI 看的「已实际发生」历史：只含已曝光事件与玩家历次敕命，不含未发生的预设。"""
    records = []
    occurred = mainline.get("occurred_events") or []
    if occurred:
        records.append ("【本剧本截止当前已实际发生的事件】")
        for ev in occurred:
            records.append("《%s》%s：%s" % (ev.get("event_id", ""),
                                             ev.get("occurred_date", ""),
                                             ev.get("title") or ""))
            if ev.get("full_text"):
                records.append("  %s" % str(ev["full_text"])[:200])
    devs = mainline.get("deviations") or []
    if devs:
        records.append ("【偏离记录 玩家实际批复与剧本预设不同】")
        for d in devs:
            records.append("%s《%s》：剧本预设「%s」，实际「%s」" % (
                d.get("at", ""), d.get("event_id", ""),
                d.get("expected", ""), d.get("actual", "")))
    mods = mainline.get("player_modifications") or []
    if mods:
        records.append ("【朝廷历次廷议做出的实际干预动作与敕命】")
        for act in mods:
            records.append("%s 圣裁决议：%s" % (act.get("timestamp", ""), act.get("pizou", "")))
    return "\n".join(records) if records else " 该要紧事物初次立案，此前暂无已发生的历史事件"


def record_deviation(state: Any, mainline_id: str, event_id: str,
                     expected: str, actual: str) -> bool:
    """章奏批复与剧本预设不同时记一条偏离；相同则什么都不记。

    偏离不改变剧情走向，只让 AI 在改写剧本时看到「玩家没按剧本走」。
    """
    from .time import date_key
    data = load_mainlines_data()
    for m in data.get("mainlines", []):
        if m.get("id") != mainline_id:
            continue
        if not expected or expected == actual:
            return False
        m.setdefault("deviations", []).append({
            "event_id": event_id,
            "expected": expected,
            "actual": actual,
            "at": date_key(state.date) if state is not None else "",
        })
        save_mainlines_data(data)
        return True
    return False


def update_mainline_script(mainline_id: str, script_data: Dict[str, Any],
                           pizou_text: str = "") -> bool:
    """写入 AI 工作流产出的剧本：剧情名称 / 剧情介绍 / 剧情大纲 / 展示事件。

    不再有阶段：每次撰写 首次或改写都是**整份替换**当前剧本的事件集，
    已经发生过的事件另存 occurred_events，不会被覆盖。
    """
    data = load_mainlines_data()
    for m in data.get("mainlines", []):
        if m.get("id") != mainline_id:
            continue
        if script_data.get("name"):
            m["script_name"] = str(script_data["name"])
        if script_data.get("introduction"):
            m["Introduction"] = str(script_data["introduction"])
        if script_data.get("outline"):
            m["long_term_script"] = str(script_data["outline"])
        events = script_data.get("events")
        if events is not None:
            m["events"] = list(events)
            # 事件集换了：上一版的未发生事件作废，但剧本起始基准日保留 触发按整份剧本起算
            m.setdefault("script_start_date", None)
        if not m.get("script_start_date"):
            m["script_start_date"] = None      # 由 _tick_mainline_events 在首次推进那天写入
        m.setdefault("occurred_events", [])
        if pizou_text:
            m.setdefault("player_modifications", []).append({
                "timestamp": str(script_data.get("timestamp") or ""),
                "pizou": pizou_text,
            })
        m["status"] = m.get("status") or "active"
        save_mainlines_data(data)
        return True
    return False


# ═══════════════════════════════════════════════════════════
# 结束条件：判定集中在 END_CHECKS，奖励只发一次
# ═══════════════════════════════════════════════════════════

def _check_diplomacy_peace(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """目标国处于和平。外交系统尚未实现，故恒不达成 仅留设计。"""
    target = spec.get("target", "")
    return False, "%s：外交系统尚未实现，本项仅留设计" % target


def _check_arrears_cleared(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """所有机动部队与镇军欠饷均已清 合计不大于 0。"""
    from .armies import get_all_armies, select_units
    units = select_units(get_all_armies(state), owner="ming")
    total = round(sum(float(u.get("arrears_wan") or 0.0) for u in units), 2)
    return total <= 0.0, "欠饷合计 %.2f 万两" % total


def _check_liquidity_health_above(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    from .economy import calc_liquidity_health, calc_national_demand
    g = calc_liquidity_health(float(state.m1), float(state.financial_rate),
                              calc_national_demand(state))
    want = float(spec.get("value", 0.0))
    return g > want, "货币健康度 %.4f，目标大于 %.2f" % (g, want)


def _check_date_after_gregorian(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """当前公历日期晚于给定公历日期 如 1644-01-01。"""
    import datetime
    from .time import greg_date
    y, mo, d = (int(x) for x in str(spec.get("value", "")).split("-"))
    now = greg_date(state.date)
    return now > datetime.date(y, mo, d), "当前公历 %s，目标晚于 %s" % (now.isoformat(), spec.get("value"))


def _check_units_absent(state: Any, spec: Dict[str, Any]) -> Tuple[bool, str]:
    """指定名称的部队都不在名册里。"""
    from .armies import load_armies_data
    names = {str(u.get("name") or "") for u in (load_armies_data().get("units") or [])}
    want = [str(x) for x in (spec.get("units") or [])]
    left = [n for n in want if n in names]
    return not left, ("尚存：" + "、".join(left)) if left else "已全部不存在"


END_CHECKS = {
    "diplomacy_peace": _check_diplomacy_peace,
    "arrears_cleared": _check_arrears_cleared,
    "liquidity_health_above": _check_liquidity_health_above,
    "date_after_gregorian": _check_date_after_gregorian,
    "units_absent": _check_units_absent,
}


def _end_spec(mainline: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """主线的结束条件；**没有固定结束条件的主线返回 None**。

    口径：自由廷议 / 玩家自建的要紧事物本来就不写结束条件，会中由玩家自行结案，
    这不算错误，不许在这里报错打断推进。只有**写了 end.check 却没登记**才是配置错误，
    那必须报出来。
    """
    spec = mainline.get("end")
    if not isinstance(spec, dict) or not spec.get("check"):
        return None
    if spec["check"] not in END_CHECKS:
        raise KeyError("主线 %s 的 end.check 未登记：%r" % (mainline.get("id"), spec["check"]))
    return spec


def mainline_rows(state: Any) -> List[Dict[str, Any]]:
    """主线面板数据：标题 / 说明 / 结束条件 / 是否达成 / 奖励 / 状态。前端只渲染。"""
    rows = []
    done_map = dict(getattr(state, "mainline_state", {}) or {})
    for m in load_mainlines_data().get("mainlines", []):
        spec = _end_spec(m)
        if spec is None:
            # 自由廷议 / 自建要紧事物：没有固定结束条件，由玩家在会中自行结案
            met, detail = False, ""
        else:
            met, detail = END_CHECKS[spec["check"]](state, spec)
        rec = done_map.get(m.get("id")) or {}
        rows.append({
            "id": m.get("id"),
            "title": m.get("title"),
            "desc": m.get("desc", ""),
            "status": m.get("status", "active"),
            "end_text": (spec or {}).get("text", ""),
            "end_note": (spec or {}).get("note", "") or (
                "" if spec else "自由廷议：无固定结束条件，由玩家在会中自行结案"),
            "end_met": bool(met),
            "end_detail": detail,
            "reward_points": float(m.get("reward_points") or 0.0),
            "ended": bool(rec.get("ended")),
            "ended_when": rec.get("when", ""),
            "script_name": m.get("script_name", ""),
            "Introduction": m.get("Introduction", ""),
            "long_term_script": m.get("long_term_script", ""),
            "occurred_events": m.get("occurred_events") or [],
            "script_switches": get_script_switches(m),
            "current_phase_script": {"events": m.get("events") or []},
        })
    return rows


def evaluate_mainlines(state: Any) -> List[Dict[str, Any]]:
    """推进时调一次：把已达成结束条件的主线结案并发奖励 只发一次。"""
    from .tasks import grant_tech_points
    newly = []
    data = load_mainlines_data()
    changed = False
    for m in data.get("mainlines", []):
        mid = m.get("id")
        rec = state.mainline_state.get(mid) or {}
        if rec.get("ended"):
            continue
        spec = _end_spec(m)
        if spec is None:
            # 没有固定结束条件：永不自动结案，等玩家在廷议里自己了结
            continue
        met, detail = END_CHECKS[spec["check"]](state, spec)
        if not met:
            continue
        points = float(m.get("reward_points") or 0.0)
        from .time import date_key
        state.mainline_state[mid] = {
            "ended": True,
            "when": date_key(state.date),
            "points": points,
            "detail": detail,
        }
        m["status"] = "completed"
        changed = True
        if points:
            grant_tech_points(state, points)
        newly.append({"id": mid, "title": m.get("title"), "reward_points": points})
        CONSOLE.log("mainline", "要紧事物结案：%s" % m.get("title"),
                    "%s → 科技点 +%g 当前 %g" % (detail, points, float(state.tech_points)))
    if changed:
        save_mainlines_data(data)
    return newly


# ═══════════════════════════════════════════════════════════
# 西北大灾：四阶段干旱地区修正 独立于灾害系统，可与常规灾害叠加
# ═══════════════════════════════════════════════════════════

NORTHWEST_ID = "northwest_famine"
DROUGHT_TAG = "西北大旱"


def _famine_phases(mainline: Dict[str, Any]) -> List[Dict[str, Any]]:
    phases = mainline.get("phases")
    if not isinstance(phases, list) or not phases:
        raise ValueError("主线 %s 缺 phases 西北大灾的分阶段干旱机制" % mainline.get("id"))
    return phases


def current_famine_phase(state: Any) -> Optional[Dict[str, Any]]:
    """按当前年份取西北大灾的阶段 年份不在任何阶段内则无。"""
    ml = get_mainline(NORTHWEST_ID)
    if ml is None:
        return None
    year = int(state.date.get("year", 1627))
    for ph in _famine_phases(ml):
        if int(ph.get("from", 0)) <= year <= int(ph.get("to", 0)):
            return ph
    return None


def tick_northwest_famine(state: Any) -> List[str]:
    """刷新西北大灾的干旱地区修正：本阶段该有的写入，不属于本阶段的撤去。

    写入形式与「超控设立的独立地区修正」同一条路 loc["custom_modifiers"]，
    因此：
    - 减产走既有的产能修正管道 `modifiers.build_local_modifier_index` 读它，不另开效果通道；
    - 温偏与降水倍率作为同一行的附加字段存在同一个条目上，**一个修正只有一份数据**；
    - 与常规灾害互不干扰：本函数既不掷灾害概率，也不写 loc["weather"]；
    - 不参与人口死亡判定：只给产能与气候，不带任何死亡项。
    """
    notes = []
    ml = get_mainline(NORTHWEST_ID)
    if ml is None:
        return notes
    phase = current_famine_phase(state)
    want = {}
    if phase:
        for mod in phase.get("modifiers") or []:
            want[str(mod["province"])] = mod

    from .time import date_key
    stamp = date_key(state.date)

    for pid, p in state.provinces.items():
        loc = p.get("local") or {}
        if not loc:
            continue
        mods = loc.setdefault("custom_modifiers", [])
        # 先撤掉旧的西北大旱条目 本轮重新按阶段写入，避免叠加出两份
        kept = [c for c in mods if c.get("tag") != DROUGHT_TAG]
        spec = want.get(pid)
        if spec is not None:
            kept.append({
                "tag": DROUGHT_TAG,
                "name": spec["name"],
                "effect_pct": -abs(float(spec["penalty"])),
                "affected_classes": ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"],
                "desc": spec.get("desc", ""),
                "temp_delta": float(spec["temp"]),
                "precip_ratio": float(spec["precip"]),
                "since": stamp,
            })
        if kept != mods:
            loc["custom_modifiers"] = kept
            if spec is not None:
                notes.append("【西北大灾第%d阶段】%s 刷新地区修正 %s 减产 %d%%｜温 +%.1f℃｜降水 ×%.2f"
                             % (int(phase.get("phase", 0)), p.get("name", pid), spec["name"],
                                int(abs(float(spec["penalty"])) * 100), float(spec["temp"]),
                                float(spec["precip"])))
            else:
                notes.append("【西北大灾】%s 撤去大旱地区修正" % p.get("name", pid))
    return notes


def drought_climate(loc: Dict[str, Any]) -> Tuple[float, float]:
    """该省的西北大旱气候偏移 温偏℃, 降水倍率；没有则 (0.0, 1.0)。"""
    temp = 0.0
    precip = 1.0
    for c in (loc.get("custom_modifiers") or []):
        if c.get("tag") != DROUGHT_TAG:
            continue
        temp += float(c.get("temp_delta") or 0.0)
        precip *= float(c.get("precip_ratio") or 1.0)
    return temp, precip
