# -*- coding: utf-8 -*-
"""召集与廷议系统：群策群力批阅章奏 召对与要紧事物大政廷辩 廷议。

【旧代码说明】「召对」是已废弃的旧模式名，代码里残留的 council 键与注释保留原样，
玩家看到的文案一律写「廷议」。

流程与模式：
1. 旧「召对」模式 (council 键)：已废弃，针对单份章奏的研讨并入廷议；
   - 结束方式 1: 结束廷议并归档 (Prompt/meeting_2.txt) -> 归档入「廷议会话」
   - 结束方式 2: 生成批奏 (Prompt/meeting_pizou.txt) -> 归档 + 写入批奏推入事件树
2. 廷议模式 (court)：针对要紧事物 主线大政，选定重臣参与廷辩；
   - 会议进行中支持随时设置与更新【动态数据开关】(data_switches)；
   - 下一轮发言动态将勾选的数据摘要注入 Prompt/conference_2.txt；
   - 结束方式 1  直接结束: 归纳观点 (Prompt/meeting_2.txt) -> 归档入「廷议会话」，不超控；
   - 结束方式 2  决议施政: 批奏 + 输出全量超控指令  Prompt/meeting_pizou_phaseN.txt 按阶段分流 -> 逻辑引擎全量执行超控并归档入「廷议会话」。
"""

import json
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from .logger import CONSOLE
from .characters import get_character, list_callable, prompt_profile
from .dataview import DATA_TOOLS, query as dataview_query
from .llm import parse_jsonl
from .time import advance_minute
from .operations import execute_operation_command, get_all_operations_schema

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_DIR = os.path.join(_BASE, "Prompt")


def read_prompt(name: str) -> str:
    try:
        with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _ngrams(text: str, n: int = 2):
    return set(text[i:i + n] for i in range(max(0, len(text) - n + 1)))


def compute_match(text: str, pids: List[str]) -> Dict[str, float]:
    """返回 {cid: 匹配度 0-1，归一化后}。"""
    tset = _ngrams(text or "")
    scores = {}
    for cid in pids:
        c = get_character(cid)
        if not c:
            continue
        bset = _ngrams(c.get("bio", ""), 2)
        inter = len(tset & bset)
        union = len(tset | bset)
        scores[cid] = inter / union if union else 0.0
    total = sum(scores.values())
    if total <= 0 or not pids:
        return {cid: 1.0 / len(pids) for cid in pids} if pids else {}
    probs = {cid: s / total for cid, s in scores.items()}
    floor = 1.0 / (3 * len(probs))
    for _ in range(20):
        probs = {cid: max(p, floor) for cid, p in probs.items()}
        total = sum(probs.values())
        probs = {cid: p / total for cid, p in probs.items()}
        if all(p >= floor for p in probs.values()):
            break
    return probs


def weighted_pick(probs: Dict[str, float], k: int = 2) -> List[str]:
    """按概率随机选 k 个角色 去重。"""
    items = list(probs.items())
    if not items:
        return []
    picked = []
    pool = list(items)
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool)
        if total <= 0:
            break
        r = random.random() * total
        acc = 0.0
        for cid, w in pool:
            acc += w
            if r <= acc:
                picked.append(cid)
                pool = [x for x in pool if x[0] != cid]
                break
    return picked


# ═══════════════════════════════════════════════════════════
# 随堂调阅的数据：一个开关 = 一个界面的**完整内容** + 一句介绍
# 口径 玩家定义：勾了哪个界面，就把那个界面里每一栏的每一项都发出去，
# 不是只发一段摘要；预览 前端右栏与实际发给 AI 的内容走同一个生产函数。
# ═══════════════════════════════════════════════════════════

# 每个开关的一句介绍 唯一一处；前端预览与 AI 请求共用
SWITCH_INTRO: Dict[str, str] = {
    "macro": "全国宏观实录：国库、皇帝私库、地方银库、流通白银、货币健康度、皇室威望、中央腐败与党派控制的总盘数据。",
    "libu": "吏部案卷：中央腐败程度、全国各地腐败程度、行政纪律逐条与在职文官名册的完整内容。",
    "hubu": "户部账册：全国基本数据、十税制逐条税目与分摊、国库与私库收支表的完整内容。",
    "rites": "礼部文册：外交事务、贸易管理、宗教管理、国家常祀、灾异祭祀与科举教育的完整内容。",
    "war": "兵部军册：粮饷筹措、军队调动、驿站传令与武官人事的完整内容。",
    "xingbu": "刑部修正：律令与刑名逐条。",
    "works": "工部营造：器物制造、仓储库存与工程建设的完整内容。",
}

# 每个开关的标题 唯一一处
SWITCH_TITLE: Dict[str, str] = {
    "macro": "全国宏观实录",
    "libu": "吏部案卷",
    "hubu": "户部账册",
    "rites": "礼部文册",
    "war": "兵部军册",
    "xingbu": "刑部律令",
    "works": "工部营造",
}

MAX_SWITCH_CHARS = 100000     # 单个界面渲染上限 超出截断并注明（实测最大界面约 8 万字）
MAX_TOTAL_CHARS = 160000      # 一次调阅总量上限

# 军队详情开关的 id 前缀：一个开关 = 一支军队的军情界面。
# 名册是 world/armies.json 那一份，这里只拼 id，不另存一份军队清单。
ARMY_SWITCH_PREFIX = "army:"

# 键名 → 中文栏名 与界面上该栏的标题一致；键名仍是数据的唯一来源，这里只给显示名
PAYLOAD_LABELS: Dict[str, str] = {
    # 通用行
    "item": "项目", "scope": "范围", "effect": "效果", "intro": "介绍",
    "name": "名称", "value": "数值", "base": "基础值", "pct": "百分比",
    "pct_str": "百分比", "flat": "加减", "desc": "说明", "summary": "摘要",
    "total": "合计", "total_pct": "合计百分比", "value_str": "数值",
    # 宏观
    "central_corruption": "中央腐败程度", "local_corruption_common": "地方腐败通用修正",
    "discipline_rows": "行政纪律", "provinces_table": "全国各地腐败程度",
    "roster_groups": "文册档案", "roster_total": "在职地方文官",
    "basic_stats": "全国基本数据", "tax_system": "赋税体系", "taxes": "税目",
    "national_treasury": "国库收支", "privy_treasury": "私库",
    "incomes": "收入项目", "expenses": "支出项目", "transactions": "实际账单",
    "classes_table": "各阶级人口产能需求", "total_actual_pop": "全国实际人口",
    "total_physical_capacity": "全国物理产能合计", "total_merchant_capacity": "商人虚拟产能",
    "total_demand": "全国需求合计", "supply_demand_ratio": "供求指数",
    "monthly_tax": "月摊", "annual_tax": "年摊", "class_tax_burdens": "各阶级承担",
    "prov_apportionment": "各省分摊", "prov_breakdown": "各省税负汇总",
    "schedule_desc": "征收时点", "rate": "税率", "rate_display": "税率显示",
    "base_val": "税基", "category": "类别", "ratio": "占比",
    "total_monthly_revenue": "月折算岁入", "total_annual_revenue": "岁征常赋总额",
    "balance": "余额", "total_monthly_income": "月入合计", "total_annual_income": "岁入合计",
    "silver_balance": "存银", "gold_balance": "存金",
    # 礼部
    "trade": "贸易管理", "religion": "宗教管理", "education": "科举教育",
    "diplomacy": "外交事务", "sacrifices": "国家常祀", "disaster_sacrifices": "灾异祭祀",
    "religions": "各教影响", "net_prod_pct": "净产能", "net_demand_pct": "净需求",
    "prod_pct": "产能", "demand_pct": "需求", "prestige_pct": "威望",
    "total_prod_pct": "产能合计", "total_demand_pct": "需求合计", "total_prestige_pct": "威望合计",
    "local_education": "受教育人口", "higher_education": "受高等教育人口",
    "current_pop": "现有", "annual_growth_expected": "今年预计增加",
    "edu_modifiers": "修正明细", "mod_val": "修正值", "pct_mod": "百分比修正",
    "flat_mod": "加减修正", "bonus": "教育加成", "unemployment": "结构性失业",
    "net_trade_monthly": "月差额", "trade_type": "贸易状态",
    # 兵部
    "liaoxiang": "粮饷筹措", "officers": "武官人事", "officers_grouped": "武官人事分组",
    "officers_total": "武官总数", "pay_categories": "粮饷名目", "relays": "驿站传令",
    "armies": "军队调动", "dest_provinces": "可调目标省份",
    "monthly_target": "月征额", "annual_target": "年征额", "apportionment": "各省分摊",
    "production": "产能", "allocated_silver_monthly": "月摊银", "allocated_silver_annual": "年摊银",
    "matter_loss": "折物质", "penalty_desc": "扣减说明",
    "title": "职位", "faction": "党派", "location": "驻地", "start_date": "在任始",
    "province_name": "省份", "distance_km": "里程", "single_trip_days": "单程日",
    "round_trip_days": "往返日", "path_str": "路线",
    # 刑部
    "laws": "刑部修正", "law_rows": "刑部修正行",
    # 工部
    "engineering": "工程建设", "inventory": "仓储库存", "armaments": "器物制造",
    "projects": "工程条目", "total_projects": "工程数", "monthly_maintenance": "月维护",
    "maintenance_annual": "年维护费", "maintenance_monthly": "月维护费",
    "cost": "造价", "status": "状态", "kind": "建筑类型", "kind_name": "建筑类型",
    "buff": "修正", "silver": "白银", "gold": "黄金", "monthly_cost": "月生产花费",
    "unit_cost": "制造成本", "monthly_output": "月产能", "stock": "库存",
    # 地方
    "id": "编号",
    "core": "核心情况", "corruption": "腐败", "infra": "基建",
    "population": "人口", "population_exact": "人口精确", "material_production": "物质产出",
    "material_demand": "物质需求", "class_material_stock": "阶级库存合计",
    "local_market_stock": "本省未售物资", "tax_total": "税收总额", "support_total": "扶持合计",
    "classes_info": "各阶级产能明细", "modifiers": "地区修正", "province_armies": "驻军",
    "controller": "控制权归属", "base_carrying": "基础承载力", "carrying": "承载力",
    "corruption_calc": "腐败计算", "carrying_calc": "承载力计算",
    "neighbors_distances": "邻接里程", "path_to_beijing": "进京驿路",
    "classes": "阶级", "projects": "工程", "weather": "天气",
    # 计算明细与常见残余键
    "pct_sum": "百分比合计", "pct_sum_display": "百分比合计", "flat_sum": "加减合计",
    "formula": "计算式", "calculation_text": "算式", "breakdown": "逐项明细",
    "subclass": "细类", "subclass_name": "细类", "province_id": "省份ID",
    "annual_silver": "年产白银", "annual_gold": "年产黄金",
    "type": "类型", "owner": "归属", "neighbors": "邻接", "local": "地方数据",
    "net_multiplier": "净倍率", "capacity": "产能", "demand": "需求",
    "base_prod": "基础产能", "base_demand": "基础需求", "pct_buff": "百分比增益",
    "flat_buff": "固定增益", "buff_notes": "增益来源", "religion_prod_buff": "宗教产能加成",
    "army_arrears_penalty": "驻军欠饷减产", "army_arrears_matter": "驻军欠饷折物质",
    "disaster_penalty": "灾异减产", "tax_matter_loss": "税负折物质",
    "total_buff_pct": "增益合计", "total_penalty_pct": "扣减合计",
    "modifier_rows": "修正行", "project_rows": "工程行", "custom_modifiers": "独立地区修正",
    "loyalty": "忠诚度", "satisfaction": "满意度", "support": "扶持", "tax_rate": "税率",
    "pop": "人口", "output": "产出", "pop_exact": "人口精确",
    "condition": "天况", "temp": "气温", "capacity_penalty": "减产",
    "count": "人数", "elite": "精锐", "equipment": "装备", "current_location": "当前驻地",
    "mission": "任务指令", "engagement_risk": "交战风险", "retreat_priority": "撤退优先级",
    "arrears_wan": "欠饷", "pay_rate": "发饷比例", "monthly_pay": "月饷",
    "monthly_maint": "月维护", "total_friendly_men": "己方守军", "enemy": "敌对武装",
    "friendly": "己方部队", "total_enemy_men": "敌军人数",
    # 军队军情界面的其余字段 栏名与界面上一致
    "commander": "主将", "station": "营防", "extra_pay": "额外赏银",
    "owner_name": "归属", "owner": "归属标识", "overall_stance": "战守方略", "battle_order": "军令",
    "path": "行军路径", "path_names": "途经", "total_distance_km": "行军里程",
    "total_days": "预计天数", "remain_days": "剩余天数", "recon_value": "侦察值",
    "current_location_name": "当前驻地", "target_location": "目的地",
    "target_location_name": "目的地", "corruption": "腐败", "desc": "说明",
    # 装备编成的分类与型号字段
    "armors": "甲胄", "melee": "近战兵器", "ranged": "远程兵器",
    "horses": "马匹", "artillery": "火器", "era_model": "型号",
    "quota": "编制配额", "grade": "品级", "weight": "重量",
}

LIST_CAP = 40      # 单个列表最多展开多少条 其余只报总数，保证每个栏目都还能出现


def _label(key: str) -> str:
    return PAYLOAD_LABELS.get(str(key), str(key))


def _intro_of(sw: str) -> str:
    if sw in SWITCH_INTRO:
        return SWITCH_INTRO[sw]
    if str(sw).startswith(ARMY_SWITCH_PREFIX):
        return "军队详情：编制人数与装备编成、士气忠诚与欠饷、任务军令与行军路线的完整内容。"
    return "地方实录：该省的地方数据、阶级、工程、军队与修正的完整内容。"


def _army_id_of(sw: str) -> str:
    return str(sw)[len(ARMY_SWITCH_PREFIX):]


def switch_title(state: Any, sw: str) -> str:
    """开关的显示标题：固定开关用表里的名字，军队用军名，其余按省名。"""
    if sw in SWITCH_TITLE:
        return SWITCH_TITLE[sw]
    if str(sw).startswith(ARMY_SWITCH_PREFIX):
        u = _army_of(state, sw)
        return "%s军情" % (_txt_name(u) or _army_id_of(sw))
    p = (getattr(state, "provinces", {}) or {}).get(sw)
    return "%s地方实录" % (p or {}).get("name", sw)


def _txt_name(unit: Any) -> str:
    return str((unit or {}).get("name") or "")


def _army_of(state: Any, sw: str) -> Optional[Dict[str, Any]]:
    """军队开关对应的名册条目 唯一来源 world/armies.json，查不到就是 None。"""
    from .armies import get_army_by_id
    return get_army_by_id(_army_id_of(sw), state)


def switch_options(state: Any) -> List[Dict[str, str]]:
    """可调阅的数据开关总目 唯一一处：前端材料挑选界面直接用它，不再另抄一份。

    三类：全国宏观与六部案卷、各省地方实录、**每支军队的军情详情**
    名册与省份都取自引擎自己的那一份数据，界面上的名字也由这里给。
    """
    opts: List[Dict[str, str]] = []
    for k in ("macro", "libu", "hubu", "rites", "war", "xingbu", "works"):
        opts.append({"id": k, "name": SWITCH_TITLE[k],
                     "grp": "宏观" if k == "macro" else "六部"})
    for pid, p in (getattr(state, "provinces", {}) or {}).items():
        opts.append({"id": pid, "name": (p or {}).get("name", pid), "grp": "地方"})
    from .armies import get_all_armies
    for u in get_all_armies(state):
        opts.append({"id": ARMY_SWITCH_PREFIX + str(u.get("id")),
                     "name": ("%s %s" % (u.get("owner_name") or "", u.get("name") or u.get("id"))).strip(),
                     "grp": "军队"})
    return opts


def _render_value(v: Any, indent: str, out: List[str], depth: int) -> None:
    """把一个值展成可读文本 dict/list 递归；键名按 PAYLOAD_LABELS 换成界面上的中文栏名。"""
    if depth > 6:
        out.append(indent + str(v)[:200])
        return
    if isinstance(v, dict):
        seen = set()
        for k, val in v.items():
            # 各省分摊明细每个省自己的实录里都有一份，这里不再重复投递
            if str(k) == "prov_apportionment":
                continue
            # 一律按名称引用：id / *_id 不再打印进材料（引擎内部照旧用它们）
            if str(k) == "id" or str(k).endswith("_id"):
                continue
            # 同名同值只列一次：载荷里 kind/kind_name、owner/owner_name、pct_sum/pct_sum_display
            # 这类同义字段是给界面用的，送到材料里重复念一遍没有意义
            if not isinstance(val, (dict, list)):
                mark = (_label(k), str(val))
                if mark in seen:
                    continue
                seen.add(mark)
                # 同一个值已经有 *_id 字段念过了，就不再念一遍 id
                if str(k) == "id" and any(
                        str(k2).endswith("_id") and str(v2) == str(val)
                        for k2, v2 in v.items() if not isinstance(v2, (dict, list))):
                    continue
            if isinstance(val, (dict, list)):
                if not val:
                    continue
                out.append("%s%s：" % (indent, _label(k)))
                _render_value(val, indent + "  ", out, depth + 1)
            else:
                out.append("%s%s：%s" % (indent, _label(k), val))
    elif isinstance(v, list):
        shown = v[:LIST_CAP]
        for i, item in enumerate(shown):
            if isinstance(item, dict):
                flat = "，".join("%s：%s" % (_label(k), v2) for k, v2 in item.items()
                                 if not isinstance(v2, (dict, list))
                                 and str(k) != "id" and not str(k).endswith("_id")
                                 and not (str(k) == "id" and any(
                                     str(k3).endswith("_id") and str(v3) == str(v2)
                                     for k3, v3 in item.items()
                                     if not isinstance(v3, (dict, list)))))
                out.append("%s%s" % (indent, flat or item))
                for k, v2 in item.items():
                    if isinstance(v2, (dict, list)) and v2:
                        out.append("%s  %s：" % (indent, _label(k)))
                        _render_value(v2, indent + "    ", out, depth + 1)
            elif isinstance(item, list):
                _render_value(item, indent + "  ", out, depth + 1)
            else:
                out.append("%s%s" % (indent, item))
        if len(v) > len(shown):
            out.append("%s 本栏共 %d 条，以上为前 %d 条，其余从略" % (indent, len(v), len(shown)))


def _render_payload(payload: Any, limit: int = MAX_SWITCH_CHARS) -> str:
    out: List[str] = []
    _render_value(payload, "", out, 0)
    text = "\n".join(out)
    if len(text) > limit:
        text = text[:limit] + "\n 内容过长，已截断至 %d 字" % limit
    return text


def switch_payload(state: Any, session: Any, sw: str) -> Any:
    """该开关对应的界面**完整载荷** 与前端该界面同一个数据源。"""
    if sw == "macro":
        from .economy import calc_liquidity_health, calc_national_demand
        from .factions import calc_overall_faction_control, calc_cabinet_faction_distribution
        from .depts_libu import calc_central_corruption
        from .depts_rites import calc_prestige
        nat_dem = calc_national_demand(state)
        return {
            "国库现银": round(float(state.treasury), 2),
            "私库": round(float(state.privy_treasury), 2),
            "地方银库窖藏": round(float(state.local_reserve), 2),
            "民间流通M1": round(float(state.m1), 2),
            "广义M2": state.m2,
            "流动性健康指数g": round(calc_liquidity_health(float(state.m1), float(state.financial_rate), nat_dem), 4),
            "全国物质总需求G": round(nat_dem, 2),
            "金融替代率f": round(float(state.financial_rate), 4),
            "皇室威望": calc_prestige(state)["value"],
            "中央腐败程度": calc_central_corruption(state)["value"],
            "中央党派控制分布": calc_overall_faction_control(state).get("ratios", {}),
            "内阁党派控制分布": calc_cabinet_faction_distribution(state).get("ratios", {}),
        }
    if sw == "libu":
        from .depts_libu import get_libu_overview
        return get_libu_overview(state)
    if sw == "hubu":
        from .depts_hubu import get_hubu_overview
        return get_hubu_overview(state)
    if sw == "rites":
        from .depts_rites import get_rites_overview
        return get_rites_overview(state)
    if sw == "war":
        from .depts_war import get_war_overview
        return get_war_overview(state)
    if sw == "xingbu":
        from .depts_xingbu import get_xingbu_overview
        return get_xingbu_overview(state)
    if sw == "works":
        from .depts_works import get_works_overview
        return get_works_overview(state)
    if str(sw).startswith(ARMY_SWITCH_PREFIX):
        army = _army_of(state, sw)
        if army is None:
            raise KeyError("调阅的军队不存在：%s" % _army_id_of(sw))
        return army
    if sw in getattr(state, "provinces", {}):
        from .localdata import build_province_view
        return build_province_view(state, sw)
    raise KeyError ("未知的数据开关：%s" % sw)


def data_preview(state: Any, session: Any, sw: str) -> str:
    """一个开关的调阅内容：一句介绍 + 该界面完整内容 前端预览与实际发送共用这一份。"""
    head = "【%s】%s" % (switch_title(state, sw), _intro_of(sw))
    return head + "\n" + _render_payload(switch_payload(state, session, sw))


def extract_context_data(state: Any, switches: List[str],
                         memorials_data: Optional[List[Dict[str, Any]]] = None,
                         session: Any = None) -> str:
    """提取随堂调阅的数据：勾选的每个界面发**它的完整内容** 每栏每项，前面加一句介绍。

    全国宏观 macro恒为强制项。导入的章奏另算 见参数 memorials_data。
    """
    sw_list = [str(x) for x in (switches or [])]
    if "macro" not in sw_list:
        sw_list.insert(0, "macro")

    parts: List[str] = []
    total = 0
    for sw in sw_list:
        try:
            text = data_preview(state, session, sw)
        except KeyError as e:
            parts.append ("【调阅失败】%s" % e)
            continue
        if total + len(text) > MAX_TOTAL_CHARS:
            parts.append (" 调阅总量已达上限 %d 字，其余开关未发送" % MAX_TOTAL_CHARS)
            break
        total += len(text)
        parts.append(text)

    if memorials_data:
        m_lines = ["【随堂调阅今日重要章奏汇编】"]
        for m in memorials_data:
            m_lines.append("《%s》 %s%s：\n  %s\n  [票拟] %s"
                           % (m.get("title"), m.get ("from", "地方", m.get("Location", "")),
                              m.get("content", ""),
                              m.get("draft") or m.get("selected_draft") or m.get("draft_processed") or ""))
        parts.append("\n".join(m_lines))

    return "\n\n".join(parts)

    return "\n\n".join(parts) if parts else " 暂无特定匹配业务数据"


def extract_books(state: Any, book_ids: Optional[List[str]] = None) -> str:
    """提取本次会议调阅的文华殿藏书 只有已收录进文华殿的书能调阅，见 engine/wenhua.py。"""
    from .wenhua import owned_books
    owned = {b.get("id"): b for b in owned_books(state)}
    lines = []
    for bid in (book_ids or []):
        b = owned.get(bid)
        if b is None:
            raise ValueError ("调阅的书籍不在文华殿藏书中：%s" % bid)
        desc = b.get("desc", "")
        if desc.startswith(b.get("name", "")):
            desc = desc[len(b.get("name", "")):].lstrip("，,、 ")
        lines.append("《%s》%s" % (b.get("name", ""), desc))
    if not lines:
        return "本次会议未调阅文华殿藏书。"
    return "\n".join(lines)


def extract_reports(session: Any, report_indexes: Optional[List[Any]] = None) -> str:
    """提取本次会议调阅的战报 {{report}} 的生产方。

    战报取自会话的战报归档 `session.battle_reports` 最近的在最前。
    `report_indexes` 是前端勾选的序号 0 起，即列表下标；空＝未调阅。
    """
    reports = list(getattr(session, "battle_reports", None) or [])
    lines = []
    for idx in (report_indexes or []):
        try:
            i = int(idx)
        except (TypeError, ValueError):
            raise ValueError ("战报序号必须是整数：%r" % idx)
        if i < 0 or i >= len(reports):
            raise ValueError("调阅的战报不存在：序号 %s 当前共 %d 份" % (i, len(reports)))
        b = reports[i]
        rep = b.get("report") or {}
        lines.append("【%s%s%s】" % (b.get("date", ""), b.get("province_name", ""),
                                      b.get("nature", "")))
        lines.append("交战：%s 对 %s" % ("、".join(b.get("friendly_names") or []) or "无",
                                        "、".join(b.get("enemy_names") or []) or "无"))
        if rep.get("battle_process"):
            lines.append ("战况：%s" % rep["battle_process"])
        for u in (rep.get("units") or []):
            lines.append("%s 阵亡 %s 人，精锐 %s" % (u.get("unit_name", ""),
                                                     u.get("deaths", 0),
                                                     u.get("elite_degradation", "")))
        arch = rep.get("architecture_result") or {}
        if arch.get("architecture_name"):
            lines.append("建筑争夺：%s，控制权变化 %s" % (arch.get("architecture_name"),
                                                          arch.get("control_change", "")))
    if not lines:
        return "本次会议未调阅战报。"
    return "\n".join(lines)


class Council:
    def __init__(self, session, llm=None, state=None):
        self.session = session
        self.llm = llm or getattr(session, "llm", None)
        self.state_ref = state
        self.active = False
        self.mode = "council"               # "council" 旧召对（废弃） | "court" 廷议 | "achievement" 成就廷议
        self.achievement_id = None         # 成就廷议对应的成就 id
        self.memorial_id = None            # 议题ID  章奏ID 或 要紧事物ID
        self.mainline_id = None            # 要紧事物ID  廷议专用
        self.participants = []             # [cid, ...]
        self.probs = {}                    # {cid: 0-1}
        self.messages = []                 # [{id, role, name, text}]
        self.msg_seq = 0
        self.last_picked = []              # 上一轮发言角色 [cid, ...]
        self.next_picked = []              # 预选/点名下一轮角色 [cid, ...]
        self.data_switches = []            # 动态数据开关 ["libu", "hubu", "henan", ...]
        self.imported_memorial_ids = []    # 导入的章奏ID清单 ["m-1", "m-2", ...]
        self.book_ids = []                 # 调阅的文华殿藏书ID清单 ["book_tech_01", ...]
        self.report_ids = []               # 调阅的战报序号清单 [0, 2, ...] battle_reports 下标
        # 廷议变体的两个开关（近侍会话 / 剧本对话都是它的变体）
        self.cast_source = "player_pick"   # player_pick | attendant_circles | event_cast
        # 结束方式只有一种：生成会议纪要并归档。廷议 / 近侍会话 / 剧本对话都一样。

    def dialogue_prompt(self, prompt_file: str, script: str = "",
                        player_line: str = "") -> str:
        """生成近侍会话 / 剧本对话的提示词：把 {{members}} 等变量真正填进去。

        与廷议共用同一份取数：members 是姓名+官职，speaker_card 是本轮开口者的
        角色卡+可选表情（等价于廷议的 random_selection）。
        """
        from .council import read_prompt as _read, _ensure_timestamp, extract_context_data
        from .hum import imported as _imported_exprs
        NL = chr(10)
        tpl = _read(prompt_file)
        if not tpl:
            return ""
        picked = self.last_picked or self.participants[:1]
        speaker = get_character(picked[0]) if picked else None
        names = [get_character(p) for p in self.participants]
        members = NL.join("%s %s" % (c["name"], c.get("title", "")) for c in names if c)
        card = ""
        if speaker:
            ex = _imported_exprs(speaker["name"])
            expr_line = (("可选表情：%s（表情变了才在 expr 里给一个）" % "、".join(ex)) if ex
                         else "该角色未导入任何表情图，本轮不要写 expr 字段。")
            card = prompt_profile(speaker) + NL + expr_line
        conv = NL.join("[%s] %s" % (m.get("name", ""), m.get("text", ""))
                       for m in self.messages)
        from .session import extract_history_12
        state = self.state_ref or getattr(self.session, "state", None)
        from .session import extract_history_all, extract_history_1, extract_history_6
        reports_text = (extract_reports(self.session, self.report_ids)
                        if self.report_ids else "本次未调阅战报。")
        out = (tpl.replace("{{members}}", members)
                  .replace("{{random_selection}}", card)
                  .replace("{{script}}", script or "")
                  .replace("{{conversation}}", conv)
                  .replace("{{history}}", extract_history_all(state) if state else "")
                  .replace("{{history_1}}", extract_history_1(state) if state else "")
                  .replace("{{history_6}}", extract_history_6(state) if state else "")
                  .replace("{{history_12}}", extract_history_12(state) if state else "")
                  .replace("{{book}}", extract_books(state, self.book_ids) if state else "")
                  .replace("{{ report}}", reports_text)
                  .replace("{{report}}", reports_text)
                  .replace("{{context_data}}",
                           extract_context_data(state, self.data_switches, session=self.session)
                           if state else ""))
        if player_line:
            out = out + NL + NL + "<player_line>" + NL + player_line + NL + "</player_line>"
        return _ensure_timestamp(out, state.date) if state else out

    def next_dialogue(self, prompt_file: str = "attendant_talk.txt",
                      script: str = "", player_line: str = "") -> Tuple[bool, str]:
        """推进一轮会话（近侍会话 / 剧本对话共用）。

        抽人沿用廷议的 _select_speakers()；工具与廷议同一套取数 + submit_speeches 交卷。
        近侍会话不传 script；剧本对话传事件里的剧本正文。
        """
        if not self.active:
            return False, "未在会话中"
        # 玩家这句话要进对话记录，否则归档里只有臣子说过什么、没有皇帝问过什么
        if player_line:
            self._add("player", "崇祯", player_line)
        picked = self._select_speakers()
        if not picked:
            return False, "无发言人选"
        self.last_picked = picked
        prompt = self.dialogue_prompt(prompt_file, script, player_line)
        if not prompt:
            return False, "缺提示词：Prompt/%s" % prompt_file

        from .ai_workflow import run_tool_workflow
        from .dataview import DATA_TOOLS
        from .script_tools import _search_database, _list_database_items
        from .submit_tools import FINISH_SPEECHES, speeches_tool
        tools = list(DATA_TOOLS) + [_list_database_items(), _search_database(), speeches_tool()]
        messages = [{"role": "system", "content": ""}, {"role": "user", "content": prompt}]
        result = run_tool_workflow(
            self.llm, messages, tools,
            state=self.state_ref or getattr(self.session, "state", None),
            session=self.session,
            finish_name=FINISH_SPEECHES,
        )
        speeches = ((result or {}).get("value") or {}).get("speeches") or []
        if not speeches:
            return False, (result or {}).get("error") or "本轮没有交出可用发言"
        for sp in speeches:
            self._add("ai", sp.get("name", ""), sp.get("text", ""), sp.get("expr", ""))
        return True, "本轮 %d 人发言" % len(speeches)

    def set_cast(self, names: List[str]) -> Tuple[bool, str]:
        """动态改参会人：近侍点圆增删、剧本事件指定 cast 都走这里。

        规则：至少留 1 人；会话进行中才允许改；名字必须能查到人物卡。
        """
        names = [n for n in (names or []) if n]
        if not names:
            return False, "至少要留一名参会者"
        pids = []
        for n in names:
            c = get_character(n)
            if not c:
                return False, "查无此人：%s" % n
            pids.append(c["id"])
        if not self.active:
            return False, "会话尚未开始"
        self.participants = pids
        self.probs = compute_match(self._topic_stamped(), pids)
        return True, "参会者已更新为 %s" % "、".join(names)

    def start(self, memorial_id: str, participant_pids: List[str], mode: str = "council",
              mainline_id: Optional[str] = None, initial_switches: Optional[List[str]] = None,
              imported_mids: Optional[List[str]] = None, book_ids: Optional[List[str]] = None,
              report_ids: Optional[List[Any]] = None,
              achievement_id: Optional[str] = None,
              cast_source: Optional[str] = None) -> Tuple[bool, str]:
        """发起会议（廷议 / 成就廷议 / 近侍会话 / 剧本对话）。"""
        if not participant_pids:
            return False, "请至少选择一名参会角色"
        unknown = [p for p in participant_pids if not get_character(p)]
        if unknown:
            return False, "召集了不存在的角色：%s" % "、".join(unknown)
        self.mode = mode or "council"
        self.cast_source = cast_source or "player_pick"
        self.achievement_id = achievement_id
        self.memorial_id = memorial_id
        self.mainline_id = mainline_id or memorial_id
        self.participants = list(participant_pids)
        self.data_switches = list(initial_switches or [])
        # 廷议：没带数据清单时，用该要紧事物上次留下的一份 主线界面勾选的那套
        if self.mode == "court" and not self.data_switches and self.mainline_id:
            from .mainline_script import get_mainline, get_script_switches
            self.data_switches = get_script_switches(get_mainline(self.mainline_id) or {})
        self.imported_memorial_ids = list(imported_mids or [])
        self.book_ids = list(book_ids or [])
        self.report_ids = list(report_ids or [])
        self.messages = []
        self.msg_seq = 0
        self.last_picked = []
        self.next_picked = []

        topic_text = self._topic_stamped()
        self.probs = compute_match(topic_text, self.participants)
        self.active = True

        mode_name = {"court": "廷议大典", "achievement": "成就廷议"}.get(self.mode, "御前廷议")
        self._add("system", "系统", f"{mode_name}开始：就《{self._topic_title()}》集议。")

        advance_minute(self.session.state, 5)    # 发起廷议：5 分钟

        cnames = [get_character(p)["name"] for p in self.participants if get_character(p)]
        CONSOLE.log("council", f"发起{mode_name}", f"议题={self._topic_title()} 模式={self.mode} 参与={'、'.join(cnames)}")
        return True, f"{mode_name}已开启"

    def set_data_switches(self, switches: List[str], imported_mids: Optional[List[str]] = None,
                          book_ids: Optional[List[str]] = None,
                          report_ids: Optional[List[Any]] = None) -> bool:
        """会话进行中随时开启/关闭数据开关、增删导入章奏、调阅藏书与战报。"""
        self.data_switches = list(switches or [])
        if imported_mids is not None:
            self.imported_memorial_ids = list(imported_mids)
        if book_ids is not None:
            self.book_ids = list(book_ids)
        if report_ids is not None:
            self.report_ids = list(report_ids)
        return True

    def state(self) -> Dict[str, Any]:
        """返回前端状态字典。"""
        parts = []
        for pid in self.participants:
            c = get_character(pid)
            if c:
                parts.append({
                    "id": pid,
                    "name": c["name"],
                    "title": c.get("title", ""),
                    "faction": c.get("faction", ""),
                    "prob": round(self.probs.get(pid, 0.0) * 100, 1),
                })
        m = self._memorial(self.memorial_id) if self.mode == "council" else None
        ml = self._mainline(self.mainline_id) if self.mode in ("court", "achievement") else None

        return {
            "active": self.active,
            "mode": self.mode,
            "memorial_id": self.memorial_id,
            "mainline_id": self.mainline_id,
            "topic_title": self._topic_title(),
            "memorial": m,
            "mainline": ml,
            "participants": parts,
            "messages": self.messages,
            "last_picked": self.last_picked,
            "next_picked": self.next_picked,
            "data_switches": self.data_switches,
            # 可调阅的数据开关总目 含每支军队的军情：前端材料挑选界面用它，不另抄一份清单
            "switch_options": switch_options(getattr(self.session, "state", None)),
            "imported_memorial_ids": self.imported_memorial_ids,
            "book_ids": self.book_ids,
            "report_ids": self.report_ids,
            # 顶层发言概率：前端按 id 取用 原来只在 participants 里，前端读 s.probs 恒空
            "probs": {p["id"]: p["prob"] for p in parts},
        }

    def _add(self, role: str, name: str, text: str, expr: str = ""):
        self.msg_seq += 1
        msg = {"id": self.msg_seq, "role": role, "name": name, "text": text}
        if expr:
            msg["expr"] = expr          # 只在表情变化时出现，前端据此换立绘
        self.messages.append(msg)

    def _memorial(self, mid: str):
        if not mid or not self.session:
            return None
        for m in self.session.memorials:
            if m.get("id") == mid:
                return m
        for d in getattr(self.session, "dialogues", []):
            if d.get("memorial_id") == mid:
                return {"title": d.get("memorial_title", ""), "content": d.get("summary", "")}
        return None

    def _mainline(self, ml_id: str):
        if not ml_id:
            return None
        if ml_id == "free_court":
            return {"id": "free_court", "title": "自由廷议大典", "category": "朝堂特议", "desc": "未预设主线议题，由皇上与群臣自由廷辩立案"}
        mainlines_path = os.path.join("world", "mainlines.json")
        try:
            with open(mainlines_path, "r", encoding="utf-8") as f:
                mls = json.load(f).get("mainlines", [])
                for m in mls:
                    if m.get("id") == ml_id:
                        return m
        except Exception:
            pass
        return {"id": ml_id, "title": "国家要紧事物", "desc": "关于国家大政之廷议"}

    def _topic_title(self) -> str:
        if self.mode == "achievement":
            from .achievements import get_achievement
            a = get_achievement(self.achievement_id) or {}
            return "成就%s" % a.get ("title", self.achievement_id or "成就")
        if self.mode == "court":
            ml = self._mainline(self.mainline_id)
            return ml.get("title", "国家要紧事物")
        m = self._memorial(self.memorial_id)
        return m.get("title", "议题章奏") if m else "议题"

    def _topic_stamped(self) -> str:
        if self.mode == "achievement":
            # 成就廷议额外带一套核查数据：科技是否解锁、地块是否控制、数值是否达标等，
            # 全部由 engine/achievements.check_pack_text 算好交给 AI，AI 只判「成没成」。
            from .achievements import check_pack_text, get_achievement
            a = get_achievement(self.achievement_id) or {}
            return ("【成就廷议 %s】\n%s\n\n%s"
                    % (a.get("title", self.achievement_id),
                       a.get("desc", ""),
                       check_pack_text(self.session.state, self.achievement_id)))
        if self.mode == "court":
            ml = self._mainline(self.mainline_id)
            return f"【要紧事物大政 {ml.get('title')}】\n分类：{ml.get('category', '国政')}\n概述：{ml.get('desc', '')}"
        m = self._memorial(self.memorial_id)
        if not m:
            return ""
        stamp = ""
        if m.get("event_time") or m.get("delivery_time"):
            stamp = f" 事件发生：{m.get('event_time', '?')}；送达：{m.get('delivery_time', '?')}"
        return f"《{m.get('title')}》{stamp}\n{m.get('content', '')}"

    def player_speak(self, text: str) -> Tuple[bool, str]:
        if not self.active:
            return False, "未在会议中"
        text = (text or "").strip()
        if not text:
            return False, "发言内容不能为空"
        self._add("player", "皇帝", text)
        advance_minute(self.session.state, 1)    # 玩家每次发言：1 分钟
        CONSOLE.log("council", "玩家发言", text)
        self._match_player_callout(text)
        return True, "已发言"

    def _match_player_callout(self, text: str):
        for pid in self.participants:
            c = get_character(pid)
            if not c:
                continue
            name = c.get("name", "")
            title = c.get("title", "")
            if (name and name in text) or (title and title in text):
                if pid not in self.next_picked:
                    self.next_picked.append(pid)

    def _select_speakers(self) -> List[str]:
        if self.next_picked:
            picked = list(self.next_picked[:2])
            self.next_picked = []
            return picked
        return weighted_pick(self.probs, k=min(2, len(self.participants)))

    def next_ai_reply(self) -> Tuple[bool, str]:
        """推进下一轮大臣发言。"""
        if not self.active:
            return False, "未在会议中"
        picked = self._select_speakers()
        if not picked:
            return False, "无发言人选"
        self.last_picked = picked

        from .hum import imported as _imported_exprs
        def _expr_line(p):
            ex = _imported_exprs(get_character(p)["name"])
            if not ex:
                return "可选表情：该角色未导入任何表情图，本轮不要为他写 expr 字段。"
            return "可选表情：%s（只有他的表情发生变化时才在 expr 里给一个，没变就不给）" % "、".join(ex)
        cards = "\n\n".join(prompt_profile(get_character(p)) + "\n" + _expr_line(p)
                             for p in picked if get_character(p))
        members = "\n".join(
            f"{get_character(p)['name']} {get_character(p).get('title','')}"
            for p in self.participants if get_character(p)
        )
        topic = self._topic_stamped()
        history = "\n".join(f"[{x['name']}] {x['text']}" for x in self.messages)
        imported_mems = [self._memorial(mid) for mid in self.imported_memorial_ids if self._memorial(mid)]
        context_data = extract_context_data(self.session.state, self.data_switches,
                                            memorials_data=imported_mems, session=self.session)
        books = extract_books(self.session.state, self.book_ids)

        prompt_file = "conference_2.txt"        # 集议只有廷议一条路，召对已删除
        prompt = read_prompt(prompt_file)

        if not prompt:
            for p in picked:
                c = get_character(p)
                self._add ("ai", c["name"], f"{c['name']}谨奏：臣以为此事体大，请陛下圣裁。")
            return True, "AI已发言 mock"

        prompt = (prompt.replace("{{members}}", members)
                  .replace("{{random_selection}}", cards)
                  .replace("{{topic}}", topic)
                  .replace("{{context_data}}", context_data)
                  .replace ("{{书籍}}", books)
                  .replace("{{history}}", history)
                  .replace("{{conversation}}", history))

        # 结构化结果只走工具交卷。两个选项（设置里可切）：
        #   默认：一次调用并交卷——只给交卷工具，一次请求里就要给出发言；
        #   打开工作流：再给 query_data，模型可以先查数、多轮往返，最后交卷。
        from .submit_tools import FINISH_SPEECHES, finish_speeches, speeches_tool
        messages = [{"role": "system", "content": ""}, {"role": "user", "content": prompt}]
        use_workflow = bool(getattr(self.llm, "council_workflow", False))
        entries, err = [], ""
        if use_workflow:
            from .ai_workflow import run_tool_workflow
            result = run_tool_workflow(self.llm, messages, list(DATA_TOOLS) + [speeches_tool()],
                                       state=self.session.state, session=self.session,
                                       finish_name=FINISH_SPEECHES)
            entries = (result.get("value") or {}).get("speeches") or []
            err = str(result.get("error") or "")
        else:
            import json as _json
            from . import ai_tools as _ai_tools
            # 直连不走工作流：工具要先剥成 name/description/parameters 才进得了请求体
            _payload = _ai_tools.tools_payload([speeches_tool()])
            _content, _think, calls = self.llm.chat_once(messages, tools=_payload,
                                                         think=False)
            for tc in (calls or []):
                fn = (tc or {}).get("function", {})
                if fn.get("name") != FINISH_SPEECHES:
                    continue
                raw = fn.get("arguments")
                try:
                    args = raw if isinstance(raw, (dict, list)) else _json.loads(raw or "{}")
                    entries = finish_speeches(args, self.session.state, self.session).get("speeches") or []
                except Exception as e:                 # 交卷参数不合格：如实记，不吃掉
                    err = "%s: %s" % (type(e).__name__, e)
                break
            else:
                err = "模型没有调用 %s，本次不落库" % FINISH_SPEECHES
        if entries:
            for e in entries[:3]:
                ex = str(e.get("expr") or "").strip()
                nm = e.get("name") or "大臣"
                if ex and ex not in _imported_exprs(nm):
                    ex = ""        # 没导入或写错的表情一律忽略，不让 UI 去猜
                self._add("ai", nm, e.get("text", ""), expr=ex)
        else:
            CONSOLE.log("council", "AI未交卷", err or "模型没有调用交卷工具")
            for p in picked:
                c = get_character(p)
                cname = c["name"] if c else "大臣"
                self._add ("ai", cname, f"{cname}谨奏：臣以为此事体大，请陛下圣裁。")
        return True, "AI已发言"

    def edit_message(self, mid: int, text: str) -> Tuple[bool, str]:
        for x in self.messages:
            if x["id"] == mid:
                x["text"] = text
                return True, "已修改"
        return False, "查无此消息"

    def delete_message(self, mid: int) -> Tuple[bool, str]:
        for x in self.messages:
            if x["id"] == mid:
                self.messages.remove(x)
                return True, "已删除"
        return False, "查无此消息"

    # ═══════════════════════════════════════════════════════════
    # 廷议出口：Prompt/meeting_2.txt（普通结束 / 直接结束共用同一份）
    # ═══════════════════════════════════════════════════════════

    def generate_pizou(self) -> Tuple[bool, str]:
        """廷议出口：批奏并归档进【廷议会话】。

        【v0.5.0】章奏回复的 AI 已全部删除：批奏不再由模型生成，而是取本次廷议里
        皇帝 玩家的裁断原文；玩家没发言时给一句固定的照例题准。
        AI 事件改由自由廷议与中旨负责。
        """
        if not self.active:
            return False, "未在召集"
        m = self._memorial(self.memorial_id)

        player_lines = [x["text"] for x in self.messages if x.get("role") == "player" and x.get("text")]
        fallback_pizou = "批红：此事着该部会同详议，妥议具奏，毋得稽延。"
        pizou =  "圣裁：" + player_lines[-1] if player_lines else fallback_pizou
        entries = []
        summary = _local_summary(self.messages)

        if m:
            m["pizou"] = pizou
            m["status"] = "annotated"

        self._add("system", "系统", "批奏已定：\n%s" % pizou)
        self._finalize(summary, entries=entries, pizou=pizou, category="council")
        return True, pizou

    def end(self) -> Tuple[bool, str]:
        """廷议出口：结束廷议并归档 纪要字段规范在 finish_minutes 的说明里。"""
        if not self.active:
            return False, "未在召集"
        summary, entries = self._summarise_minutes("meeting_2.txt")
        self._finalize(summary, entries=entries, pizou="", category="council")
        return True, summary

    # ═══════════════════════════════════════════════════════════
    # 廷议出口：Prompt/meeting_2.txt 与 meeting_pizou_free/replan.txt
    # ═══════════════════════════════════════════════════════════

    def roster_names(self) -> List[str]:
        """本次参会者姓名 唯一一处口径：由 participants 的人物卡取名。"""
        return [get_character(p)["name"] for p in self.participants if get_character(p)]

    def _summarise_minutes(self, prompt_file: str) -> Tuple[str, List[Dict[str, Any]]]:
        """两个出口共用的纪要生成：喂数据 → 模型用 finish_minutes 交卷。

        原来这里是「发一段提示词 → 让模型回 JSONL → 猜着解析」，解析不出来就退回模板；
        现在纪要的字段规范在工具说明里，归档正文由引擎从条目拼成人话 不再存 JSONL。
        """
        topic = self._topic_stamped()
        history = "\n".join(f"[{x['name']}] {x['text']}" for x in self.messages)
        members = "、".join(self.roster_names())
        prompt = read_prompt(prompt_file)
        if not prompt or getattr(self.llm, "mock", True):
            return _local_summary(self.messages), []
        prompt = (prompt.replace("{{topic}}", topic)
                        .replace("{{conversation}}", history)
                        .replace("{{history}}", history)
                        .replace("{{members}}", members))
        prompt = _ensure_timestamp(prompt, self.session.state.date)

        from . import minute_tools
        from .ai_workflow import run_tool_workflow
        messages = [{"role": "system",
                     "content": "你现在是会议纪要整理者。归纳会上各人观点，"
                                "用 finish_minutes 工具交回纪要。"},
                    {"role": "user", "content": prompt}]
        res = run_tool_workflow(
            self.llm, messages, minute_tools.TOOLS,
            state=self.session.state, session=self.session,
            finish_name=minute_tools.TOOL_FINISH, max_calls=3,
            payload_keys=minute_tools.FINISH_PAYLOAD_KEYS,
            finish_handler=minute_tools.finish_minutes)
        if not res["value"] or not res["value"]["entries"]:
            CONSOLE.log("council", "纪要未交卷，改用模板归纳", res.get("error") or "条目为空")
            return _local_summary(self.messages), []
        return res["value"]["summary"], res["value"]["entries"]

    def end_court_direct(self) -> Tuple[bool, str]:
        """廷议出口 1：直接结束总结并归档进【廷议会话】 Prompt/meeting_2.txt。"""
        if not self.active:
            return False, "未在廷议中"
        summary, entries = self._summarise_minutes("meeting_2.txt")
        self._finalize(summary, entries=entries, pizou="", category="court")
        return True, summary

    def _end_achievement_court(self) -> Tuple[bool, str, List[str]]:
        """成就廷议出口：AI 依据引擎给出的核查数据判定结束条件，判定通过即发科技点。

        - 核查数据由 engine/achievements.check_pack_text 现算 硬数值只有引擎一处算；
        - AI 只回「成没成」；未配模型 mock时退回引擎自己的硬判定，保证可演示。
        """
        from . import achievements as A

        aid = self.achievement_id
        a = A.get_achievement(aid) or {}
        pack = A.check_pack_text(self.session.state, aid)
        history = "\n".join("[%s] %s" % (x["name"], x["text"]) for x in self.messages)
        player_lines = [x["text"] for x in self.messages if x.get("role") == "player" and x.get("text")]
        pizou =  "圣裁：" + player_lines[-1] if player_lines else "成就廷议告成，著礼部依议推行。"

        verdict = None
        reason = ""
        if not getattr(self.llm, "mock", True):
            prompt = (
                "你在判定一个成就的结束条件是否已经达成。\n\n%s\n\n"
                "【本次廷议记录】\n%s\n\n"
                "只回一行 JSON：{\"completed\": true 或 false, \"reason\": \"一句话理由\"}\n"
                "注意：所有数值由系统给出，你只需据此判断结束条件是否达成，不要自己算数。"
                % (pack, history)
            )
            try:
                from .llm import parse_jsonl
                raw = self.llm.chat_text("", prompt, think=False)
                for row in parse_jsonl(raw or ""):
                    if isinstance(row, dict) and "completed" in row:
                        verdict = bool(row.get("completed"))
                        reason = str(row.get("reason") or "")
                        break
            except Exception as e:
                CONSOLE.log("council", "成就判定调用失败", str(e))
                verdict = None

        if verdict is None:
            # 未配模型或模型没给结论：退回引擎自己的硬判定 如实记明来源
            ok_hard, detail = A.run_check(self.session.state, a.get("end") or {})
            verdict = bool(ok_hard)
            reason = " 引擎硬判定" + detail

        op_logs: List[str] = []
        if verdict:
            done, msg = A.complete(self.session.state, aid)
            op_logs.append(msg)
        else:
            op_logs.append("结束条件未达成，本次廷议不发奖励：%s" % reason)

        summary = "成就廷议：%s" % reason
        self._add("system", "系统", "成就廷议结论：%s\n%s" % (reason, "；".join(op_logs)))
        self._finalize(summary, entries=[], pizou=pizou, category="court")
        return True, pizou, op_logs

    def end_court_with_pizou(self) -> Tuple[bool, str, List[str]]:
        """廷议出口：形成决议并施政，归档进【廷议会话】。

        【v0.5.0】不再分阶段、只剩两套提示词，剧本由 AI 三轮工具工作流撰写
         见 engine/script_workflow.py：
        - 首次撰写 → Prompt/meeting_pizou_free.txt
        - 玩家干预后改写 → Prompt/meeting_pizou_replan.txt
        超控指令不再由廷议直接执行，而是写在事件的 impact 里，由引擎在事件曝光时执行；
        整段工作流对话记进会话的 script_sessions，在前端「事件树」查看。
        """
        if not self.active:
            return False, "未在廷议中", []
        
        if self.mode == "achievement":
            return self._end_achievement_court()

        from .mainline_script import (get_mainline, update_mainline_script,
                                      create_custom_mainline, set_script_switches)

        ml = get_mainline(self.mainline_id) or {}
        is_free_court = (self.mode == "court" and self.mainline_id == "free_court")

        topic = self._topic_stamped()
        history = "\n".join("[%s] %s" % (x["name"], x["text"]) for x in self.messages)
        members = "\n".join(
            "%s %s" % (get_character(p)["name"], get_character(p).get("title", ""))
            for p in self.participants if get_character(p)
        )
        imported_mems = [self._memorial(mid) for mid in self.imported_memorial_ids if self._memorial(mid)]
        context_data = extract_context_data(self.session.state, self.data_switches,
                                            memorials_data=imported_mems, session=self.session)
        books = extract_books(self.session.state, self.book_ids)
        reports = extract_reports(self.session, self.report_ids)

        from .script_workflow import run_script_workflow
        from .llm import get_llm_for_system
        court_llm = get_llm_for_system("court", self.llm)
        result = run_script_workflow(
            self.session.state, self.session, mainline=ml, topic=topic,
            context_data=context_data, book_text=books, report_text=reports,
            members=members, conversation=history, llm=court_llm)
        script_data = result.get("script")
        log = result.get("log") or []
        end_summary = (result or {}).get("end_summary") or ""
        if end_summary and not script_data:
            # 没有数值硬判定的剧本：由 AI 用 submit_script_end 收束，只落一段概括
            self._add("system", "系统", "剧本收束：%s" % end_summary)

        # 自由廷议：先用 AI 给的剧情名称立一个新的要紧事物，再把剧本挂上去
        if is_free_court and script_data:
            new_ml = create_custom_mainline(script_data.get("name") or "朝堂新定大政",
                                            desc=script_data.get("introduction", ""),
                                            category="朝堂特议")
            self.mainline_id = new_ml["id"]
            self.memorial_id = new_ml["id"]

        # 圣裁决议＝玩家在本次廷议里的发言；没有发言就照例题准
        player_lines = [x["text"] for x in self.messages if x.get("role") == "player" and x.get("text")]
        pizou =  "圣裁：" + player_lines[-1] if player_lines else \
            "廷议大典告成。著六部九卿遵照会议成案，实心推行，毋得推诿废弛。"

        op_logs: List[str] = []
        if script_data:
            update_mainline_script(self.mainline_id, script_data, pizou_text=pizou)
            op_logs.append("剧本《%s》已立，展示事件 %d 条 其中的超控影响随事件曝光执行。"
                           % (script_data.get("name", ""), len(script_data.get("events") or [])))
        else:
            op_logs.append("本次未取得可用剧本：%s" % result.get("error", "未知原因"))

        # 数据清单留作下次撰写剧本的默认
        if self.mainline_id and self.mainline_id != "free_court":
            set_script_switches(self.mainline_id, self.data_switches)

        # 事件树：整段工作流对话落库 输入、每轮工具包、工具往返、最终剧本
        self.session.add_script_session(
            mainline_id=self.mainline_id,
            title=(script_data or {}).get("name") or ml.get("title") or topic,
            log=log,
            script=script_data,
            note=(result.get("error") or ""),
        )

        summary = script_data.get("introduction") if script_data else _local_summary(self.messages)
        self._add("system", "系统", "廷议决议已下颁施行：\n%s\n\n%s" % (pizou, "\n".join(op_logs)))
        self._finalize(summary, entries=[], pizou=pizou, category="court")
        return True, pizou, op_logs

    def quick_end(self) -> Tuple[bool, str]:
        """未发言直接退出：只标记散会，不归档、不总结为对话记录。"""
        if not self.active:
            return False, "未在会议中"
        self.active = False
        self.messages = []
        self.last_picked = []
        self.next_picked = []
        CONSOLE.log("council", "未发言直接散会", "议题=%s 不归档对话记录" % self._topic_title())
        return True, "已退出"

    def _finalize(self, summary: str, entries: Optional[List[Dict[str, Any]]] = None,
                  pizou: str = "", next_due: str = "", category: str = "council") -> int:
        title = self._topic_title()
        pid = self.session.save_dialogue(
            self.memorial_id,
            title,
            summary,
            self.participants,
            category=category,
            entries=entries,
            pizou=pizou,
            messages=list(self.messages),
            next_due=next_due
        )
        mode_name = "廷议会话"      # 旧「召对会话」归档已删除
        CONSOLE.log("council", f"{mode_name}已归档 (id={pid})", f"议题={title} 参与={len(self.participants)}员")
        self.active = False
        self.last_picked = []
        self.next_picked = []
        return pid


def _local_summary(messages: List[Dict[str, Any]]) -> str:
    parts = []
    for x in messages:
        if x.get("role") in ("player", "ai"):
            parts.append(f"{x['name']}：{x['text'][:60]}")
    return "；".join(parts)[:800] or " 无实质内容"


def _ensure_timestamp(prompt: str, date: Dict[str, Any]) -> str:
    from .time import date_key
    if "{{timestamp}}" in prompt:
        return prompt.replace("{{timestamp}}", date_key(date))
    return prompt


def _split_pizou(text: str) -> Tuple[List[Dict[str, Any]], str]:
    lines = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("```") or line.startswith("json"):
            continue
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                lines.append(d)
        except ValueError:
            continue
    entries = [d for d in lines if "character" in d and "summary" in d]
    p_item = next((d for d in reversed(lines) if "pizou" in d), None)
    pizou = p_item.get("pizou", "") if p_item else ""
    return entries, pizou


def _split_court_pizou(text: str) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """拆分 JSONL 格式返回数据。"""
    lines = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("```") or line.startswith("json"):
            continue
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                lines.append(d)
        except ValueError:
            continue
    entries = [d for d in lines if "character" in d and "summary" in d]
    p_item = next((d for d in reversed(lines) if "pizou" in d or "operations" in d), None)
    pizou = p_item.get("pizou", "") if p_item else ""
    ops = p_item.get("operations", []) if p_item else []
    return entries, pizou, ops
