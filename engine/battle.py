"""战争裁判与战斗模拟系统：
1. 每天推进时根据各省双方军队攻防状态 总体状态：进攻/防御判定是否触发战斗：
   - 己方进攻 vs 敌方防御：触发进攻战
   - 己方防御 vs 敌方进攻：触发防御战
   - 双方均为进攻：触发遭遇战
   - 双方均为防御：不发生战斗
2. 战役发生后调用 Prompt/war.txt 由战争裁判 AI 或确定性 mock 兜底推演战斗过程与损失；
3. 将实际伤亡、精锐变动、装备损毁扣减到部队；
4. 战报生成后存入待交付队列，于次日凌晨 1 点正式送达归档，并提醒前端亮红点。
"""

import os
import json
import random
from typing import Any, Dict, List, Optional, Tuple

from .logger import CONSOLE
from .distances import PROVINCE_NAMES
from .char_cards import load_cards, get_card
from .armies import load_armies_data, save_armies_data, find_unit
from .llm import get_llm_for_system

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAR_PROMPT_PATH = os.path.join(_BASE, "Prompt", "war.txt")


def _format_unit_data_for_prompt(u: Dict[str, Any], cards_pool: Dict[str, Any]) -> str:
    """按要求组装 {{military}} 或 {{enemy forces}} 的军队及名将数据。"""
    u_name = u.get ("name", "未命名部队")
    u_count = int(u.get("count", 0))
    u_elite = u.get("elite", 50)
    u_corr = u.get("corruption", 50)
    u_loyalty = u.get("loyalty", 70)
    u_stance = u.get ("overall_stance", "防御")
    u_mission = u.get ("mission", "守备")
    u_risk = u.get ("engagement_risk", "中风险交战")
    u_retreat = u.get ("retreat_priority", "低撤退优先级")
    u_order = u.get ("battle_order", "守备")
    u_station = u.get ("station", "营地")
    commander_name = u.get("commander", "")

    # 装备概要
    eq_parts = []
    for cat, rows in (u.get("equipment") or {}).items():
        for r in rows:
            m_name = r.get("era_model") or r.get("grade")
            c = r.get("count", 0)
            if c > 0:
                eq_parts.append(f"{m_name}×{c}")
    eq_str = "、".join(eq_parts) if eq_parts else "常规刀矛器械"

    lines = [
        f"【部队】{u_name} 总人数: {u_count}人，精锐指数: {u_elite}，军队腐败: {u_corr}，忠诚度: {u_loyalty}",
        f"  - 驻所/防线: {u_station} | 任务指令: {u_mission} | 交战风险: {u_risk} | 撤退优先级: {u_retreat}",
        f"  - 战役指令: {u_order} | 装备列装: {eq_str}",
    ]

    # 名将与领导人匹配
    found_card = None
    if commander_name:
        if commander_name in cards_pool:
            found_card = cards_pool[commander_name]
        else:
            c = get_card(commander_name)
            if c:
                found_card = c

    if found_card:
        b = found_card.get("basic", {})
        lines.append(f"  - 领导人/统帅: {commander_name} 官职: {b.get('官职', '主将')}，籍贯: {b.get('籍贯', '')}，阵营: {b.get('阵营', '')}")
    elif commander_name:
        lines.append (f"  - 领导人/统帅: {commander_name}")
    else:
        lines.append ("  - 领导人/统帅: 无信息")

    return "\n".join(lines)


def _get_province_architecture(state: Any, pid: str) -> str:
    """获取本省军事建筑变量 {{architecture}}。"""
    if not state or not hasattr(state, "provinces"):
        return "本省暂无特设城防要塞设施。"
    p = state.provinces.get(pid)
    if not p:
        return "本省暂无特设城防要塞设施。"
    loc = p.get("local") or {}
    projs = loc.get("projects") or []
    arch_lines = []

    for pj in projs:
        kind = pj.get("kind", "")
        name = pj.get("name", "")
        if kind in  ("要塞", "边墙", "城垣", "城防要塞" or "关" in name or "要塞" in name or "城" in name or "堡" in name or "边墙" in name):
            from .armies import owner_label
            ctrl = pj.get("controller") or owner_label(p.get("owner"))
            dmg_str = f" 已损坏 {int(pj.get('damage_ratio', 0.0) * 100)}%" if pj.get("damaged") else " 完好"
            arch_lines.append(f"建筑名: {name} | 控制权所属: {ctrl}{dmg_str} | 描述: {pj.get('desc', '')} 类型: {pj.get('kind', '军事要塞')}")

    if not arch_lines:
        from .armies import owner_label
        ctrl = owner_label(p.get("owner"))
        return f"建筑名: {p.get('name', pid)}卫所城垣 | 控制权所属: {ctrl} | 描述: 卫所砖石营城与基本哨垒"
    return "\n".join(arch_lines)


def _get_local_weather(state: Any, pid: str) -> str:
    """获取战场当地当日天气变量 {{local_weather}} 写法沿用 session 的天气情况。"""
    p = (state.provinces.get(pid) if state and hasattr(state, "provinces") else None) or {}
    w = (p.get("local") or {}).get("weather") or {}
    if not w:
        return " 本省当日天气暂无记录"
    parts = ["%s %s℃" % (w.get("condition", "?"), w.get("temp", 0))]
    if w.get("rain_mm"):
        parts.append("降水 %.1fmm" % float(w["rain_mm"]))
    if w.get("disaster"):
        parts.append("灾害：%s 尚余 %s 日，强度 %s"
                     % (w.get("disaster"), w.get("disaster_remain", 0), w.get("severity", 0)))
    if w.get("capacity_penalty"):
        parts.append("本季产能 -%.0f%%" % (float(w["capacity_penalty"]) * 100))
    return "，".join(parts)


def _get_local_classes(state: Any, pid: str) -> str:
    """获取战场当地各阶级态度变量 {{local_classes}}。"""
    p = (state.provinces.get(pid) if state and hasattr(state, "provinces") else None) or {}
    cls = (p.get("local") or {}).get("classes") or {}
    if not cls:
        return " 本省各阶级口碑暂无记录"
    return "\n".join(
        "%s：态度 %.1f | 人口 %.2f万"
        % (name, c.get("attitude", 0), c.get("pop", 0))
        for name, c in cls.items()
    )


def run_war_referee_ai(state: Any, pid: str, nature: str, friendly_units: List[Dict[str, Any]], enemy_units: List[Dict[str, Any]], involve_arch: bool = False) -> Dict[str, Any]:
    """运行战争裁判 AI。"""
    cards_pool = load_cards()
    f_texts = [_format_unit_data_for_prompt(u, cards_pool) for u in friendly_units]
    e_texts = [_format_unit_data_for_prompt(u, cards_pool) for u in enemy_units]

    military_str = "\n\n".join(f_texts)
    enemy_str = "\n\n".join(e_texts)
    architecture_str = _get_province_architecture(state, pid)

    p_name = PROVINCE_NAMES.get(pid, pid)
    cur_date_str = f"{state.date.get('year', 1627)}-{state.date.get('month', 8):02d}-{state.date.get('day', 1):02d}"

    # 动态从历史纪事中提取过去12个月的世界记录
    from .session import extract_history_12
    history_12 = extract_history_12(state)

    # 读取 Prompt/war.txt
    prompt_tpl = ""
    if os.path.exists(WAR_PROMPT_PATH):
        try:
            with open(WAR_PROMPT_PATH, "r", encoding="utf-8") as f:
                prompt_tpl = f.read()
        except Exception:
            pass

    if prompt_tpl:
        prompt_full = (prompt_tpl
                       .replace("{{military}}", military_str)
                       .replace("{{enemy forces}}", enemy_str)
                       .replace("{{architecture}}", architecture_str)
                       .replace("{{nature}}", nature)
                       .replace("{{timestamp}}", cur_date_str)
                       .replace("{{local_weather}}", _get_local_weather(state, pid))
                       .replace("{{local_classes}}", _get_local_classes(state, pid))
                       .replace("{{history_12}}", history_12))
    else:
        prompt_full = f"战争裁判推演：{nature}，战场：{p_name}。\n己方：\n{military_str}\n敌方：\n{enemy_str}"

    llm = get_llm_for_system("war_referee")
    llm.timeout = int(getattr(llm, "timeout", 60) or 60)

    # 战报的字段规范与示例在 finish_battle 的 description 里；这里只把数据喂给模型，
    # 多轮往返与"参数不合法就如实报错"由 engine/ai_workflow 统一处理。
    from . import battle_tools
    from .ai_workflow import run_tool_workflow

    report_obj = None
    if not llm.mock:
        messages = [
            {"role": "system",
             "content": "你现在将扮演战争裁判 AI。用 finish_battle 工具交回战报。"},
            {"role": "user", "content": prompt_full},
        ]
        res = run_tool_workflow(llm, messages, battle_tools.TOOLS,
                               state=state, session=None,
                               finish_name=battle_tools.TOOL_FINISH, max_calls=3,
                               payload_keys=battle_tools.FINISH_PAYLOAD_KEYS,
                               finish_handler=battle_tools.finish_report)
        report_obj = res["value"]

    if not report_obj:
        # 裁判没交卷 mock / 调用失败 / 参数不合规到底：用确定性兜底战报，
        # 并在日志里说清这是引擎兜底、不是 AI 推演的结果
        CONSOLE.log("war", "战争裁判未交卷，改用引擎兜底战报", "省份 %s 战役性质 %s" % (pid, nature))
        from .armies import owner_label
        f_main = friendly_units[0].get ("name", "官军" if friendly_units else "官军")
        e_main = enemy_units[0].get ("name", "敌军" if enemy_units else "敌军")
        f_owner = friendly_units[0].get("owner", "ming") if friendly_units else "ming"
        e_owner = enemy_units[0].get("owner", "ming") if enemy_units else "ming"
        deaths_f = random.randint(15, 85)
        deaths_e = random.randint(30, 160)

        # 建筑争夺 mock
        if involve_arch:
            # 先手一方为本方 见 evaluate_province_engagement 的视角
            winner = owner_label(f_owner) if nature in ("进攻战", "遭遇战") else owner_label(e_owner)
            arch_res = {
                "architecture_name": f"{p_name}卫所城垣",
                "control_change": winner,
                "is_damaged": True,
                "damage_ratio": 0.15
            }
        else:
            arch_res = {
                "architecture_name": "无",
                "control_change": "无变化",
                "is_damaged": False,
                "damage_ratio": 0.0
            }

        mock_process = (
            f"【{p_name}战报】{f_main}与{e_main}于{p_name}境展开{nature}。"
            f"双方依令布阵，前锋以火器弓弩齐射，继而步骑合击；一方阵线动摇而后撤，"
            f"战役达成初步作战目标。"
        )
        report_obj = {
            "timestamp": cur_date_str,
            "battle_process": mock_process,
            "units": [
                {
                    "unit_name": f_main,
                    "deaths": deaths_f,
                    "elite_degradation": "+1",
                    "equipment_loss": [{"equipment": "万历二十年式白蜡杆长矛", "count": 10},
                                       {"equipment": "嘉靖三十四年式戚家倭刀", "count": 5}]
                },
                {
                    "unit_name": e_main,
                    "deaths": deaths_e,
                    "elite_degradation": "-2",
                    "equipment_loss": [{"equipment": "卫所旧布面铁甲", "count": 12},
                                       {"equipment": "白蜡杆铁扎长枪与大刀", "count": 25}]
                }
            ],
            "architecture_result": arch_res
        }
        report_obj["engine_fallback"] = True     # 标明这份战报是引擎兜底，不是 AI 推演的

    return {
        "province_id": pid,
        "province_name": p_name,
        "nature": nature,
        "date": cur_date_str,
        "report": report_obj,
        "involve_architecture": involve_arch,
        "friendly_names": [u["name"] for u in friendly_units],
        "enemy_names": [u["name"] for u in enemy_units],
    }


def apply_battle_losses(state: Any, battle_res: Dict[str, Any]) -> List[str]:
    """将战斗裁判给出的损失 伤亡、精锐变化、装备损毁、军事建筑控制权/损坏结算进实际部队与省份。"""
    notes = []
    rep = battle_res.get("report") or {}
    units_losses = rep.get("units") or []
    data = load_armies_data()
    changed = False

    for ul in units_losses:
        u_name = ul.get("unit_name", "")
        deaths = int(ul.get("deaths", 0))
        elite_change_str = str(ul.get("elite_degradation", "0")).replace("+", "")
        try:
            elite_change = int(elite_change_str)
        except ValueError:
            elite_change = 0

        eq_losses = ul.get("equipment_loss") or []

        # 在名册里按 id 或名称找到那一条 机动、镇军、反叛军同走一份名册
        target_unit = find_unit(data.get("units") or [], u_name)

        if target_unit:
            # 1. 扣减人员
            if deaths > 0:
                old_cnt = int(target_unit.get("count", 0))
                new_cnt = max(0, old_cnt - deaths)
                target_unit["count"] = new_cnt
                changed = True
                notes.append (f"【战损】{u_name}阵亡 {deaths}人 兵力: {old_cnt} -> {new_cnt}。")

            # 2. 精锐度调整
            if elite_change != 0:
                old_elite = int(target_unit.get("elite", 50))
                new_elite = max(0, min(100, old_elite + elite_change))
                target_unit["elite"] = new_elite
                changed = True
                sign = "+" if elite_change > 0 else ""
                notes.append (f"【战功/磨砺】{u_name}精锐度变动 {sign}{elite_change} 现指数: {new_elite}。")

            # 3. 装备损毁扣减 新数据是 {equipment,count}，旧存档是"名称+尾数"串，统一在这里规范
            from .battle_tools import equipment_pairs
            eq_dict = target_unit.get("equipment") or {}
            for eq_pure_name, loss_num in equipment_pairs(eq_losses):
                if not eq_pure_name:
                    continue
                for cat, rows in eq_dict.items():
                    hit = False
                    for r in rows:
                        m_n = r.get("era_model") or r.get("grade") or ""
                        if eq_pure_name in m_n or m_n in eq_pure_name:
                            r["count"] = max(0, int(r.get("count", 0)) - loss_num)
                            changed = True
                            hit = True
                            break
                    if hit:
                        break

    if changed:
        save_armies_data(data)

    # 4. 结算军事建筑控制权与损坏比例
    arch_res = rep.get("architecture_result")
    if arch_res and state and hasattr(state, "provinces"):
        pid = battle_res.get("province_id")
        p = state.provinces.get(pid)
        if p:
            loc = p.setdefault("local", {})
            projs = loc.setdefault("projects", [])
            arch_name = arch_res.get("architecture_name", "")
            ctrl_change = arch_res.get ("control_change", "无变化")
            is_damaged = bool(arch_res.get("is_damaged", False))
            dmg_ratio = float(arch_res.get("damage_ratio", 0.0))

            target_proj = None
            for pj in projs:
                if pj.get("name") == arch_name or (not arch_name and (pj.get("kind") in ("要塞", "边墙", "城垣", "城防要塞") or "城" in pj.get("name", ""))):
                    target_proj = pj
                    break

            if not target_proj and arch_name:
                # 若无匹配工程则动态建立省份卫所城垣条目 五个属性 + 战斗状态
                from .armies import owner_label
                target_proj = {
                    "name": arch_name,
                    "kind": "城防要塞",
                    "scope": "本省",
                    "status": "运营中",
                    "buff": {},
                    "controller": owner_label(p.get("owner")),
                    "damaged": False,
                    "damage_ratio": 0.0
                }
                projs.append(target_proj)

            if target_proj:
                if ctrl_change and ctrl_change != "无变化":
                    target_proj["controller"] = ctrl_change
                    notes.append(f"【要塞得失】{p.get('name', pid)}军事设施【{target_proj.get('name')}】控制权易手为：{ctrl_change}！")
                if is_damaged:
                    target_proj["damaged"] = True
                    target_proj["damage_ratio"] = min(1.0, round(float(target_proj.get("damage_ratio", 0.0)) + dmg_ratio, 2))
                    notes.append(f"【要塞受创】{target_proj.get('name')}战火受创，当前损坏度：{int(target_proj['damage_ratio'] * 100)}%。")

    return notes


def should_engage(initiator: Dict[str, Any], opponent: Dict[str, Any]) -> bool:
    """硬逻辑裁决：根据主动方部队的交战风险判定是否愿意与目标交战。
    - 避战：军队永远不主动发生任何战斗
    - 低风险交战：只会与精锐程度和人数都低于自己的部队战斗
    - 中风险交战：与精锐程度和人数都不高于自己20%或低于自己的部队战斗
    - 高风险交战：与精锐程度和人数都不高于自己50%或低于自己的部队战斗
    - 逢敌必战：主动和任何部队战斗
    """
    risk = initiator.get ("engagement_risk", "中风险交战")
    if risk == "避战":
        return False
    if risk == "逢敌必战":
        return True

    init_cnt = int(initiator.get("count", 1))
    init_elite = float(initiator.get("elite", 50))
    opp_cnt = int(opponent.get("count", 1))
    opp_elite = float(opponent.get("elite", 50))

    if risk == "低风险交战":
        return (opp_cnt < init_cnt) and (opp_elite < init_elite)
    elif risk == "中风险交战":
        return (opp_cnt <= init_cnt * 1.20) and (opp_elite <= init_elite * 1.20)
    elif risk == "高风险交战":
        return (opp_cnt <= init_cnt * 1.50) and (opp_elite <= init_elite * 1.50)

    return True


def evaluate_province_engagement(f_list: List[Dict[str, Any]], e_list: List[Dict[str, Any]]) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool]:
    """根据双方侦察值、任务指令与交战风险裁决是否爆发战役。
    返回 (should_fight, nature, decisive_unit, target_unit, involve_architecture)
    """
    from .armies import calc_recon_value

    # 1. 计算双方最强侦察主力
    for u in f_list:
        u["recon_value"] = calc_recon_value(u)
    for u in e_list:
        u["recon_value"] = calc_recon_value(u)

    f_scout_unit = max(f_list, key=lambda x: x["recon_value"])
    e_scout_unit = max(e_list, key=lambda x: x["recon_value"])

    f_recon = f_scout_unit["recon_value"]
    e_recon = e_scout_unit["recon_value"]

    # 侦察值高者获得裁决权
    if f_recon >= e_recon:
        decisive_side = "friendly"
        decisive_unit = f_scout_unit
        target_unit = e_scout_unit
    else:
        decisive_side = "enemy"
        decisive_unit = e_scout_unit
        target_unit = f_scout_unit

    # 任务指令判定
    decisive_mission = decisive_unit.get ("mission", "守备")
    target_mission = target_unit.get ("mission", "守备")

    # 若高侦察方处于守备任务，守备部队分散驻守不主动出击
    if decisive_mission == "守备":
        return False, None, None, None, False

    # 检查交战风险：高侦察方根据自身交战风险决定是否抓捕开战
    if not should_engage(decisive_unit, target_unit):
        return False, None, None, None, False

    # 战斗性质判定：
    # - 双方皆为进攻向任务 决战/巡逻 -> 遭遇战
    # - 己方进攻敌方守备 -> 进攻战
    # - 敌方进攻己方守备 -> 防御战
    f_has_atk = any(u.get("mission") in ("决战", "巡逻") for u in f_list)
    e_has_atk = any(u.get("mission") in ("决战", "巡逻") for u in e_list)

    if f_has_atk and e_has_atk:
        nature = "遭遇战"
    elif f_has_atk and not e_has_atk:
        nature = "进攻战"
    else:
        nature = "防御战"

    # 军事建筑关联判定：任务指令为“决战”或“守备”时倾向于计入军事建筑，纯“巡逻”只打野战不攻城
    involve_architecture = (decisive_mission == "决战" or target_mission in  ("决战", "守备"))

    return True, nature, decisive_unit, target_unit, involve_architecture


def check_and_trigger_daily_battles(state: Any) -> List[Dict[str, Any]]:
    """每日检查各省份驻军，判定是否触发战役：
    - 严格使用 get_all_armies 读取真实的部队全量数据 人数、装备、精锐、主将，绝不使用残缺空壳；
    - **按所属国家 owner分类**：同一省内归属不同的两方即成敌对，两两判定，不问是哪一国
       明 vs 满清、满清 vs 蒙古诸部、明 vs 叛军…同一套规则；同属一国的部队之间不交战；
    - 侦察值高的部队根据自身交战风险与任务指令先决定是否开启战斗；
    - 战役性质：进攻战 / 防御战 / 遭遇战 以先手一方的视角给；
    - 任务指令 决战、巡逻、守备联动军事建筑影响。
    """
    from .armies import get_all_armies, owner_label
    all_armies = get_all_armies(state)

    # 按省份聚合：{省: {归属: [部队]}}——交战方就是"同一省内归属不同的两方"
    prov_by_owner: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for u in all_armies:
        loc = u.get("current_location") or u.get("province_id")
        if not loc:
            continue
        prov_by_owner.setdefault(loc, {}).setdefault(u.get("owner", "ming"), []).append(u)

    triggered_battles = []

    for pid, by_owner in prov_by_owner.items():
        if len(by_owner) < 2:
            continue
        owners = list(by_owner)
        # 两两配对 同一省内不同归属即敌对双方
        for i in range(len(owners)):
            for j in range(i + 1, len(owners)):
                o_a, o_b = owners[i], owners[j]
                a_list, b_list = by_owner[o_a], by_owner[o_b]

                fight, nature, dec_unit, tgt_unit, involve_arch = evaluate_province_engagement(a_list, b_list)
                if not fight or not nature:
                    continue

                # 每日交火判定 10% 概率触发
                if random.random() < 0.10:
                    battle_res = run_war_referee_ai(state, pid, nature, a_list, b_list, involve_arch=involve_arch)
                    battle_res["friendly_owner"] = owner_label(o_a)
                    battle_res["enemy_owner"] = owner_label(o_b)
                    loss_notes = apply_battle_losses(state, battle_res)
                    battle_res["loss_notes"] = loss_notes
                    triggered_battles.append(battle_res)
                    CONSOLE.log("war", f"【战事爆发】{battle_res['province_name']}发生{nature}！",
                                f"{owner_label(o_a)} vs {owner_label(o_b)}\n"
                                f"侦察主导：{dec_unit['name']} 侦察{dec_unit['recon_value']:.1f}，目标：{tgt_unit['name']} 侦察{tgt_unit['recon_value']:.1f}\n"
                                f"参战部队：{'、'.join(battle_res['friendly_names'])} vs {'、'.join(battle_res['enemy_names'])}\n"
                                f"战况简述：{battle_res['report'].get('battle_process', '')}")

    return triggered_battles
