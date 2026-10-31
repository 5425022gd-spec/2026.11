"""剧本数据库搜索工具 供 AI 通过 Tool Calls 检索剧本内容。

用途：AI 在召对、廷议、批红等场合需要翻查档案时，调两个工具之一：
  search_database       按关键词搜索某个域 或全部域的条目
  list_database_items   只列出某个域的子目录 条目名，不取全文

数据来源一律取自现有模块，本文件不自造第二套数据：
  六部 / 外诸司 / 二十四衙门 / 紫禁城设施  -> engine.depts.build_dept + 各部专用总览
  军队名册                              -> engine.armies.get_all_armies
  人物                                  -> engine.officials / factions / char_cards
  科技                                  -> engine.tech.tech_view
  战报                                  -> session.battle_reports
  对话查阅                              -> session.dialogues / session.list_dialogues
  国家                                  -> armies.OWNERS + depts_rites.DIPLOMATIC_COUNTRIES
  要紧事务                              -> engine.mainline_script.load_mainlines_data
  章奏                                  -> session.memorials 含事件树会话
  地方情况                              -> engine.localdata 明控省份 + build_province_view

约定：
  - 所有对外函数只返回字符串，绝不抛异常；取数失败一律在文本里写明原因。
  - keywords 用空格分隔多个词，全部命中才算命中 词序无关。
  - 条目摘要每条截断约 300 字，单次搜索总输出上限约 6000 字。
"""

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# 摘要与总量口径 唯一一处
SUMMARY_CHARS = 300
SEARCH_LIMIT = 6000          # 单次搜索总输出上限
BLOCK_LIMIT = 3000           # 单条 query 的输出上限 防一条大域吃光全部预算
LIST_LIMIT = 4000
CATALOG_LIMIT = 2600
SEARCH_SECONDS = 20.0        # 单次搜索取数的时间上限 超时即收尾并说明

MAX_SEARCH_QUERIES = 6


# ═══════════════════════════ 基础小工具 ═══════════════════════════

def _txt(value: Any) -> str:
    """把任意值收成一行干净文本 None 为空串，不写 "None"。"""
    if value is None:
        return ""
    if isinstance(value, float):
        return ("%g" % value)
    if isinstance(value, (int, str)):
        return str(value)
    return str(value)


def _join(items, sep: str = "；") -> str:
    parts = []
    for x in (items or []):
        s = _txt(x).strip()
        if s:
            parts.append(s)
    return sep.join(parts)


def _flatten(value: Any, depth: int = 0) -> str:
    """把总览嵌套结构压成可读文本 列表与字典都能读，不丢字段名。"""
    if value is None:
        return ""
    if isinstance(value, dict):
        # 栏名与廷议/召对材料共用 council.PAYLOAD_LABELS 这一张表 不再各写一套
        from .council import _label as _payload_label
        parts = []
        for k, v in value.items():
            if str(k).startswith("_"):
                continue
            k = _payload_label(k)
            inner = _flatten(v, depth + 1)
            if inner:
                parts.append("%s：%s" % (k, inner))
        return " " + "，".join(parts) + "" if depth > 0 else "，".join(parts)
    if isinstance(value, (list, tuple)):
        parts = [_flatten(v, depth + 1) for v in value]
        parts = [p for p in parts if p]
        if depth == 0:
            return "；".join(parts)
        return "、".join(parts[:12]) + ("…" if len(parts) > 12 else "")
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return "%g" % value
    return _txt(value).strip()


def _cut(text: str, limit: int = SUMMARY_CHARS) -> str:
    """截断到约 limit 字：尽量断在句读处，不加省略号之外的修饰。"""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    head = t[:limit]
    for mark in ("。", "；", "，", "、"):
        idx = head.rfind(mark)
        if idx >= limit - 80:
            return head[:idx + 1]
    return head + "…"


# ═══════════════════════════ 域一：六部 ═══════════════════════════

SIX_DEPTS = ["吏部", "户部", "礼部", "刑部", "兵部", "工部"]

# 有专用总览的部门：总览函数与它在总览界面上的名字 唯一一处
OVERVIEW_FUNCS = {
    "户部": ("engine.depts_hubu", "get_hubu_overview", "户部总览账册"),
    "吏部": ("engine.depts_libu", "get_libu_overview", "吏部总览文册"),
    "礼部": ("engine.depts_rites", "get_rites_overview", "礼部总览文册"),
    "刑部": ("engine.depts_xingbu", "get_xingbu_overview", "刑部总览律令"),
    "工部": ("engine.depts_works", "get_works_overview", "工部总览营造"),
    "兵部": ("engine.depts_war", "get_war_overview", "兵部总览军册"),
    "太仆寺": ("engine.depts_taipusi", "get_taipusi_overview", "太仆寺总览马政"),
}


def _call_overview(dept: str, state: Any) -> Tuple[str, str]:
    """取某部门专用总览：返回  标题, 文本；没有专用总览或取数失败则返回  "", 原因。"""
    spec = OVERVIEW_FUNCS.get(dept)
    if not spec:
        return "", ""
    mod_name, fn_name, title = spec
    try:
        mod = __import__(mod_name, fromlist=[fn_name])
        fn = getattr(mod, fn_name)
        data = fn(state)
    except Exception as exc:
        return "", "该项取数失败：%s.%s 出错 %s: %s" % (
            mod_name, fn_name, type(exc).__name__, exc)
    if not isinstance(data, dict):
        return title, _txt(data)
    return title, _flatten(data)


def _dept_text(name: str, state: Any) -> str:
    """一个部门 外朝六部、外诸司、二十四衙门、紫禁城殿宇的档案全文。"""
    from .depts import build_dept
    try:
        d = build_dept(name, state)
    except Exception as exc:
        return "该项取数失败：build_dept(%s) 出错 %s: %s" % (name, type(exc).__name__, exc)
    if not d:
        return "该项取数失败：部门库中没有 %s 的资料 engine.depts.DEPTS 与 " \
               "depts_neiting.DEPTS、depts_palaces.PALACES 都查不到" % name

    lines = ["【%s】" % d.get("name", name)]
    alias = d.get("alias") or []
    if alias:
        lines.append("别称：" + _join(alias, "、"))
    if d.get("intro"):
        lines.append("沿革与职能：" + _txt(d["intro"]))
    if d.get("headcount"):
        lines.append("编制：" + _txt(d["headcount"]))
    routine = d.get("routine") or []
    if routine:
        lines.append("日常事务：" + _join(routine))
    sub = d.get("suboffices") or []
    if sub:
        lines.append("下属机构：" + _join(
            "%s %s" % (_txt(x.get("name")), _txt(x.get("duty"))) if isinstance(x, dict)
            else _txt(x) for x in sub))
    hist = d.get("history") or []
    if hist:
        lines.append("沿革编年：" + _join(
            "%s %s" % (_txt(x.get("era")), _txt(x.get("text"))) if isinstance(x, dict)
            else _txt(x) for x in hist))
    staff = d.get("staff") or []
    if staff:
        lines.append("现任职官 %d 员：" % len(staff) + _join(
            "%s %s %s，%s 到任" % (_txt(s.get("title")), _txt(s.get("name")),
                                     _txt(s.get("faction")), _txt(s.get("start")))
            for s in staff))
    else:
        lines.append ("现任职官：无配员记录")
    figs = d.get("figures") or []
    if figs:
        lines.append("名宦录：" + _join(
            "%s %s：%s" % (_txt(f.get("name")), _txt(f.get("role")), _txt(f.get("deed")))
            if isinstance(f, dict) else _txt(f) for f in figs))
    anec = d.get("anecdotes") or []
    if anec:
        lines.append("掌故轶闻：" + _join(
            "%s：%s" % (_txt(a.get("title")), _txt(a.get("text"))) if isinstance(a, dict)
            else _txt(a) for a in anec))
    quotes = d.get("quotes") or []
    if quotes:
        lines.append("史料摘句：" + _join(
            "%s %s" % (_txt(q.get("text")), _txt(q.get("from"))) if isinstance(q, dict)
            else _txt(q) for q in quotes))
    data = d.get("data") or []
    if data:
        lines.append("实时数据：" + _join(data))
    if d.get("note"):
        lines.append("附注：" + _txt(d["note"]))
    if d.get("sources"):
        lines.append("出处：" + _join(d["sources"], "、"))

    otitle, otext = _call_overview(name, state)
    if otitle:
        lines.append("%s：%s" % (otitle, otext))
    return "\n".join(lines)


def _dept_items(names, session) -> List[Tuple[str, Callable[[], str]]]:
    return [(n, (lambda n=n: _dept_text(n, _state_of(session)))) for n in names]


# ═══════════════════════════ 域二：外诸司 / 域三：二十四衙门 ═══════════════════════════

WAIZHUSI = ["内阁", "都察院", "六科给事中", "通政使司", "大理寺", "太常寺", "光禄寺",
            "太仆寺", "鸿胪寺", "国子监", "翰林院", "詹事府", "太医院", "钦天监", "宗人府"]

NEITING = ["司礼监", "内官监", "御用监", "司设监", "御马监", "神宫监", "尚膳监", "尚宝监",
           "印绶监", "直殿监", "尚衣监", "都知监", "惜薪司", "钟鼓司", "宝钞司", "混堂司",
           "兵仗局", "银作局", "浣衣局", "巾帽局", "针工局", "内织染局", "酒醋面局", "司苑局"]


# ═══════════════════════════ 域四：紫禁城设施 ═══════════════════════════

def _palace_names() -> List[str]:
    """紫禁城地图上的殿宇名册 唯一来源：engine.depts_palaces.room_names 读 png/room.txt。"""
    from .depts_palaces import room_names
    return list(room_names())


# ═══════════════════════════ 域五：军队名册 ═══════════════════════════

def _army_line(u: Dict[str, Any]) -> str:
    return ("%s 军队ID：%s，归属 %s，类别 %s，%s 人，驻地 %s，任务 %s，状态 %s，"
            "精锐 %s，忠诚 %s，发饷 %s，欠饷 %s 万两" % (
                _txt(u.get("name")), _txt(u.get("army_id") or u.get("id")),
                _txt(u.get("name")), _txt(u.get("owner_name") or u.get("owner")),
                _txt(u.get("category")), _txt(u.get("count")),
                _txt(u.get("current_location_name") or u.get("station") or u.get("province_id")),
                _txt(u.get("mission")), _txt(u.get("status")), _txt(u.get("elite")),
                _txt(u.get("loyalty")), _txt(u.get("pay_rate")), _txt(u.get("arrears_wan"))))


def _army_text(unit_id: str, session) -> str:
    from .armies import get_all_armies
    state = _state_of(session)
    try:
        units = get_all_armies(state)
    except Exception as exc:
        return "该项取数失败：get_all_armies 出错 %s: %s" % (type(exc).__name__, exc)
    u = None
    for x in units:
        if str(x.get("id")) == unit_id:
            u = x
            break
    if u is None:
        return "该项取数失败：军队名册中已无 id 为 %s 的部队" % unit_id

    lines = ["【%s】%s" % (_txt(u.get("name")), _army_line(u))]
    if u.get("commander"):
        lines.append("主将：" + _txt(u.get("commander")))
    if u.get("desc"):
        lines.append("部队简介：" + _txt(u.get("desc")))
    if u.get("path_names"):
        lines.append("行军路径：" + _join(u.get("path_names"), " -> "))
    if u.get("total_distance_km") is not None:
        lines.append("行军里程：%s 千米" % _txt(u.get("total_distance_km")))
    if u.get("monthly_pay") is not None:
        lines.append("月度军饷：%s 万两" % _txt(u.get("monthly_pay")))
    if u.get("monthly_maint") is not None:
        lines.append("月度维护：%s 万两" % _txt(u.get("monthly_maint")))
    if u.get("overall_stance") or u.get("battle_order"):
        lines.append("战守方略：%s，军令 %s" % (_txt(u.get("overall_stance")),
                                            _txt(u.get("battle_order"))))
    eq = u.get("equipment") or {}
    if isinstance(eq, dict) and eq:
        lines.append("装备编成：" + _flatten(eq))
    return "\n".join(lines)


# ═══════════════════════════ 域六：人物 ═══════════════════════════

def _official_line(o: Dict[str, Any]) -> str:
    return "%s %s 籍贯属地 %s，位置 %s，党派 %s，%s 就任" % (
        _txt(o.get("title")), _txt(o.get("name")), _txt(o.get("province")),
        _txt(o.get("location")), _txt(o.get("faction")), _txt(o.get("start_date")))


def _people_group_text(kind: str, session) -> str:
    from .officials import civil_roster, list_military_by_region
    lines = []
    try:
        if kind == "文官":
            groups = civil_roster(include_capital=True)
            lines.append ("【在职文官名册】按辖区列出在任文官 含京官，武官归武官名册")
        elif kind == "武官":
            groups = list_military_by_region()
            lines.append ("【武官名册】按地域与边镇列出在任武职")
        else:
            return "该项取数失败：未知人物分组 %s" % kind
    except Exception as exc:
        return "该项取数失败：官员库读取出错 %s: %s" % (type(exc).__name__, exc)
    total = 0
    for prov, rows in groups.items():
        rows = rows or []
        total += len(rows)
        lines.append("%s %d 员：%s" % (_txt(prov), len(rows),
                                        _join(_official_line(o) for o in rows)))
    if not total:
        lines.append (" 当前没有在任记录")
    lines.append ("合计 %d 员。" % total)
    return "\n".join(lines)


def _cards_text(session) -> str:
    from .char_cards import card_view, list_cards
    try:
        rows = list_cards()
    except Exception as exc:
        return "该项取数失败：char_cards.list_cards 出错 %s: %s" % (type(exc).__name__, exc)
    if not rows:
        return "人物卡库当前无卡。"
    lines = ["【人物卡】共 %d 张 basic 基本信息 + state 可变状态" % len(rows)]
    for r in rows:
        name = r.get("name", "")
        lines.append(("%s：官职 %s，现职 %s，驻地 %s，状态 %s，党派 %s") % (
            _txt(name), _txt(r.get("官职")), _txt(r.get("现职")),
            _txt(r.get("驻地")), _txt(r.get("状态")), _txt(r.get("党派"))))
        try:
            full = card_view(name)
        except Exception as exc:
            lines.append("  该项取数失败：card_view(%s) 出错 %s: %s"
                         % (name, type(exc).__name__, exc))
            continue
        if not full:
            continue
        basic = full.get("basic") or {}
        extra = [(k, v) for k, v in basic.items()
                 if k not in  ("姓名", "官职", "阵营" and v)]
        if extra:
            lines.append("  基本信息：" + _join("%s %s" % (_txt(k), _txt(v)) for k, v in extra))
        traits = full.get("性格") or []
        if traits:
            lines.append("  性格：" + _join(
                ("%s%s" % (_txt(t.get("标题")), _txt(t.get("述")))) if isinstance(t, dict)
                else _txt(t) for t in traits))
        life = full.get("生平") or []
        if life:
            lines.append("  生平：" + _join(
                ("%s %s" % (_txt(t.get("年")), _txt(t.get("事")))) if isinstance(t, dict)
                else _txt(t) for t in life))
        st = full.get("state") or {}
        if st:
            lines.append("  可变状态：" + _join("%s %s" % (_txt(k), _txt(v))
                                            for k, v in st.items() if v))
    return "\n".join(lines)


# ═══════════════════════════ 域七：科技 ═══════════════════════════

def _tech_cache(session) -> Dict[str, Any]:
    from .tech import tech_view
    return tech_view(_state_of(session))


def _tech_tree_text(tree_id: str, session) -> str:
    view = _tech_cache(session)
    tree_names = {t.get("id"): t.get("name", t.get("id")) for t in (view.get("trees") or [])}
    nodes = [n for n in (view.get("nodes") or []) if n.get("tree") == tree_id]
    lines = ["【%s 科技树】共 %d 条，当前科技点 %s" % (
        _txt(tree_names.get(tree_id, tree_id)), len(nodes), _txt(view.get("points")))]
    for n in nodes:
        lines.append("%s %s，消耗 %s 点，状态 %s：前置 %s；后置 %s。%s%s" % (
            _txt(n.get("name")), _txt(n.get("line") or "未分组"), _txt(n.get("cost")),
            _txt(n.get("unlock_state")),
            _join(n.get("prereq_names"), "、") or "无",
            _join(n.get("dependent_names"), "、") or "无",
            _txt(n.get("intro")), _txt(n.get("effect"))))
    if not nodes:
        lines.append (" 该树下暂无条目")
    return "\n".join(lines)


def _tech_line_text(line: str, session) -> str:
    view = _tech_cache(session)
    nodes = [n for n in (view.get("nodes") or []) if (n.get("line") or "未分组") == line]
    lines = ["【科技分组 %s】共 %d 条" % (_txt(line), len(nodes))]
    for n in nodes:
        lines.append("%s %s，消耗 %s 点，状态 %s：%s%s" % (
            _txt(n.get("name")), _txt(n.get("tree_name")), _txt(n.get("cost")),
            _txt(n.get("unlock_state")), _txt(n.get("intro")), _txt(n.get("effect"))))
    return "\n".join(lines)


# ═══════════════════════════ 域八：战报 ═══════════════════════════

def _reports(session) -> List[Dict[str, Any]]:
    rows = getattr(session, "battle_reports", None)
    return list(rows) if isinstance(rows, list) else []


def _report_brief(r: Dict[str, Any]) -> str:
    rep = r.get("report") or {}
    units = rep.get("units") or []
    unit_bits = []
    for u in units:
        if isinstance(u, dict):
            unit_bits.append("%s 阵亡 %s，精锐变化 %s，损毁装备 %s" % (
                _txt(u.get("unit_name")), _txt(u.get("deaths")),
                _txt(u.get("elite_degradation")), _join(u.get("equipment_loss"), "、")))
        else:
            unit_bits.append(_txt(u))
    arch = rep.get("architecture_result") or {}
    return "【%s %s %s】参战：%s；对敌：%s。%s 双方损失：%s。%s" % (
        _txt(r.get("date")), _txt(r.get("province_name")), _txt(r.get("nature")),
        _join(r.get("friendly_names"), "、") or "未记",
        _join(r.get("enemy_names"), "、") or "未记",
        _txt(rep.get("battle_process")),
        _join(unit_bits) or "未记",
        ("军事建筑：%s，控制权变化 %s，损毁 %s" % (
            _txt(arch.get("architecture_name")), _txt(arch.get("control_change")),
            _txt(arch.get("damage_ratio" if arch else "")

))))
def _report_text(idx: int, session) -> str:
    rows = _reports(session)
    if idx < 0 or idx >= len(rows):
        return "该项取数失败：战报列表中已无第 %d 份 现有 %d 份" % (idx + 1, len(rows))
    r = rows[idx]
    if not isinstance(r, dict):
        return "该项取数失败：战报条目不是记录结构 %s" % type(r).__name__
    lines = [_report_brief(r)]
    rep = r.get("report") or {}
    if rep.get("timestamp"):
        lines.append("战报时间戳：" + _txt(rep.get("timestamp")))
    if r.get("involve_architecture"):
        lines.append ("本战涉及建筑争夺：是")
    return "\n".join(lines)


# ═══════════════════════════ 域九：对话查阅 ═══════════════════════════

DIALOGUE_LABELS = [ "council", "召对",  "court", "廷议",
                    "world", "世界总结",  "zhongzhi", "中旨"]


def _dialogue_text(category: str, session) -> str:
    label = dict(DIALOGUE_LABELS).get(category, category)
    rows = []
    lister = getattr(session, "list_dialogues", None)
    if callable(lister):
        try:
            rows = list(lister(category) or [])
        except Exception as exc:
            return "该项取数失败：session.list_dialogues(%s) 出错 %s: %s" % (
                category, type(exc).__name__, exc)
    else:
        allrows = getattr(session, "dialogues", None) or []
        rows = [d for d in allrows if isinstance(d, dict) and d.get("category") == category]
        rows = list(reversed(rows[-50:]))
    lines = ["【对话查阅 分类 %s】共 %d 条 最多列最近 50 条" % (label, len(rows))]
    if not rows:
        lines.append ("暂无记录。")
        return "\n".join(lines)
    for d in rows:
        if not isinstance(d, dict):
            lines.append(_txt(d))
            continue
        lines.append("编号 %s 《%s》 %s，参与 %s：%s" % (
            _txt(d.get("id")), _txt(d.get("memorial_title") or "无题"),
            _txt(d.get("created")),
            _join(d.get("participants"), "、") or "未记",
            _txt(d.get("summary"))))
        if d.get("pizou"):
            lines.append("  批奏：" + _txt(d.get("pizou")))
        entries = d.get("entries") or []
        if entries:
            lines.append("  条目：" + _join(
                "%s 论 %s：%s" % (_txt(e.get("character")), _txt(e.get("topic")),
                                 _txt(e.get("summary")))
                if isinstance(e, dict) else _txt(e) for e in entries))
        msgs = d.get("messages") or []
        if msgs:
            lines.append("  发言 %d 条：%s" % (len(msgs), _join(
                "%s：%s" % (_txt(m.get("name")), _txt(m.get("text"))[:120])
                if isinstance(m, dict) else _txt(m) for m in msgs[:10])))
    return "\n".join(lines)


def _script_sessions(session) -> Optional[List[Dict[str, Any]]]:
    """事件树会话：session 有 script_sessions 属性才算，没有就返回 None。"""
    if not hasattr(session, "script_sessions"):
        return None
    rows = getattr(session, "script_sessions")
    return list(rows) if isinstance(rows, list) else []


def _script_session_text(session) -> str:
    rows = _script_sessions(session)
    if rows is None:
        return "暂无记录：当前会话没有 script_sessions 属性 事件树会话数据未接入。"
    lines = ["【事件树会话】共 %d 条" % len(rows)]
    if not rows:
        lines.append ("暂无记录。")
        return "\n".join(lines)
    for t in rows:
        if not isinstance(t, dict):
            lines.append(_txt(t))
            continue
        lines.append("编号 %s 《%s》事发地 %s，批奏日期 %s，下次触发 %s 序号 %s，状态 %s" % (
            _txt(t.get("id")), _txt(t.get("title")), _txt(t.get("Location")),
            _txt(t.get("reply_date")), _txt(t.get("next_due")), _txt(t.get("next_seq")),
            _txt(t.get("status"))))
        if t.get("content"):
            lines.append("  正文：" + _txt(t.get("content")))
        if t.get("pizou"):
            lines.append("  批奏：" + _txt(t.get("pizou")))
        if t.get("draft"):
            lines.append("  票拟：" + _txt(t.get("draft")))
    return "\n".join(lines)


# ═══════════════════════════ 域十：国家 ═══════════════════════════

def _country_names() -> List[str]:
    """势力名：军队名册的归属 armies.OWNERS+ 礼部外交对象 DIPLOMATIC_COUNTRIES。"""
    from .armies import OWNERS, owner_label
    from .depts_rites import DIPLOMATIC_COUNTRIES
    names = []
    for code in OWNERS:
        label = owner_label(code)
        if label and label not in names:
            names.append(label)
    for c in DIPLOMATIC_COUNTRIES:
        n = (c or {}).get("name")
        if n and n not in names:
            names.append(n)
    return names


def _country_text(name: str, session) -> str:
    from .armies import OWNERS, get_all_armies, owner_label, owner_of, select_units
    from .localdata import build_province_view, controller_of
    from .depts_rites import DIPLOMATIC_COUNTRIES
    state = _state_of(session)

    lines = ["【%s】" % name]
    code = owner_of(name)
    if code:
        lines.append("势力标识：%s；军令发出省：%s" % (code, _txt(OWNERS.get(code, {}).get("origin")) or "无中枢"))
    else:
        lines.append ("势力标识：不在 armies.OWNERS 中 该名来自礼部外交对象名册")

    for c in DIPLOMATIC_COUNTRIES:
        if (c or {}).get("name") == name:
            lines.append("外交关系：%s；贸易 %s；岁贡 %s" % (
                _txt(c.get("relation")), _txt(c.get("trade_status")),
                _txt(c.get("tribute_annual"))))
            break

    # 该国名册部队
    try:
        units = get_all_armies(state)
    except Exception as exc:
        units = []
        lines.append("该项取数失败：get_all_armies 出错 %s: %s" % (type(exc).__name__, exc))
    mine = []
    if code:
        mine = [u for u in select_units(units, owner=code)]
    else:
        mine = [u for u in units if _txt(u.get("owner_name")) == name]
    if mine:
        lines.append("名册部队 %d 支：%s" % (len(mine), _join(_army_line(u) for u in mine)))
    else:
        lines.append ("名册部队：军队名册 world/armies.json中没有该国条目")

    # 其控制省份的地方数据
    provs = []
    for pid, p in (getattr(state, "provinces", None) or {}).items():
        ctrl = controller_of(p)
        if ctrl != name:
            continue
        try:
            v = build_province_view(state, pid)
        except Exception as exc:
            provs.append("%s：该项取数失败：build_province_view 出错 %s: %s"
                         % (pid, type(exc).__name__, exc))
            continue
        if not v:
            continue
        st = v.get("supply_status") or {}
        provs.append("%s %s：控制 %s，核心 %s，人口 %s 万，腐败 %s，基建 %s，"
                     "月产物质 %s，需求 %s，供需 %s %s，月税 %s 万两" % (
                         _txt(v.get("name") or pid), pid, _txt(v.get("controller")),
                         _txt((v.get("local") or {}).get("core")), _txt(v.get("population")),
                         _txt(v.get("corruption")), _txt((v.get("local") or {}).get("infra")),
                         _txt(v.get("material_production")), _txt(v.get("material_demand")),
                         _txt(v.get("supply_demand_ratio")), _txt(st.get("name")),
                         _txt(v.get("tax_total"))))
    if provs:
        lines.append("控制省份 %d 处：%s" % (len(provs), _join(provs)))
    else:
        lines.append ("控制省份：当前没有由该势力控制的省份")
    return "\n".join(lines)


# ═══════════════════════════ 域十一：要紧事务 ═══════════════════════════

def _mainline_texts(session) -> List[Tuple[str, str]]:
    """要紧事务：先取数再逐条生成，取数失败由调用方写成「取数失败」条目。"""
    from .mainline_script import load_mainlines_data
    data = load_mainlines_data()
    out = []
    for m in (data.get("mainlines") or []):
        if not isinstance(m, dict):
            continue
        title = _txt(m.get("title") or m.get("id"))
        lines = ["【要紧事务 %s】%s" % (_txt(m.get("id")), title)]
        if m.get("desc"):
            lines.append("立案说明：" + _txt(m.get("desc")))
        if m.get("status"):
            lines.append("状态：%s；当前阶段 %s" % (_txt(m.get("status")),
                                            _txt(m.get("current_phase"))))
        if m.get("end"):
            lines.append("结束条件：" + _txt(m.get("end")))
        if m.get("reward_points") is not None:
            lines.append("奖励点数：" + _txt(m.get("reward_points")))
        if m.get("long_term_script"):
            lines.append("长期剧本：" + _txt(m.get("long_term_script")))
        if m.get("Introduction"):
            lines.append("剧本引言：" + _txt(m.get("Introduction")))
        events = m.get("events") or []
        if events:
            lines.append("预设事件 %d 件：" % len(events) + _join(
                "%s %s，%s：%s" % (_txt(e.get("event_id")), _txt(e.get("display_type")),
                                    _txt(e.get("trigger")), _txt(e.get("title") or e.get("full_text")))
                if isinstance(e, dict) else _txt(e) for e in events))
        occurred = m.get("occurred_events") or []
        if occurred:
            lines.append("已发生事件 %d 件：" % len(occurred) + _join(
                "%s %s：%s" % (_txt(e.get("event_id")), _txt(e.get("occurred_date")),
                                _txt(e.get("title") or e.get("event_content")))
                if isinstance(e, dict) else _txt(e) for e in occurred))
        phases = m.get("phases") or []
        if phases:
            lines.append("阶段划分：" + _join(
                "%s %s" % (_txt(p.get("phase") or p.get("name")), _txt(p.get("desc") or p.get("title")))
                if isinstance(p, dict) else _txt(p) for p in phases))
        out.append((title, "\n".join(lines)))
    return out


# ═══════════════════════════ 域十二：章奏 ═══════════════════════════

def _memorial_text(mid: str, session) -> str:
    rows = getattr(session, "memorials", None)
    if not isinstance(rows, list):
        return "该项取数失败：session.memorials 不可读 类型 %s" % type(rows).__name__
    m = None
    for x in rows:
        if isinstance(x, dict) and str(x.get("id")) == mid:
            m = x
            break
    if m is None:
        return "该项取数失败：章奏目录中已无编号 %s 的章奏 已批阅归档者不在目录内" % mid

    lines = ["【章奏 %s】《%s》" % (_txt(m.get("id")), _txt(m.get("title")))]
    lines.append("体裁分类：%s；内容分类：%s" % (
        _txt(m.get("category_label") or m.get("format")),
        _txt(m.get("content_label") or m.get("category"))))
    lines.append("事发地 %s；呈递 %s；日期 %s；状态 %s；来源 %s" % (
        _txt(m.get("Location")), _txt(m.get("from")), _txt(m.get("date")),
        _txt(m.get("status")), _txt(m.get("source"))))
    if m.get("urgent"):
        lines.append ("紧要：是")
    body = m.get("full_text") or m.get("content")
    if body:
        lines.append("正文：" + _txt(body))
    draft = (m.get("draft") or m.get("selected_draft") or m.get("draft_processed")
             or m.get("draft_unprocessed"))
    if draft:
        lines.append("票拟：" + _txt(draft))
    reward = (m.get("reward") or m.get("reward_processed") or m.get("reward_unprocessed")
              or m.get("selected_reward"))
    if reward:
        lines.append("处置结果：" + _txt(reward))
    if m.get("variation_id"):
        lines.append("变化池编号：%s %s" % (_txt(m.get("variation_id")), _txt(m.get("vtype"))))
    if not body and not draft:
        lines.append ("说明：该章奏记录的正文与票拟字段均为空 现有记录里没有可读文本")
    return "\n".join(lines)


def _memorial_items(session) -> List[Tuple[str, Callable[[], str]]]:
    rows = getattr(session, "memorials", None)
    if not isinstance(rows, list):
        return []
    out = []
    seen = set()
    for m in rows:
        if not isinstance(m, dict):
            continue
        mid = _txt(m.get("id"))
        if not mid or mid in seen:
            continue
        seen.add(mid)
        label = "%s %s" % (mid, _txt(m.get("title")))
        out.append((label, (lambda mid=mid: _memorial_text(mid, _session_of_alias(session)))))
    return out


# ═══════════════════════════ 域十三：地方情况 ═══════════════════════════

def _province_line(pid: str, session) -> str:
    from .localdata import build_province_view
    state = _state_of(session)
    try:
        v = build_province_view(state, pid)
    except Exception as exc:
        return "该项取数失败：build_province_view(%s) 出错 %s: %s" % (
            pid, type(exc).__name__, exc)
    if not v:
        return "该项取数失败：state.provinces 中没有省份 %s 可能已被合并或更名" % pid
    loc = v.get("local") or {}
    st = v.get("supply_status") or {}
    lines = ["【%s】省份ID：%s" % (_txt(v.get("name") or pid), pid)]
    lines.append("控制权：%s；核心归属：%s；控制说明 %s" % (
        _txt(v.get("controller")), _txt(loc.get("core")), _txt(loc.get("core_note"))))
    lines.append("人口：%s 万人 %s 人；承载人口 %s 万人" % (
        _txt(v.get("population")), _txt(v.get("population_exact")),
        _txt(v.get("carrying"))))
    lines.append("腐败：%s %s；基建：%s" % (
        _txt(v.get("corruption")), _txt(loc.get("corruption_note")), _txt(loc.get("infra"))))
    lines.append("月产物质：%s；月需求物质：%s；供需指数 %s %s；物资库存 %s" % (
        _txt(v.get("material_production")), _txt(v.get("material_demand")),
        _txt(v.get("supply_demand_ratio")), _txt(st.get("name")),
        _txt(v.get("class_material_stock"))))
    lines.append("月税收总额：%s 万两；扶持合计 %s 两/月；本地市场价格指数 %s" % (
        _txt(v.get("tax_total")), _txt(v.get("support_total")), _txt(v.get("price_index"))))
    if v.get("liaoxiang"):
        lx = v["liaoxiang"]
        lines.append("辽饷分摊：月派 %s 万两，折物质损失 %s 系数 %s" % (
            _txt(lx.get("allocated_wan")), _txt(lx.get("matter_loss")),
            _txt(lx.get("matter_coef"))))

    weather = loc.get("weather") or {}
    if weather:
        lines.append("天气：%s，气温 %s，灾害 %s 余 %s 日，程度 %s，降水 %s 毫米" % (
            _txt(weather.get("condition")), _txt(weather.get("temp")),
            _txt(weather.get("disaster")), _txt(weather.get("disaster_remain")),
            _txt(weather.get("severity")), _txt(weather.get("rain_mm"))))

    classes = loc.get("classes") or {}
    if classes:
        bits = []
        for cname, c in classes.items():
            if not isinstance(c, dict):
                continue
            bits.append("%s 人口 %s 态度 %s 库存 %s 产出 %s 税比 %s 扶持 %s" % (
                _txt(cname), _txt(c.get("pop")), _txt(c.get("attitude")),
                _txt(c.get("stock")), _txt(c.get("output")), _txt(c.get("tax_rate")),
                _txt(c.get("support"))))
        lines.append("地方阶级：" + _join(bits))

    projects = loc.get("projects") or []
    if projects:
        lines.append("地方工程 %d 项：%s" % (len(projects), _join(
            "%s %s，%s，%s" % (_txt(pj.get("name")), _txt(pj.get("kind")),
                                 _txt(pj.get("scope")), _txt(pj.get("status")))
            if isinstance(pj, dict) else _txt(pj) for pj in projects)))

    mods = v.get("modifiers") or {}
    badges = mods.get("summary_badges") or []
    if badges:
        lines.append("地区修正要目：" + _join(
            b.get("label") if isinstance(b, dict) else b for b in badges))

    armies = v.get("province_armies") or {}
    if armies:
        lines.append("本省驻军：己方 %s 人 %s，敌方 %s 人 %s" % (
            _txt(armies.get("total_friendly_men")),
            _join(a.get("name") for a in (armies.get("friendly") or []) if isinstance(a, dict)) or "无",
            _txt(armies.get("total_enemy_men")),
            _join(a.get("name") for a in (armies.get("enemy") or []) if isinstance(a, dict)) or "无"))
    return "\n".join(lines)


# ═══════════════════════════ session 与 state 的取用 ═══════════════════════════

def _state_of(session) -> Any:
    """从会话取世界状态：会话本身就是 state 也能用。"""
    return getattr(session, "state", session)


def _session_of_alias(session) -> Any:
    return session


# ═══════════════════════════ 域定义表 ═══════════════════════════

def _no_items(note: str):
    return lambda state, session: [(note,  lambda note=note: "该项取数失败：%s" % note)]


def _dialogue_items(state, session):
    items = [(label, (lambda cat=cat: _dialogue_text(cat, _session_of_alias(session))))
             for cat, label in DIALOGUE_LABELS]
    if _script_sessions(session) is not None:
        items.append(("事件树", lambda: _script_session_text(_session_of_alias(session))))
    else:
        items.append( "事件树", lambda: "暂无记录：当前会话没有 script_sessions 属性。")
    return items


def _army_items(state, session):
    from .armies import get_all_armies
    try:
        units = get_all_armies(_state_of(session))
    except Exception as exc:
        return [("军队名册", (lambda: "该项取数失败：get_all_armies 出错 %s: %s"
                             % (type(exc).__name__, exc)))]
    out = []
    for u in units:
        if not isinstance(u, dict):
            continue
        uid = _txt(u.get("id"))
        if not uid:
            continue
        out.append((_txt(u.get("name")) or uid,
                    (lambda uid=uid: _army_text(uid, _session_of_alias(session)))))
    return out


def _people_items(state, session):
    return [("在职文官", lambda: _people_group_text("文官", _session_of_alias(session))),
            ("武官", lambda: _people_group_text("武官", _session_of_alias(session))),
            ("人物卡", lambda: _cards_text(_session_of_alias(session)))]


def _tech_items(state, session):
    view = _tech_cache(session)
    out = []
    for t in (view.get("trees") or []):
        tid = t.get("id")
        if not tid:
            continue
        out.append((_txt(t.get("name")) or tid,
                    (lambda tid=tid: _tech_tree_text(tid, _session_of_alias(session)))))
    lines = sorted({(n.get("line") or "未分组") for n in (view.get("nodes") or [])})
    for line in lines:
        out.append(("分组 %s" % line,
                    (lambda line=line: _tech_line_text(line, _session_of_alias(session)))))
    return out


def _report_items(state, session):
    rows = _reports(session)
    if not rows:
        return [("暂无战报",  lambda: "暂无记录：session.battle_reports 为空 还没有战报送达通达枢密。")]
    out = []
    for i, r in enumerate(rows):
        name = "%s %s %s" % (_txt((r or {}).get("date")) if isinstance(r, dict) else "",
                             _txt((r or {}).get("province_name")) if isinstance(r, dict) else "",
                             "第 %d 份" % (i + 1))
        out.append((name.strip(),
                    (lambda i=i: _report_text(i, _session_of_alias(session)))))
    return out


def _mainline_items(state, session):
    try:
        rows = _mainline_texts(session)
    except Exception as exc:
        return [("要紧事务", (lambda: "该项取数失败：load_mainlines_data 出错 %s: %s"
                             % (type(exc).__name__, exc)))]
    if not rows:
        return [("暂无要紧事务",  lambda: "暂无记录：world/mainlines.json 中没有要紧事务。")]
    return [(title, (lambda text=text: text)) for title, text in rows]


def _country_items(state, session):
    return [(n, (lambda n=n: _country_text(n, _session_of_alias(session))))
            for n in _country_names()]


def _province_items(state, session):
    from .localdata import under_ming_control
    st = _state_of(session)
    out = []
    for pid, p in (getattr(st, "provinces", None) or {}).items():
        try:
            if not under_ming_control(p):
                continue
        except Exception as exc:
            out.append(("%s 取数失败" % pid,
                        (lambda pid=pid, exc=exc: "该项取数失败：under_ming_control(%s) 出错 %s: %s"
                         % (pid, type(exc).__name__, exc))))
            continue
        label = "%s %s" % (_txt((p or {}).get("name") or pid), pid)
        out.append((label, (lambda pid=pid: _province_line(pid, _session_of_alias(session)))))
    return out


def _palace_items(state, session):
    try:
        names = _palace_names()
    except Exception as exc:
        return [("紫禁城设施", (lambda: "该项取数失败：png/room.txt 读取出错 %s: %s"
                             % (type(exc).__name__, exc)))]
    return [(n, (lambda n=n: _dept_text(n, _state_of(session)))) for n in names]


DOMAINS: Dict[str, Dict[str, Any]] = {
    "六部": {
        "title": "六部",
        "desc": "吏部 户部 礼部 兵部 刑部 工部，共六个平铺入口：每一部就是一个界面，没有下级界面。"
                "清吏司、库局等名目只写在各部正文的沿革与职能里。",
        "items": lambda state, session: _dept_items(SIX_DEPTS, session),
    },
    "外诸司": {
        "title": "外诸司",
        "desc": "内阁 都察院 六科给事中 通政使司 大理寺 各寺 国子监 翰林院 詹事府 太医院 钦天监 宗人府。",
        "items": lambda state, session: _dept_items(WAIZHUSI, session),
    },
    "二十四衙门": {
        "title": "二十四衙门",
        "desc": "内廷十二监、四司、八局 司礼监 内官监 御用监 司设监 御马监 神宫监 尚膳监 尚宝监 印绶监 直殿监 尚衣监 都知监 惜薪司 钟鼓司 宝钞司 混堂司 兵仗局 银作局 浣衣局 巾帽局 针工局 内织染局 酒醋面局 司苑局。",
        "items": lambda state, session: _dept_items(NEITING, session),
    },
    "紫禁城设施": {
        "title": "紫禁城设施",
        "desc": "紫禁城地图上标注的 20 处殿宇宫苑 名册唯一来源 png/room.txt。设施从紫禁城地图点入，此处只作档案检索。",
        "items": _palace_items,
    },
    "军队名册": {
        "title": "军队名册",
        "desc": "全国军队名册 world/armies.json，子条目为全部部队，含归属、类别、驻地、任务与装备编成。",
        "items": _army_items,
    },
    "人物": {
        "title": "人物",
        "desc": "在职文官名册、武官名册与人物卡 含性格、生平、可变状态。",
        "items": _people_items,
    },
    "科技": {
        "title": "科技",
        "desc": "两张科技树 理论科学、科学技术共 252 条，另有按线分组 数学 物理 化学 生物 医学 工学 工业 地理 科学 管理学 军事等。",
        "items": _tech_items,
    },
    "战报": {
        "title": "战报",
        "desc": "已送达通达枢密的战报 session.battle_reports：省份、性质、日期、战况过程与双方损失。",
        "items": _report_items,
    },
    "对话查阅": {
        "title": "对话查阅",
        "desc": "召对、廷议、世界总结、中旨四类对话归档与事件树。",
        "items": _dialogue_items,
    },
    "国家": {
        "title": "国家",
        "desc": "各势力 明 满清 蒙古诸部 诸部 叛军与礼部外交对象 朝鲜 东番 日本 满清；每国含名册部队与其控制省份的地方数据。",
        "items": _country_items,
    },
    "要紧事务": {
        "title": "要紧事务",
        "desc": "主线剧本档案 world/mainlines.json：结束条件、长期剧本、预设与已发生事件、阶段划分。",
        "items": _mainline_items,
    },
    "章奏": {
        "title": "章奏",
        "desc": "通政使司在册章奏 session.memorials：编号、标题、体裁与内容分类、事发地、正文、票拟与处置结果。",
        "items": lambda state, session: _memorial_items(session),
    },
    "地方情况": {
        "title": "地方情况",
        "desc": "当前属明朝控制的省份 控制权为明，含人口、腐败、供需、税收、天气、阶级、工程、驻军。",
        "items": _province_items,
    },
}


# ═══════════════════════════ 对外接口：目录与取条目 ═══════════════════════════

def catalog_text() -> str:
    """域目录：给 AI 看的购物清单 域键、中文标题、说明。"""
    try:
        lines = ["【剧本数据库域目录】共 %d 个域。用 list_database_items 看某域的子条目，"
                 "用 search_database 按关键词检索内容。" % len(DOMAINS)]
        for key, spec in DOMAINS.items():
            lines.append("%s %s：%s" % (key, spec.get("title", key), spec.get("desc", "")))
        out = "\n".join(lines)
        if len(out) > CATALOG_LIMIT:
            out = out[:CATALOG_LIMIT] + "\n 域目录已截断"
        return out
    except Exception as exc:
        return "目录生成失败：%s: %s" % (type(exc).__name__, exc)


def _domain_check(domain: str) -> str:
    """域键校验：未知域返回可读提示，合法域返回空串。"""
    if domain in DOMAINS:
        return ""
    return "未知域：%s，可用域：%s" % (domain, "、".join(DOMAINS.keys

))
def _fetch_items(state, session, domain: str) -> Tuple[List[Tuple[str, Callable[[], str]]], str]:
    """取某域条目表：返回  条目列表, 错误说明。错误说明非空时列表不可用。"""
    spec = DOMAINS[domain]
    fn = spec["items"]
    try:
        items = fn(state, session)
    except Exception as exc:
        return [], "该项取数失败：域 %s 的条目表取数出错 %s: %s" % (
            domain, type(exc).__name__, exc)
    out = []
    for it in (items or []):
        try:
            name, getter = it
        except (TypeError, ValueError):
            out.append((_txt(it), (lambda it=it: _txt(it))))
            continue
        out.append((_txt(name), getter))
    return out, ""


def _safe_text(getter: Callable[[], str]) -> str:
    """取条目正文：任何异常都变成可读的失败说明，绝不外抛。"""
    try:
        return _txt(getter())
    except Exception as exc:
        return "该项取数失败：%s: %s" % (type(exc).__name__, exc)


def list_items(state, session, domain: str) -> str:
    """列出某域的所有子条目名 不取正文，正文请用 search_database 或再次查询。"""
    try:
        d = (domain or "").strip()
        if not d:
            return "未指定域。可用域：%s" % "、".join(DOMAINS.keys())
        bad = _domain_check(d)
        if bad:
            return bad
        items, err = _fetch_items(state, session, d)
        if err:
            return err
        lines = ["【%s %s】共 %d 个条目" % (DOMAINS[d].get("title", d), d, len(items))]
        lines.append(DOMAINS[d].get("desc", ""))
        for i, (name, _getter) in enumerate(items, 1):
            lines.append("%d. %s" % (i, name))
        out = "\n".join(lines)
        if len(out) > LIST_LIMIT:
            out = out[:LIST_LIMIT] + "\n 条目列表已截断，只显示前 %d 字" % LIST_LIMIT
        return out
    except Exception as exc:
        return "列条目失败：%s: %s" % (type(exc).__name__, exc)


# ═══════════════════════════ 对外接口：搜索 ═══════════════════════════

def _parse_keywords(raw: Any) -> List[str]:
    """关键词串或表 -> 词表 空格/顿号/逗号/分号都算分隔符。"""
    if isinstance(raw, (list, tuple)):
        parts = []
        for x in raw:
            parts.extend(_parse_keywords(x))
        return parts
    text = _txt(raw)
    if not text.strip():
        return []
    return [w for w in re.split(r"[\s、,，;；]+", text.strip()) if w]


def _find_spans(text: str, words: List[str]) -> Tuple[List[int], List[str]]:
    """返回  命中位置表, 未命中词表。全部词都出现才算命中 由调用方判断。"""
    low = text.lower()
    spans = []
    missing = []
    for w in words:
        idx = low.find(w.lower())
        if idx < 0:
            missing.append(w)
        else:
            spans.append(idx)
    return spans, missing


def _extract_summary(text: str, words: List[str], limit: int = SUMMARY_CHARS) -> str:
    """摘出最相关的一段 尽量覆盖关键词，约 limit 字。"""
    hits = [w for w in words if text.lower().find(w.lower()) >= 0]
    start = 0
    if hits:
        starts = [max(0, text.lower().find(w.lower()) - limit // 4) for w in hits]
        start = min(starts)
    seg = " ".join(text[start:start + limit * 2].split())
    return _cut(seg, limit)


def _run_search(state, session, queries) -> Dict[str, Any]:
    """按查询数组逐条检索，返回**结构化**结果 文字由 render_search 负责。

    结构：{"blocks": [{"keywords": [...], "domain": str, "lines": [...],
                      "hits": int, "failed": int}], "truncated": bool, "timed_out": bool}
    查询格式不对时不抛异常，而是记进该块的 lines 一条查询写坏不该让整次检索失败。
    """
    if isinstance(queries, dict):
        queries = [queries]
    elif isinstance(queries, str):
        queries = [{"keywords": queries}]
    elif not isinstance(queries, (list, tuple)):
        return "关键词格式不对：queries 应为数组，每项形如 {\"keywords\": \"陕西 大旱\", \"domain\": \"地方情况\"}"
    queries = list(queries)[:MAX_SEARCH_QUERIES]
    if not queries:
        return "未提供关键词。可用域：%s" % "、".join(DOMAINS.keys())

    started = time.time()
    out_blocks: List[Dict[str, Any]] = []
    total = 0
    truncated = False
    timed_out = False

    for q in queries:
        if not isinstance(q, dict):
            bad = "【检索 %s】\n查询格式不对：每项应为 {\"keywords\": \"...\", \"domain\": \"...\"}" % _txt(q)
            out_blocks.append({"keywords": [], "domain": "", "text": bad,
                               "lines": [bad], "hits": 0, "failed": 0, "cut": False})
            continue
        words = _parse_keywords(q.get("keywords"))
        domain = _txt(q.get("domain")).strip()
        head = "【检索 关键词「%s」域「%s」】" % (" ".join(words) or "未提供", domain or "全部域")
        if not words:
            bad = head + "\n未提供关键词：keywords 用空格分隔多个词，全部命中才算命中。"
            out_blocks.append({"keywords": [], "domain": domain, "text": bad,
                               "lines": [bad], "hits": 0, "failed": 0, "cut": False})
            continue

        if domain:
            bad = _domain_check(domain)
            if bad:
                text = head + "\n" + bad
                out_blocks.append({"keywords": words, "domain": domain, "text": text,
                                   "lines": [text], "hits": 0, "failed": 0, "cut": False})
                continue
            targets = [domain]
        else:
            targets = list(DOMAINS.keys())

        lines = [head]
        hit_count = 0
        fail_count = 0
        block_budget = min(BLOCK_LIMIT, SEARCH_LIMIT - total)
        used = len(head)
        notes = []

        def _push(line: str) -> bool:
            """块内写入：超出本条 query 的预算就停手并记一条说明。"""
            nonlocal used
            if used + len(line) + 1 > block_budget:
                return False
            lines.append(line)
            used += len(line) + 1
            return True

        for d in targets:
            if time.time() - started > SEARCH_SECONDS:
                timed_out = True
                left = targets[targets.index(d):]
                notes.append(" 取数已用 %d 秒，以下域本次未检索：%s" % (
                    int(time.time() - started), "、".join(left)))
                break
            items, err = _fetch_items(state, session, d)
            if err:
                if not _push("[%s] %s" % (d, err)):
                    notes.append (" 本域条目较多，本次只显示到此处；请加关键词或用 domain 收窄后重查")
                    break
                fail_count += 1
                continue
            for name, getter in items:
                text = _safe_text(getter)
                if text.startswith ("该项取数失败"):
                    fail_count += 1
                    if not _push("[%s] %s：%s" % (d, name, _cut(text, 120))):
                        notes.append (" 本域条目较多，本次只显示到此处；请加关键词或用 domain 收窄后重查")
                        break
                    continue
                spans, missing = _find_spans(text, words)
                if missing:
                    continue
                hit_count += 1
                summary = _extract_summary(text, words)
                if not _push("[%s] %s 命中 %d 次，全文 %d 字\n  %s" % (
                        d, name, len(spans), len(text), summary)):
                    notes.append (" 本域条目较多，本次只显示到此处；请加关键词或用 domain 收窄后重查")
                    break
        if hit_count == 0 and fail_count == 0:
            lines.append ("本域检索无命中。")
        elif hit_count == 0:
            lines.append ("本域检索无命中 另有 %d 项取数失败。" % fail_count)
        else:
            lines.append("共命中 %d 条 另有 %d 项取数失败。" % (hit_count, fail_count))
        lines.extend(notes)

        block_text = "\n".join(lines)
        if total + len(block_text) > SEARCH_LIMIT:
            remain = max(0, SEARCH_LIMIT - total)
            kept = block_text[:remain] if remain > 0 else ""
            out_blocks.append({"keywords": words, "domain": domain, "text": kept,
                               "lines": lines, "hits": hit_count, "failed": fail_count,
                               "cut": True})
            total += len(kept)
            truncated = True
            break
        out_blocks.append({"keywords": words, "domain": domain, "text": block_text,
                           "lines": lines, "hits": hit_count, "failed": fail_count,
                           "cut": False})
        total += len(block_text)

    return {"blocks": out_blocks, "truncated": truncated, "timed_out": timed_out}


def render_search(value: Dict[str, Any]) -> str:
    """把结构化检索结果渲染成模型看到的文字 与旧输出一致，含截断与超时说明。"""
    out = "\n\n".join(str(b.get("text") or "") for b in (value.get("blocks") or []))
    if value.get("truncated"):
        out += "\n\n 单次搜索总输出上限约 %d 字，已截断。请缩小域或增删关键词后再查。" % SEARCH_LIMIT
    if value.get("timed_out"):
        out += "\n 取数时间上限 %d 秒，本次搜索已提前收尾；请指定 domain 或减少关键词后重查。" % int(SEARCH_SECONDS)
    return out


def search(state, session, queries) -> str:
    """检索结果文字 对外旧接口：内部走结构化再渲染，口径与工具一致。"""
    try:
        return render_search(_run_search(state, session, queries))
    except Exception as exc:
        return "搜索失败：%s: %s" % (type(exc).__name__, exc)
