"""大明军队名册与军务调度体系：机动部队、地方镇军、行军调动与欠饷结算。

数据与规则规范：
1. 机动部队与地方镇军：
   - 机动部队 Mobile Forces：可跨省调度与野战的主力军团 如石砫白杆兵。
   - 地方镇军 Garrison Forces：驻扎各省府卫所，亦可由枢密兵部下令调遣换防。
2. 军队状态与属性：
   - 人数 (count)、精锐 (elite)、腐败 (corruption)、忠诚 (loyalty)
   - 月度军饷  monthly_pay: count × 1.5 / 10000 万两/月
   - 月度维护费  monthly_maint: count × 0.35 / 10000 万两/月
   - 当前位置 (current_location)、目标位置 (target_location)
   - 行军状态  status: 驻防 / 行军中、行军路径 (path)、总耗时 (total_days)、剩余天数 (remain_days)
3. 欠饷与减产惩罚：
   - 默认不欠军饷 开局 arrears_wan = 0.0。
   - 所欠军饷只合算本季度未能发出的军饷。
   - 每月结算时，若国库银两不足支付全军军饷，无法支出的缺口部分按比例计入各部队所欠军饷 arrears_wan。
   - 军队所在地 当前位置全阶级产能减产：每欠 1 万两扣减 1.0%。
   - 跨季度时 季初重置本季度欠饷为 0。
4. 军队调动与行军推进：
   - 调动规则同驿站规则：根据省际邻接距离图与移动修正，使用 Dijkstra 算法计算最短时间路径。
   - 设定目标位置后立即向目标行军；若中途重新选择目标，则以当前所在省份为起点重新计算路径与时间。
   - 每日随时间推进减少行军剩余天数，到达目标地后就地驻防。
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple
from .distances import find_shortest_path, PROVINCE_NAMES
from .logger import CONSOLE

ARMIES_FILE = os.path.join("world", "armies.json")

_RUNTIME_ARMIES = None

# ── 军队标识词表 唯一一处定义──
# 名册是**独立的一份** world/armies.json：任何势力的任何部队都登记在这里，
# 靠属性过滤与识别 owner 定归属敌我、category 定编制、location 定所在、status 定状态。
# 兵部、枢密、战斗、地图、前端都只是这份名册的读者；兵部通过调度 写改它的状态。
TAGS: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "category":        (("mobile", "garrison"), "garrison"),
    "owner":           (("ming", "houjin", "mongol", "诸部", "叛军"), "ming"),
    "status":          (("驻防", "行军中", "候旨开拔", "溃散"), "驻防"),
    "mission":         (("守备", "巡逻", "决战", "围城"), "守备"),
    "overall_stance":  (("进攻", "防御"), "防御"),
    "engagement_risk": (("高风险交战", "中风险交战", "低风险交战"), "中风险交战"),
    "retreat_priority": (("高撤退优先级", "中撤退优先级", "低撤退优先级"), "中撤退优先级"),
    "battle_order":    (("依令行动", "守备卫所", "据险固守", "主动出击"), "依令行动"),
}

# 归属 owner的中文名与军令起点 唯一一处：
#   谁的名册条目、谁的军令从哪发出、势力名怎么写，都看这一张表。
#   label：界面、文案、数据字段 local.controller / 名册与人物卡 faction统一只写这一种；
#   names：同义写法，只当**输入别名**用 把 AI、旧存档送进来的名字归到同一个 code，
#   数据与界面一律不写它们。
#   origin：该国军令的发出省 空串表示无中枢，就在部队所在省发令
OWNERS: Dict[str, Dict[str, Any]] = {
    "ming":   {"label": "明",       "names": ("明", "明朝", "明廷"),      "origin": "beizhili"},
    "houjin": {"label": "满清",     "names": ("满清",),                  "origin": "shengjing"},
    "mongol": {"label": "蒙古诸部", "names": ("蒙古诸部", "蒙古"),         "origin": ""},
    "诸部":    {"label": "诸部",     "names": ("诸部", "other"),           "origin": ""},
    "叛军":    {"label": "叛军",     "names": ("叛军", "流寇", "起义军"),   "origin": ""},
}


def owner_label(owner: str) -> str:
    """归属的中文名 界面与文案统一从这里取。"""
    code = owner if owner in OWNERS else owner_of(owner)
    return (OWNERS.get(code or "") or {}).get("label", owner or "明")


def owner_of(name: str) -> str:
    """势力名/归属中文名 → owner 标识 与 owner_label 同一张表，反查。"""
    n = (name or "").strip()
    if not n:
        return ""
    if n in OWNERS:
        return n
    for code, info in OWNERS.items():
        if n == info.get("label") or n in (info.get("names") or ()):
            return code
    return ""


def order_origin(owner: str) -> str:
    """该国军令的发出省 空串表示无中枢，就在部队所在省发令。"""
    return (OWNERS.get((owner or "ming")) or {}).get("origin", "")


def is_friendly(unit: Dict[str, Any]) -> bool:
    """敌我识别只看归属属性：owner == ming 为明属 己方，其余为敌方。"""
    return (unit or {}).get("owner", "ming") == "ming"


def tag_values(key: str) -> Tuple[str, ...]:
    """取某标识的可选值 给前端、给指令校验共用。"""
    return TAGS[key][0]


# 军饷标准 唯一一处：镇军与机动部队同用
#   月度军饷 = 人数 × PAY_PER_MAN 两/人月   月度维护 = 人数 × MAINT_PER_MAN 两/人月
# 名册只记人数与发饷比例，军饷需求一律由人数乘常数现算，不再有第二处存"军饷需求"。
PAY_PER_MAN = 1.5
MAINT_PER_MAN = 0.35

# 开局欠饷 按类别给月数，金额 = 人数 × PAY_PER_MAN × 月数：机动部队 6 个月、镇军 36 个月。
# 口径：**只在名册初次从 world/armies.json 装载时按此规则写入一次**，此后欠饷由结算与支付累积，
# 直到被真正付清 「军饷筹措」主线以清空欠饷为结束条件，故不再有季度自动清零。
OPENING_ARREARS_MONTHS = {"mobile": 6, "garrison": 36}


def opening_arrears_wan(unit: Dict[str, Any]) -> float:
    """该部队的开局欠饷 万两＝ 人数 × PAY_PER_MAN × 该类别的开局月数。"""
    months = OPENING_ARREARS_MONTHS.get(str(unit.get("category") or ""), 0)
    if not months:
        return 0.0
    return round(int(unit.get("count") or 0) * PAY_PER_MAN * months / 10000.0, 2)


def garrison_unit(pid: str, units: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """名册里该省的镇军条目 可写引用；镇军数据只有名册一份真相。"""
    if units is None:
        units = load_armies_data().get("units") or []
    for u in units:
        if u.get("category") == "garrison" and u.get("province_id") == pid:
            return u
    return None


def garrison_counts() -> Dict[str, int]:
    """各省镇军人数 取自名册；省份数据里不再存副本。"""
    out: Dict[str, int] = {}
    for u in load_armies_data().get("units") or []:
        if u.get("category") == "garrison" and u.get("province_id"):
            out[u["province_id"]] = int(u.get("count") or 0)
    return out


def garrison_pay_rate(pid: str, unit: Optional[Dict[str, Any]] = None) -> float:
    """发饷比例 0~1：名册 pay_rate 是唯一真相。"""
    if unit is None:
        unit = garrison_unit(pid)
    if unit and unit.get("pay_rate") is not None:
        return round(float(unit["pay_rate"]), 4)
    return 1.0


def garrison_pay_need(unit: Dict[str, Any]) -> int:
    """军饷需求 两/月= 人数 × 常数。"""
    return int(round(int((unit or {}).get("count") or 0) * PAY_PER_MAN))


def set_garrison_count(pid: str, count: float) -> bool:
    """改写某省镇军人数 名册唯一来源；省份数据里没有副本可写。"""
    data = load_armies_data()
    u = garrison_unit(pid, data.get("units") or [])
    if u is None:
        return False
    u["count"] = int(max(0.0, count))
    save_armies_data(data)
    return True


def set_garrison_pay_rate(pid: str, rate: float) -> bool:
    """改写某省镇军发饷比例 名册唯一来源。"""
    data = load_armies_data()
    u = garrison_unit(pid, data.get("units") or [])
    if u is None:
        return False
    u["pay_rate"] = round(min(1.0, max(0.0, float(rate))), 4)
    save_armies_data(data)
    return True


def select_units(units: List[Dict[str, Any]], **criteria: Any) -> List[Dict[str, Any]]:
    """按属性过滤名册 唯一过滤口。

    名册只有一份，识别一律靠属性：`select_units(units, category="mobile", owner="ming")`。
    不按名称、不按登记位置、不按下标判断任何东西。
    """
    return [u for u in (units or []) if all((u or {}).get(k) == v for k, v in criteria.items())]


def find_unit(units: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    """按 id 或名称在名册中找到那一条 AI 指令有时下发部队名。"""
    if not key:
        return None
    for u in units or []:
        if u.get("id") == key or u.get("name") == key:
            return u
    return None


def normalize_unit(unit: Dict[str, Any]) -> List[str]:
    """按词表补齐并校验标识；返回非法值清单 供日志与压力测试用。

    代码只认词表，不再按具体部队 id 写特判。
    """
    bad = []
    for key, (allowed, default) in TAGS.items():
        val = unit.get(key)
        if val is None or val == "":
            unit[key] = default
        elif val not in allowed:
            bad.append("%s=%s" % (key, val))
    return bad

def load_armies_data() -> Dict[str, Any]:
    """读取军队名册 纯内存，不写回文件，重启自动恢复初始状态。

    开局欠饷按 `OPENING_ARREARS_MONTHS` 规则写入**一次** 机动 6 个月、镇军 36 个月；
    之后欠饷由月度结算与支付累积，直到被真正付清。
    """
    global _RUNTIME_ARMIES
    if _RUNTIME_ARMIES is None:
        with open(ARMIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        units = data["units"]
        if not isinstance(units, list):
            raise TypeError("world/armies.json: 'units' must be a list")
        for u in units:
            u["arrears_wan"] = opening_arrears_wan(u)
        _RUNTIME_ARMIES = data
    return _RUNTIME_ARMIES


def save_armies_data(data: Dict[str, Any]) -> None:
    """仅保存到内存中，不持久化写入磁盘文件。"""
    global _RUNTIME_ARMIES
    _RUNTIME_ARMIES = data


def default_garrison_equipment(count: int) -> Dict[str, List[Dict[str, Any]]]:
    """镇军制式装备编制 按在编人数编成：军册只记人、记归属、记状态，装备按编制派生。"""
    return {
        "armors": [
            {"era_model": "万历十三年布面甲", "count": int(count * 0.45)},
            {"era_model": "万历元年式柳条齐头软藤甲", "count": int(count * 0.35)},
            {"era_model": "洪武年式皮甲", "count": int(count * 0.20)}
        ],
        "melee": [
            {"era_model": "隆庆元年式透甲枪", "count": int(count * 0.50)},
            {"era_model": "万历四十二年式雁翎佩刀", "count": int(count * 0.45)},
            {"era_model": "洪武二十六年式长柄斩马刀", "count": int(count * 0.05)}
        ],
        "ranged": [
            {"era_model": "永乐二十年式开元木弓", "count": int(count * 0.40)},
            {"era_model": "嘉靖三十七年式鸟嘴铳", "count": int(count * 0.30)},
            {"era_model": "嘉靖四十四年式三眼铳", "count": int(count * 0.15)}
        ],
        "artillery": [
            {"era_model": "嘉靖二十三年造三十斤虎蹲炮", "count": max(2, int(count / 300))},
            {"era_model": "嘉靖二年造五百斤四号佛郎机", "count": max(2, int(count / 500))}
        ],
        "horses": [
            {"grade": "上等战马", "count": int(count * 0.02)},
            {"grade": "中等战马", "count": int(count * 0.05)},
            {"grade": "下等驭马", "count": int(count * 0.12)}
        ]
    }


def get_all_armies(state: Any = None) -> List[Dict[str, Any]]:
    """名册全量 扁平一份，每一条自带 category / owner / location / status 等属性。

    名册是**独立的一份** world/armies.json：任何势力的任何部队都登记在这里。
    本函数只做归一与派生，不做筛选：调用方一律用 select_units() 按属性过滤。
    标识 category/owner/status/mission/overall_stance/engagement_risk/retreat_priority/
    battle_order一律由 normalize_unit() 按 TAGS 词表补齐并校验，代码不按部队 id 写特判。
    """
    data = load_armies_data()
    out = []
    for raw in data.get("units") or []:
        u = dict(raw)
        cnt = int(u.get("count") or 0)
        u["monthly_pay"] = round(cnt * PAY_PER_MAN / 10000.0, 2)
        u["monthly_maint"] = round(cnt * MAINT_PER_MAN / 10000.0, 2)
        u["arrears_wan"] = round(float(u.get("arrears_wan", 0.0)), 2)
        if not u.get("current_location"):
            u["current_location"] = u.get("province_id")
        if not u.get("target_location"):
            u["target_location"] = u["current_location"]
        normalize_unit(u)
        u["owner_name"] = owner_label(u.get("owner"))   # 归属中文名：后端一处给，前端不再自己映射
        # 军队修正：修正只有一个物种，显示位置＝这支军队，就在它的详情里列出来
        from .modifiers import department_modifier_rows
        u["modifier_rows"] = department_modifier_rows(state, u.get("name", ""))
        if u.get("mission") in  ("决战", "巡逻" and not u.get("_stance_explicit")):
            u["overall_stance"] = "进攻"
        if u.get("category") == "garrison" and not u.get("equipment"):
            u["equipment"] = default_garrison_equipment(cnt)
        u["pay_rate"] = garrison_pay_rate(u.get("province_id"), u)
        u["recon_value"] = calc_recon_value(u)
        u["current_location_name"] = PROVINCE_NAMES.get(u["current_location"], u["current_location"])
        u["target_location_name"] = PROVINCE_NAMES.get(u["target_location"], u["target_location"])
        out.append(u)
    return out


def get_province_armies(pid: str, state: Any = None) -> Dict[str, Any]:
    """获取指定省份的军队 己方 / 敌方。

    识别只看名册属性：**owner == "ming" 为明属 己方，其余归属一律敌方**
     后金、蒙古诸部、土司叛乱、民变起义军等同理。不按它登记在哪一组来判断。
    """
    all_armies = get_all_armies(state)

    friendly = []
    enemy = []
    seen = set()
    for u in all_armies:
        loc = u.get("current_location") or u.get("province_id")
        if loc != pid and u.get("province_id") != pid:
            continue
        key = u.get("id")
        if key in seen:
            continue
        seen.add(key)
        (friendly if is_friendly(u) else enemy).append(u)

    total_friendly_men = sum(int(a.get("count") or 0) for a in friendly)
    total_enemy_men = sum(int(a.get("count") or 0) for a in enemy)

    return {
        "province_id": pid,
        "province_name": PROVINCE_NAMES.get(pid, pid),
        "friendly": friendly,
        "enemy": enemy,
        "total_friendly_men": total_friendly_men,
        "total_enemy_men": total_enemy_men,
        "has_battle": total_friendly_men > 0 and total_enemy_men > 0,
        "has_enemy": total_enemy_men > 0,
    }


def get_army_by_id(army_id: str, state: Any = None) -> Optional[Dict[str, Any]]:
    """根据军队 ID 获取军队完整数据 机动部队、地方镇军、反叛军同走一份名册。"""
    u = find_unit(get_all_armies(state), army_id)
    return dict(u) if u else None


def dispatch_army(army_id: str, target_pid: str, state: Any = None,
                  owner: Optional[str] = None) -> Tuple[bool, str]:
    """指令某军队向目标省份调动行军。如果重新选择目标，则从当前所在省份重新规划路径。

    名册只有一份、任何势力的部队都在里面，所以下令必须带**归属**：
    `owner` 是下令一方的归属 缺省为明，即兵部/玩家通道；只能调遣本国部队，
    军令也从本国中枢发出 见 OWNERS 的 origin。
    """
    if not target_pid:
        return False, "未指定目标省份"

    data = load_armies_data()
    target_name = PROVINCE_NAMES.get(target_pid, target_pid)

    # 1. 按属性找到这条部队 名册只有一份，机动与镇军靠 category 区分
    unit = find_unit(data.get("units") or [], army_id)
    if unit is None:
        return False, "未找到该部队"

    # 2. 归属校验：只能调本国部队
    cmd_owner = owner or "ming"
    unit_owner = unit.get("owner", "ming")
    if unit_owner != cmd_owner:
        return False, "【%s】不归%s调遣。" % (unit.get("name", army_id), owner_label(cmd_owner))

    # 3. 地方镇军守备本省卫所，不可调离
    if unit.get("category") == "garrison":
        return False, "地方镇军负责守备本省卫所，不可跨省调动。"

    m = unit
    cur_loc = m.get("current_location", m.get("province_id", "sichuan"))
    if cur_loc == target_pid:
        m["target_location"] = target_pid
        m["status"] = "驻防"
        m["remain_days"] = 0.0
        m["total_days"] = 0.0
        m["path"] = [target_pid]
        m["path_names"] = [target_name]
        save_armies_data(data)
        return True, f"【{m['name']}】已进驻{target_name}，就地驻防。"

    # 1. 本国中枢发令至部队当前驻地 传骑时间；无中枢的势力就在本省发令 0 日
    origin = order_origin(cmd_owner)
    if origin and origin != cur_loc:
        order_res = find_shortest_path(origin, cur_loc, state, base_speed=350.0)
        order_days = float(order_res.get("time_days", 0.0))
    else:
        order_days = 0.0

    # 2. 部队行军时间 旧位置至新位置
    march_res = find_shortest_path(cur_loc, target_pid, state, base_speed=250.0)
    march_days = float(march_res.get("time_days", 1.0))
    march_segs = march_res.get("segments", [])

    m["target_location"] = target_pid
    m["target_location_name"] = target_name
    m["status"] = "候旨开拔" if order_days > 0 else "行军中"
    m["order_remain_days"] = round(order_days, 1)
    m["path"] = march_res["path"]
    m["path_names"] = march_res["path_names"]
    m["segments"] = march_segs
    m["seg_idx"] = 0
    m["seg_remain_days"] = float(march_segs[0]["time_days"]) if march_segs else 0.0
    m["total_days"] = round(order_days + march_days, 1)
    m["remain_days"] = round(order_days + march_days, 1)
    m["total_distance_km"] = march_res["dist_km"]
    save_armies_data(data)
    route_str = " → ".join(march_res["path_names"])
    cur_name = PROVINCE_NAMES.get(cur_loc, cur_loc)
    label = owner_label(cmd_owner)
    order_txt = ("自%s发令，" % PROVINCE_NAMES.get(origin, origin)) if order_days > 0 else ""
    CONSOLE.log("army", f"【{m['name']}】调兵发令", f"{label}{order_txt}调【{m['name']}】自{cur_name}开拔{target_name} 传令需{order_days:.1f}日，行军需{march_days:.1f}日，途经：{route_str}，共预计{order_days+march_days:.1f}日达")
    head = "兵部发令快马已出京" if cmd_owner == "ming" else "%s军令已下" % label
    return True, f"{head}：传檄【{m['name']}】自{cur_name}开拔{target_name} 传令需 {order_days:.1f} 日，行军途经 {route_str} 需 {march_days:.1f} 日，预计共 {order_days+march_days:.1f} 日抵达。"


def update_army_arrears(army_id: str, arrears_wan: float, state: Any = None) -> bool:
    """更新某部队所欠军饷金额 万两，记录在兵部军册 world/armies.json。"""
    data = load_armies_data()
    arrears_wan = max(0.0, round(float(arrears_wan), 2))

    unit = find_unit(data.get("units") or [], army_id)
    if unit is None:
        return False
    unit["arrears_wan"] = arrears_wan
    save_armies_data(data)
    return True


def clear_arrears(state: Any = None) -> None:
    """清空全国欠饷 测试与调试用。

    注意：这**不再是**季度自动行为。欠饷现在是「军饷筹措」主线的目标，
    只有在军饷真正付清时才减少，所以季度自动清零已删除。
    """
    data = load_armies_data()
    for u in data.get("units") or []:
        u["arrears_wan"] = 0.0
    save_armies_data(data)


def calc_recon_value(unit: Dict[str, Any]) -> float:
    """计算军队侦察值：
    基础侦察值 = 人数 × [1 +  5 × 精锐值 × 0.01 -  铠甲数量 / 军队人数 +  5 × 马匹数量 / 军队人数] -  重炮数量 × 100
    任务指令修正：
    - 决战：额外提供 +100% 侦察值 × 2.0
    - 守备：侦察值降低到原来的 30% × 0.3
    - 巡逻：常规侦察 × 1.0
    """
    cnt = max(1, int(unit.get("count", 0)))
    elite = float(unit.get("elite", 50))
    eq = unit.get("equipment") or {}

    # 1. 铠甲数量
    armor_count = sum(int(r.get("count", 0)) for r in eq.get("armors", []))

    # 2. 马匹数量
    horse_count = sum(int(r.get("count", 0)) for r in eq.get("horses", []))

    # 3. 重炮数量 (artillery)
    artillery_count = sum(int(r.get("count", 0)) for r in eq.get("artillery", []))

    bracket = 1.0 + (5.0 * elite * 0.01) - (armor_count / cnt) + (5.0 * horse_count / cnt)
    base_recon = max(0.0, (cnt * bracket) - (artillery_count * 100.0))

    # 任务指令姿态乘数
    mission = unit.get ("mission", "守备")
    if mission == "决战":
        multiplier = 2.0  # 决战额外提供100%侦察值
    elif mission == "守备":
        multiplier = 0.3  # 守备侦察值降低到原来的30%
    else:
        multiplier = 1.0  # 巡逻或其它保持1.0

    return round(base_recon * multiplier, 1)


def update_army_naval_posture(army_id: str,
                              mission: Optional[str] = None,
                              risk: Optional[str] = None,
                              retreat: Optional[str] = None,
                              battle_order: Optional[str] = None,
                              owner: Optional[str] = None) -> Tuple[bool, str]:
    """修改部队任务指令 决战/巡逻/守备、交战风险、撤退优先级或战役指令。

    `owner` 为下令一方归属 缺省明：只能改本国部队的指令。
    """
    data = load_armies_data()
    unit = find_unit(data.get("units") or [], army_id)
    if unit is None:
        return False, f"未找到部队【{army_id}】。"

    cmd_owner = owner or "ming"
    if unit.get("owner", "ming") != cmd_owner:
        return False, "【%s】不归%s调遣。" % (unit.get("name", army_id), owner_label(cmd_owner))

    valid_missions = ["决战", "巡逻", "守备"]
    valid_risks = ["避战", "低风险交战", "中风险交战", "高风险交战", "逢敌必战"]
    valid_retreats = ["从不撤退", "低撤退优先级", "高撤退优先级"]

    if mission in valid_missions:
        unit["mission"] = mission
        unit["overall_stance"] = "进攻" if mission in  ("决战", "巡逻") else "防御"
    if risk in valid_risks:
        unit["engagement_risk"] = risk
    if retreat in valid_retreats:
        unit["retreat_priority"] = retreat
    if battle_order is not None:
        unit["battle_order"] = str(battle_order).strip()

    save_armies_data(data)
    return True, "已成功更新部队战备姿态与交战指令。"


def update_army_stance_and_order(army_id: str, overall_stance: Optional[str] = None, battle_order: Optional[str] = None) -> Tuple[bool, str]:
    """修改部队的总体状态 进攻/防御或作战指令。"""
    mission = "决战" if overall_stance == "进攻" else  "守备" if overall_stance == "防御" else None
    return update_army_naval_posture(army_id, mission=mission, battle_order=battle_order)


def update_equipment_quota(army_id: str, cat_key: str, model_name: str, new_quota: int) -> Tuple[bool, str]:
    """修改部队某型装备的编制数量。"""
    new_quota = max(0, int(new_quota))
    data = load_armies_data()
    unit = find_unit(data.get("units") or [], army_id)
    if unit is None:
        return False, f"未找到部队【{army_id}】的装备【{model_name}】。"

    eq = unit.setdefault("equipment", {})
    rows = eq.setdefault(cat_key, [])
    for r in rows:
        if r.get("era_model") == model_name or r.get("grade") == model_name:
            r["quota"] = new_quota
            save_armies_data(data)
            return True, f"已更新【{army_id}】之【{model_name}】编制数量为 {new_quota}。"
    return False, f"未找到部队【{army_id}】的装备【{model_name}】。"


def delete_equipment_row(army_id: str, cat_key: str, model_name: str, state: Any = None) -> Tuple[bool, str, int]:
    """删除部队某型装备，在役数量直接回收进工部库存。"""
    data = load_armies_data()
    unit = find_unit(data.get("units") or [], army_id)
    if unit is not None:
        eq = unit.setdefault("equipment", {})
        rows = eq.get(cat_key, [])
        matched = [r for r in rows if r.get("era_model") == model_name or r.get("grade") == model_name]
        if matched:
            reclaimed_count = int(matched[0].get("count", 0))
            eq[cat_key] = [r for r in rows if r.get("era_model") != model_name and r.get("grade") != model_name]
            save_armies_data(data)
            if state and reclaimed_count > 0:
                from .depts_works import get_works_inventory
                inv = get_works_inventory(state)
                inv[model_name] = inv.get(model_name, 0) + reclaimed_count
            return True, f"已从【{army_id}】裁撤装备【{model_name}】，在役 {reclaimed_count} 件已回收至工部库存。", reclaimed_count
    return False, f"未找到部队【{army_id}】的装备【{model_name}】。", 0


def add_equipment_row(army_id: str, cat_key: str, model_name: str, quota: int = 1000) -> Tuple[bool, str]:
    """为部队新增一行装备编制。"""
    data = load_armies_data()
    quota = max(0, int(quota))
    unit = find_unit(data.get("units") or [], army_id)
    if unit is None:
        return False, "添加失败：该装备可能已在编制中或未找到部队。"

    eq = unit.setdefault("equipment", {})
    rows = eq.setdefault(cat_key, [])
    if any(r.get("era_model") == model_name or r.get("grade") == model_name for r in rows):
        return False, "添加失败：该装备可能已在编制中或未找到部队。"
    if cat_key == "horses":
        rows.append({"grade": model_name, "count": 0, "quota": quota, "desc": "新编入营马配置"})
    else:
        rows.append({"era_model": model_name, "count": 0, "quota": quota, "desc": "新编入军械序列"})
    save_armies_data(data)
    return True, f"已在【{army_id}】新增装备编制【{model_name}】 编制数 {quota}。"


def tick_armies_daily(state: Any, days: int = 1) -> List[str]:
    """每日推进：部队行军与抵达驻防，以及工部库存装备增援配发逻辑 需求5。"""
    notes = []
    if days <= 0:
        return notes

    data = load_armies_data()
    changed = False

    # ── 装备增援逻辑 需求5：每日检测，按编制数增援，优先机动部队，同级平均分配──
    # 军械取自工部仓储；马匹取自太仆寺常盈库马政库存。
    if state is not None:
        from .depts_works import get_works_inventory
        from .depts_taipusi import get_taipusi_horses, horse_names
        works_inv = get_works_inventory(state)
        horse_inv = get_taipusi_horses(state)

        # sources: 装备名 -> 其所属库存字典 增援结果写回该字典
        sources = {}
        for k in works_inv:
            sources[k] = works_inv
        for k in horse_names():
            sources[k] = horse_inv

        # 收集所有有编制缺口的部队装备行
        # 对每型装备分别按  机动军团 -> 地方镇军 分层增援
        all_models = list(sources.keys())
        for model_name in all_models:
            inventory = sources[model_name]
            avail = inventory.get(model_name, 0)
            if avail <= 0:
                continue

            # 1. 检查机动部队缺口
            mobile_demands = []
            for m in select_units(data.get("units") or [], category="mobile"):
                for cat, rows in (m.get("equipment") or {}).items():
                    for r in rows:
                        m_name = r.get("era_model") or r.get("grade")
                        if m_name == model_name:
                            cur_cnt = int(r.get("count", 0))
                            quota = int(r.get("quota", cur_cnt))
                            if quota > cur_cnt:
                                mobile_demands.append((r, quota - cur_cnt))

            if mobile_demands and avail > 0:
                # 平均分配到所有有缺口的机动部队
                num_receivers = len(mobile_demands)
                share_per_unit = max(1, avail // num_receivers)
                for r, gap in mobile_demands:
                    give = min(gap, share_per_unit, avail)
                    if give > 0:
                        r["count"] = int(r.get("count", 0)) + give
                        avail -= give
                        changed = True
                    if avail <= 0:
                        break

            # 2. 若机动部队配满后仍有剩余库存，供给地方镇军
            if avail > 0:
                garrison_demands = []
                for entry in select_units(data.get("units") or [], category="garrison"):
                    for cat, rows in (entry.get("equipment") or {}).items():
                        for r in rows:
                            m_name = r.get("era_model") or r.get("grade")
                            if m_name == model_name:
                                cur_cnt = int(r.get("count", 0))
                                quota = int(r.get("quota", cur_cnt))
                                if quota > cur_cnt:
                                    garrison_demands.append((r, quota - cur_cnt))

                if garrison_demands and avail > 0:
                    num_receivers = len(garrison_demands)
                    share_per_unit = max(1, avail // num_receivers)
                    for r, gap in garrison_demands:
                        give = min(gap, share_per_unit, avail)
                        if give > 0:
                            r["count"] = int(r.get("count", 0)) + give
                            avail -= give
                            changed = True
                        if avail <= 0:
                            break

            inventory[model_name] = avail

    # 推进在途部队 候旨开拔 → 行军中 → 抵达驻防，机动与镇军同走一份名册
    for m in data.get("units") or []:
        st_val = m.get("status")

        # 阶段 A：候旨开拔中 中枢令箭快马在途
        if st_val == "候旨开拔":
            ord_rem = float(m.get("order_remain_days", 0.0)) - days
            m["remain_days"] = max(0.0, round(float(m.get("remain_days", 0.0)) - days, 1))
            if ord_rem <= 0:
                m["status"] = "行军中"
                m["order_remain_days"] = 0.0
                cur_n = m.get("current_location_name", m.get("current_location", ""))
                route_str = " → ".join(m.get("path_names", []))
                note = f"【军令送达】朝廷调兵公文抵达{cur_n}，【{m['name']}】受命拔营起行，沿途经【{route_str}】向目的地进军！"
                notes.append(note)
                CONSOLE.log("army", f"【{m['name']}】拔营开拔", note)
            else:
                m["order_remain_days"] = round(ord_rem, 1)
            changed = True
            continue

        # 阶段 B：正式行军中 沿途经过各省节点逐段推进
        if st_val == "行军中":
            m["remain_days"] = max(0.0, round(float(m.get("remain_days", 0.0)) - days, 1))
            segs = m.get("segments", [])
            seg_idx = m.get("seg_idx", 0)
            seg_rem = float(m.get("seg_remain_days", 0.0)) - days

            if segs and seg_idx < len(segs):
                if seg_rem <= 0:
                    # 走完当前分段，进入下一省份节点
                    seg_idx += 1
                    if seg_idx < len(segs):
                        m["seg_idx"] = seg_idx
                        cur_node_pid = segs[seg_idx]["from_id"]
                        cur_node_name = segs[seg_idx]["from_name"]
                        m["current_location"] = cur_node_pid
                        m["current_location_name"] = cur_node_name
                        m["seg_remain_days"] = float(segs[seg_idx]["time_days"])
                        tgt_name = m.get("target_location_name", "")
                        note = f"【行军进次】机动军团【{m['name']}】行军抵达【{cur_node_name}】境内，正继续奔赴{tgt_name} 尚余{m['remain_days']:.1f}日。"
                        notes.append(note)
                        CONSOLE.log("army", f"【{m['name']}】途经{cur_node_name}", note)
                    else:
                        # 全部路段走完，正式进驻目的地
                        tgt = m.get("target_location", "beizhili")
                        tgt_name = PROVINCE_NAMES.get(tgt, tgt)
                        m["current_location"] = tgt
                        m["current_location_name"] = tgt_name
                        m["status"] = "驻防"
                        m["remain_days"] = 0.0
                        m["station"] = f"{tgt_name}营防"
                        note = f"【军队调动】机动军团【{m['name']}】已全军行军抵达【{tgt_name}】进驻防守！"
                        notes.append(note)
                        CONSOLE.log("army", f"【{m['name']}】抵达目的地", f"已进驻{tgt_name}")
                else:
                    m["seg_remain_days"] = round(seg_rem, 1)
            else:
                # 容错降级
                rem = float(m.get("remain_days", 0.0)) - days
                if rem <= 0:
                    tgt = m.get("target_location", "beizhili")
                    tgt_name = PROVINCE_NAMES.get(tgt, tgt)
                    m["current_location"] = tgt
                    m["current_location_name"] = tgt_name
                    m["status"] = "驻防"
                    m["remain_days"] = 0.0
                    m["station"] = f"{tgt_name}营防"
                    note = f"【军队调动】机动军团【{m['name']}】已行军抵达{tgt_name}进驻防守。"
                    notes.append(note)
                else:
                    m["remain_days"] = round(rem, 1)
            changed = True

    if changed:
        save_armies_data(data)

    return notes


def tick_military_expenses_monthly(state: Any) -> List[str]:
    """月度军饷结算 包含中央腐败与军队腐败衍生机制：

    - 计算全军 机动部队 + 地方镇军本月所需军饷与维护费。
    - 扣除逻辑 需求4：
      1. 中央腐败：在军队腐败开始计算前，在每次军饷下发时，从所有军饷中扣除【中央腐败%】到地方窖藏；
      2. 军队腐败：从国库结算到军队银两时，对剩余部分扣除【军队腐败%】到地方窖藏，被扣除的也同时计入军队欠饷；
      3. 国库不足时产生的欠发缺口同样累加至欠饷 arrears_wan。
    """
    notes = []
    if state is None:
        return notes

    all_armies = get_all_armies(state)
    # 国库只发明属军饷：后金八旗、蒙古盟军、各部牛录与部落兵不在明军饷之列
    mobiles = select_units(all_armies, category="mobile", owner="ming")
    garrisons = select_units(all_armies, category="garrison", owner="ming")

    total_need = 0.0
    for m in mobiles:
        total_need += float(m.get("monthly_pay", 0.0)) + float(m.get("monthly_maint", 0.0))
    for g in garrisons:
        total_need += float(g.get("monthly_pay", 0.0)) + float(g.get("monthly_maint", 0.0))
    total_need = round(total_need, 2)

    if total_need <= 0:
        return notes

    treasury = float(state.treasury)
    actual_dispatched_from_treasury = min(treasury, total_need)
    shortfall = round(total_need - actual_dispatched_from_treasury, 2)
    state.treasury = round(max(0.0, state.treasury - actual_dispatched_from_treasury), 2)

    # 1. 中央腐败扣除 在军饷下发时从下发总额中扣除，截留转入地方窖藏
    from .depts_libu import calc_central_corruption
    central_corr_info = calc_central_corruption(state)
    central_corr_rate = max(0.0, min(1.0, float(central_corr_info.get("value", 82.5)) / 100.0))
    central_pay_retained = round(actual_dispatched_from_treasury * central_corr_rate, 2)
    pay_after_central = actual_dispatched_from_treasury - central_pay_retained

    # 2. 军队腐败扣除 对剩余部分扣除各部队腐败%，转入地方窖藏，同时扣除部分直接计入军队欠饷
    # 全军同走一份名册：归属看 owner、编制看 category，欠饷只记在名册里 不向省份写副本
    data = load_armies_data()
    total_army_corr_retained = 0.0

    for unit in select_units(data.get("units") or [], owner="ming"):
        if unit.get("category") == "garrison":
            # 地方镇军腐败取该部队自身属性
            corr_default = 15
        else:
            corr_default = 12
        u_req = (float(unit.get("count") or 0) * 1.85) / 10000.0
        u_prop = (u_req / total_need) if total_need > 0 else 0.0
        u_dispatched = actual_dispatched_from_treasury * u_prop
        u_after_central = u_dispatched * (1.0 - central_corr_rate)

        corr_rate = max(0.0, min(1.0, float(unit.get("corruption", corr_default)) / 100.0))
        corr_retained = round(u_after_central * corr_rate, 2)
        total_army_corr_retained += corr_retained

        # 军队被克扣部分 + 国库缺发部分 均计入欠饷
        u_shortfall = round(u_req * (shortfall / total_need) if total_need > 0 else 0.0, 2)
        unit["arrears_wan"] = round(float(unit.get("arrears_wan", 0.0)) + corr_retained + u_shortfall, 2)

    save_armies_data(data)

    # 被中央腐败和军队腐败截留的银两沉淀至地方银库窖藏
    total_retained_to_local = round(central_pay_retained + total_army_corr_retained, 2)
    state.local_reserve = round(float(state.local_reserve) + total_retained_to_local, 2)

    state.national_ledger.append({
        "date": dict(state.date),
        "type": "expense",
        "category": "军饷军费",
        "amount": actual_dispatched_from_treasury,
        "desc": f"全国各军月度军饷实拨 {actual_dispatched_from_treasury:.2f}万两 中央腐败截留 {central_pay_retained:.2f}万两，军队腐败克扣 {total_army_corr_retained:.2f}万两转入地方窖藏",
    })

    if shortfall > 0:
        notes.append(f"兵部军饷发放：国库支发 {actual_dispatched_from_treasury:.2f}万两 缺发 {shortfall:.2f}万两。因中央腐败截留 {central_pay_retained:.2f}万两、将校克扣 {total_army_corr_retained:.2f}万两，所克扣与缺发部分已如数计入各部欠饷！")
    else:
        notes.append(f"兵部军饷发放：国库如额拨发 {actual_dispatched_from_treasury:.2f}万两。经中枢截留 {central_pay_retained:.2f}万两、军营克扣 {total_army_corr_retained:.2f}万两转入地方窖藏，克扣款已计入欠饷。")

    return notes
