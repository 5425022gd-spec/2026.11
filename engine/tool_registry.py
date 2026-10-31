# -*- coding: utf-8 -*-
"""工具表：全站 AI 工具的单一清单。

背景：工具原先散在 9 个模块里，靠各调用点自己拼列表（`DATA_TOOLS`、
`battle_tools.TOOLS`、各 `*_tool()` 工厂），谁被哪个系统看到只能逐个 grep。
这里把「有哪些工具、谁造的、给哪个系统用」集中成一张表，新增工具必须先登记。

表只描述**元数据**，不复制实现：`factory` 指向真正的构造点，`tools_for()` 现取。
"""
from typing import Any, Dict, List, Optional

# ═════════════════════════════ 工具表 ═════════════════════════════
# 字段：
#   name    工具名（模型看到的名字，必须与 define_tool 的第一个参数一致）
#   kind    query（取数） / finish（交卷）
#   module  定义所在模块
#   factory 构造函数名；不带参数的可调用对象，返回 define_tool 的产物
#   systems 哪些系统会给模型这套工具（对应 run_tool_workflow 的调用点）
#   note    一句话说明；重复实现、已知限制都写在这里
TOOL_TABLE: List[Dict[str, Any]] = [
    # ── 取数 ──
    {"name": "query_data", "kind": "query", "module": "engine.dataview",
     "factory": "_query_data", "systems": ["council"],
     "note": "按「全局／省名」取整块数据"},
    {"name": "search_database", "kind": "query", "module": "engine.script_tools",
     "factory": "_search_database", "systems": ["script", "memorial", "council"],
     "note": "按关键词检索域内条目"},
    {"name": "list_database_items", "kind": "query", "module": "engine.script_tools",
     "factory": "_list_database_items", "systems": ["script", "memorial", "council"],
     "note": "列某域的子条目名，不含正文"},
    {"name": "list_variations", "kind": "query", "module": "engine.memorial_tools",
     "factory": "_list_variations", "systems": ["memorial"],
     "note": "近期变化点清单"},
    {"name": "get_variation_scope", "kind": "query", "module": "engine.memorial_tools",
     "factory": "_get_variation_scope", "systems": ["memorial"],
     "note": "变化点绑定的系统超控范围"},
    {"name": "get_reward_rules", "kind": "query", "module": "engine.memorial_tools",
     "factory": "_get_reward_rules", "systems": ["memorial"],
     "note": "系统奖惩规则包"},
    {"name": "get_operation_rules", "kind": "query", "module": "engine.zhongzhi_tools",
     "factory": "_get_operation_rules", "systems": ["zhongzhi"],
     "note": "中旨指令全集与合法取值"},
    {"name": "try_operation", "kind": "query", "module": "engine.zhongzhi_tools",
     "factory": "_try_operation", "systems": ["zhongzhi"],
     "note": "试跑一条中旨指令拿回执"},
    {"name": "get_faction_rules", "kind": "query", "module": "engine.faction_ai",
     "factory": "_operation_rules_tool", "systems": ["faction"],
     "note": "国家 AI 本季度可用指令（与中旨那套分开，参数不同）"},
    {"name": "try_faction_operation", "kind": "query", "module": "engine.faction_ai",
     "factory": "_try_operation_tool", "systems": ["faction"],
     "note": "试跑一条国家 AI 指令；参数是 command + faction"},

    # ── 交卷 ──
    {"name": "finish_memorial", "kind": "finish", "module": "engine.memorial_tools",
     "factory": "_finish_memorial", "systems": ["memorial"], "note": "章奏"},
    {"name": "finish_battle", "kind": "finish", "module": "engine.battle_tools",
     "factory": "TOOLS", "systems": ["battle"], "note": "战报（模块以列表形式导出）"},
    {"name": "finish_minutes", "kind": "finish", "module": "engine.minute_tools",
     "factory": "_finish_minutes", "systems": ["council"], "note": "廷议纪要"},
    {"name": "finish_person_card", "kind": "finish", "module": "engine.person_tools",
     "factory": "_finish_person_card", "systems": ["person"], "note": "人物档案"},
    {"name": "finish_script", "kind": "finish", "module": "engine.script_tools",
     "factory": "_finish_script", "systems": ["script"], "note": "剧本"},
    {"name": "submit_script_end", "kind": "finish", "module": "engine.script_tools",
     "factory": "end_tool", "systems": ["script"], "note": "剧本收束，经 {{toolA}} 注入"},
    {"name": "finish_command", "kind": "finish", "module": "engine.zhongzhi_tools",
     "factory": "_finish_command", "systems": ["zhongzhi", "faction"], "note": "中旨／国家 AI 指令"},
    {"name": "submit_speeches", "kind": "finish", "module": "engine.submit_tools",
     "factory": "speeches_tool", "systems": ["council"], "note": "廷议发言；条数＝本轮被点名人数，姓名不在名单内会被拒"},
    {"name": "submit_monthly_summary", "kind": "finish", "module": "engine.submit_tools",
     "factory": "monthly_tool", "systems": ["monthly"], "note": "月度世界总结"},
    {"name": "submit_refusal", "kind": "finish", "module": "engine.submit_tools",
     "factory": "refusal_tool", "systems": ["zhongzhi"], "note": "机构抗疏"},
    {"name": "submit_quarter_plan", "kind": "finish", "module": "engine.submit_tools",
     "factory": "quarter_tool", "systems": ["faction"], "note": "国家 AI 季度方针"},
]


def table() -> List[Dict[str, Any]]:
    """整张表（副本，避免调用方改到常量）。"""
    return [dict(row) for row in TOOL_TABLE]


def by_name(name: str) -> Optional[Dict[str, Any]]:
    for row in TOOL_TABLE:
        if row["name"] == name:
            return dict(row)
    return None


def for_system(system: str) -> List[Dict[str, Any]]:
    """某系统用到的工具行。"""
    return [dict(r) for r in TOOL_TABLE if system in (r.get("systems") or [])]


def resolve(row: Dict[str, Any]) -> Any:
    """把一行解析成真正的工具对象；解析不到返回 None。"""
    import importlib
    try:
        mod = importlib.import_module(row["module"])
        obj = getattr(mod, row["factory"])
    except (ImportError, AttributeError):
        return None
    return obj() if callable(obj) and not isinstance(obj, list) else obj


def verify() -> List[str]:
    """自检：每条工厂都能解析、且工具名与登记名一致。返回问题清单。"""
    import importlib
    problems: List[str] = []
    seen: Dict[str, str] = {}
    for row in TOOL_TABLE:
        name = row["name"]
        if name in seen:
            problems.append("重名：%s（%s 与 %s）" % (name, seen[name], row["module"]))
        seen[name] = row["module"]
        try:
            mod = importlib.import_module(row["module"])
            obj = getattr(mod, row["factory"])
        except (ImportError, AttributeError) as e:
            problems.append("%s：取不到 %s.%s（%s）" % (name, row["module"], row["factory"], e))
            continue
        try:
            tools = obj if isinstance(obj, list) else ([obj()] if callable(obj) else [obj])
        except Exception as e:                       # noqa: BLE001 构造失败也要报出来
            problems.append("%s：构造失败 %s" % (name, e))
            continue
        names = [t.get("name") for t in tools if isinstance(t, dict)]
        if name not in names:
            problems.append("%s：构造出的工具名是 %s，与登记不一致" % (name, names))
    return problems


def describe() -> str:
    """给人看的一行一工具清单。"""
    lines = ["%-24s %-7s %-26s %s" % ("工具", "类别", "定义处", "系统")]
    for row in TOOL_TABLE:
        lines.append("%-24s %-7s %-26s %s" % (
            row["name"], row["kind"], "%s.%s" % (row["module"].replace("engine.", ""), row["factory"]),
            "、".join(row.get("systems") or [])))
    return "\n".join(lines)
