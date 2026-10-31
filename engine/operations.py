# -*- coding: utf-8 -*-
"""大明王朝 1627 全域超控指令执行系统（Operations System）。

两道派发器，各管一套指令，互不越界：

  execute_operation_command  全域指令。玩家中旨、廷议、部门 AI、事件 impact 都走它；
                             认不出就返回「无法识别的指令」。
  execute_ai_operations      国家 AI 专用（宣战／求和／贸易／军队调动／修改军队战役指令）。
                             这些指令全域派发器不认，只随季度决议下发。

唯一真相源是本模块末尾的 OVERRIDE_COMMANDS：每条指令的写法、参数说明、可抄的示例、
真正执行它的函数名（impl）与归属（ai=True 即国家 AI 专用）都只写在那张表里。
AI 提示词里的指令清单（operations_schema_from_table）与设置页的两张表都由它生成，
不许再手写第二份。

修正只有一个物种：create_modifier / modify_modifier / delete_modifier，
「显示位置」是参数（部门名进该部门修正栏、省名进该省地区修正表），不再有部门修正／
地区修正／法律／行政纪律各一套的旧口径。效果栏的合法用词见 MODIFIER_TERMS。

月度推进的三个入口：tick_local_modifiers_monthly、apply_flow_modifiers_monthly、
tick_construction_monthly。
"""

import json
import os
import random
import re
from typing import Any, Dict, List, Optional, Tuple
from .time import date_str
from .logger import CONSOLE

PROV_MAP = {
    "北直隶": "beizhili", "南直隶": "nanzhili", "山东": "shandong", "山西": "shanxi",
    "河南": "henan", "陕西": "shaanxi", "四川": "sichuan", "湖广": "huguang",
    "江西": "jiangxi", "浙江": "zhejiang", "福建": "fujian", "广东": "guangdong",
    "广西": "guangxi", "云南": "yunnan", "贵州": "guizhou", "辽东": "liaodong",
    "东番": "dongfan", "盛京": "shengjing", "海西": "haixi", "嫩科尔沁": "nenkeerqin",
    "索伦": "suolun", "东海": "donghai", "苦兀": "kuwu", "兀良哈": "wuliangha",
    "察哈尔": "chahar", "土默特": "tumote", "鄂尔多斯": "ordos",
    # 同义写法：人物名册里「京师」指京畿所在的北直隶
    "京师": "beizhili",
}
PROV_REV = {v: k for k, v in PROV_MAP.items()}
CLASSES = ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]

# 阶级词表与解析：只在这里定义一次。修正汇总、产能计算、提示词注入一律调用 classes_in()，
# 别处不再各写一套缩写对照 本文件同时是超控指令解析器的所在地。
CLASS_ALIAS = {
    "宗室": "宗室", "士绅": "士绅", "士": "士绅",
    "商人": "商人", "商": "商人",
    "农民": "农民", "农": "农民",
    "百工": "百工", "工": "百工",
    "百役": "百役", "役": "百役",
    "流民": "流民", "流": "流民",
}
ALL_CLASSES_ALIAS =  "全阶级", "所有阶级", "全"


def classes_in(text) -> List[str]:
    """把「商/农/工/役」「宗室、士绅」「全阶级」这类写法解析成阶级名列表 按 CLASSES 顺序。"""
    raw = re.sub(r"[ (].*?[)]", "", str(text or ""))
    found = []
    for tok in re.split(r"[/、,，\s]+", raw):
        tok = tok.strip()
        if not tok:
            continue
        if tok in ALL_CLASSES_ALIAS:
            return list(CLASSES)
        c = CLASS_ALIAS.get(tok)
        if c and c not in found:
            found.append(c)
    return [c for c in CLASSES if c in found]

# 修正作用的范围：解析出的 target → 范围名 None 表示"该省"。
# 地区修正表只收录地方范围的条目；"中央"是中枢指标，不属于地区修正。
_SCOPE_OF_TARGET = {
    "all_local_corruption": "全国",
    "capacity": "全国",
    "attitude": "全国",
    "prov_corruption": None,
    "central_corruption": "中央",
    "prestige": "中央",
    "financial_rate": "中央",
}


def _get_prov_id(p_name: str) -> Optional[str]:
    """省名或省 id → 省 id。**认不出就返回 None**。

    原来认不出会兜底成 beizhili（北直隶），于是「创建修正 刑部 …」这种把部门当省份的
    写法会悄悄落到北直隶头上。口径：查不到就说查不到，由调用方报「未找到省份」。
    """
    if not p_name:
        return None
    p_name = str(p_name).strip()
    if p_name in PROV_REV:
        return p_name
    for k, v in PROV_MAP.items():
        if k in p_name or v == p_name.lower():
            return v
    return None


def _extract_prov_and_class(text: str) -> Tuple[str, Optional[str]]:
    matched_p = None
    matched_c = None
    for c in CLASSES:
        if c in text:
            matched_c = c
            break
    for k, v in PROV_MAP.items():
        if k in text:
            matched_p = v
            break
        elif re.search(rf"\b{v}\b", text, re.IGNORECASE):
            matched_p = v
            break
    return matched_p or "beizhili", matched_c


def _record_national_tx(state, txtype: str, amount: float, item: str, desc: str):
    """向国库记入一笔真实账单交易。"""
    ledger = state.national_ledger
    ledger.insert(0, {
        "date": date_str(state.date),
        "type": txtype,
        "amount": round(amount, 2),
        "item": item,
        "desc": desc,
    })
    if len(ledger) > 100:
        state.national_ledger = ledger[:100]


def _record_privy_tx(state, txtype: str, amount: float, item: str, desc: str):
    """向私库 私库记入一笔真实账单交易。"""
    ledger = state.privy_ledger
    ledger.insert(0, {
        "date": date_str(state.date),
        "type": txtype,
        "amount": round(amount, 2),
        "item": item,
        "desc": desc,
    })
    if len(ledger) > 100:
        state.privy_ledger = ledger[:100]


# ═══════════════════════════════════════════════════════════
# 一、 地方超控接口 (Local Overrides)
# ═══════════════════════════════════════════════════════════

def delete_army(state, province: str, army_name_or_id: str) -> Tuple[bool, str]:
    """1. 删除任何军队 包括己方和敌方的。"""
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    target = army_name_or_id.strip()

    from .armies import load_armies_data, save_armies_data, find_unit
    data = load_armies_data()
    units = data.get("units") or []
    deleted = False

    target_unit = find_unit(units, target)
    if target_unit is not None and (not pid or target_unit.get("province_id") == pid or target_unit.get("current_location") == pid):
        units = [u for u in units if u is not target_unit]
        data["units"] = units
        deleted = True

    save_armies_data(data)
    if deleted:
        return True, f"已从{pname}撤销/消灭编制军队【{target}】。"
    return False, f"未在{pname}找到军队【{target}】。"




def tick_local_modifiers_monthly(state) -> List[str]:
    """推进独立地区修正的剩余月数，并移除到期条目。"""
    notes = []
    for province in state.provinces.values():
        local = province.get("local") or {}
        modifiers = local.get("custom_modifiers") or []
        active = []
        for modifier in modifiers:
            if modifier.get("duration_months") is None:
                active.append(modifier)          # 永久修正：统一指令设立的地区修正
                continue
            remaining = int(modifier.get("duration_months", 0))
            if remaining <= 0:
                notes.append("【%s】地区修正【%s】已到期" %
                             (province.get("name", province.get("id", "")), modifier.get("name", "")))
                continue
            modifier["duration_months"] = remaining - 1
            if modifier["duration_months"] <= 0:
                notes.append("【%s】地区修正【%s】本月生效后到期" %
                             (province.get("name", province.get("id", "")), modifier.get("name", "")))
            else:
                active.append(modifier)
        local["custom_modifiers"] = active
    return notes




# ═══════════════════════════════════════════════════════════
# 二、 人物与党派超控接口 (Person & Faction Overrides)
# ═══════════════════════════════════════════════════════════

def set_person_faction(name: str, faction: str) -> Tuple[bool, str]:
    """修改人物党派 作用于人物卡 basic.阵营 与 officials.db 绑定。查不到人就报错。"""
    from .char_cards import get_card, save_card
    from .factions import normalize_faction, _OFFICIALS_FACTION_CACHE
    if not faction:
        return False, "党派不能为空。"
    # normalize_faction 会把认不出的词一律归到「无党派」，这里先按写法核对，
    # 写错（如「乱党」）直接报错，不能悄悄变成无党派
    key = faction.strip()
    if any(k in key for k in ("阉", "魏")):
        norm_f = "阉党"
    elif "东林" in key:
        norm_f = "东林党"
    elif key in ("无党派", "无党", "中立", "无"):
        norm_f = "无党派"
    else:
        return False, f"党派只认 阉党／东林党／无党派，收到【{faction}】。"
    if not _person_exists(name):
        return False, f"未找到人物【{name}】的档案卡或名录记录。"

    # 1. 更新人物卡
    c = get_card(name)
    if c:
        c.setdefault("basic", {})["阵营"] = norm_f
        save_card(name, c)

    # 2. 更新 officials.db 与内存缓存
    from .officials import _conn
    conn = _conn()
    conn.execute("UPDATE positions SET faction=? WHERE person=?", (norm_f, name))
    conn.commit()
    conn.close()
    _OFFICIALS_FACTION_CACHE[name] = norm_f
    return True, f"已将人物【{name}】的党派归属调整绑定为【{norm_f}】。"


def set_person_location(name: str, location: str, state: Any = None) -> Tuple[bool, str]:
    """修改人物位置 接入中枢发令传骑 + 角色沿途逐省移动系统。"""
    from .relay import dispatch_person_movement
    return dispatch_person_movement(name, location, state)


def set_person_office(name: str, office_title: str) -> Tuple[bool, str]:
    """修改人物官职 作用于人物卡 basic.官职 / state.现职 与 officials.db。查不到人就报错。

    职位之间的分隔一律用顿号「、」，历史数据里的间隔点「·」在这里就被换掉。"""
    office_title = (office_title or "").replace("·", "、")
    from . import positions as _pos
    if not _pos.exists(office_title):
        return False, ("职位库里没有【%s】这个职位。要新增职位请先用「增加部门职位 <部门> <职位>」，"
                       "不要在官职里写一个不存在的职位。" % office_title)
    if not _person_exists(name):
        return False, f"未找到人物【{name}】的档案卡或名录记录。"
    # 该人原有在任行一律卸任，再把目标职位交给他：官职表是唯一来源
    for row in _pos.by_person(name):
        _pos.update(row["id"], person="", status="vacant", end_date="1627-08")
    if not _pos.assign(office_title, name, "1627-08"):
        return False, f"【{office_title}】这个职位没有可任的槽位。"
    from .char_cards import get_card, save_card
    c = get_card(name)
    if c:
        c.setdefault("basic", {})["官职"] = office_title
        c.setdefault("state", {})["现职"] = office_title
        save_card(name, c)
    return True, f"已将人物【{name}】的官职头衔调整为【{office_title}】。"


def set_person_status(name: str, status: str) -> Tuple[bool, str]:
    """修改人物状态 仅从 在职 / 居乡 / 赋闲 / 死亡 中选择一个。

    状态写别的、或人名查不到，都直接报错——原来会悄悄当成「在职」并报成功。"""
    valid_statuses = ["在职", "居乡", "赋闲", "死亡"]
    # 名下有在任官职的人，状态锁死在【在职】，不能改成居乡/赋闲/死亡
    from .officials import has_active_office
    if status != "在职" and has_active_office(name):
        return False, f"【{name}】名下还有在任官职，状态锁定为【在职】，不能改为【{status}】。"
    if status not in valid_statuses:
        return False, f"状态只认 {'／'.join(valid_statuses)}，收到【{status}】。"
    if not _person_exists(name):
        return False, f"未找到人物【{name}】的档案卡或名录记录。"
    target_status = status

    from .char_cards import get_card, patch_state
    c = get_card(name)
    if c:
        patch_state(name, {"状态": target_status})
    from . import positions as _pos
    if target_status == "死亡":
        for row in _pos.by_person(name):
            _pos.update(row["id"], status="dead", end_date="1627-08")
    elif target_status in ("居乡", "赋闲"):
        for row in _pos.by_person(name):
            _pos.update(row["id"], status="removed", end_date="1627-08")
    else:
        for row in _pos.by_person(name):
            _pos.update(row["id"], status="active")
    return True, f"已将人物【{name}】的仕途状态调整为【{target_status}】。"




# ═══════════════════════════════════════════════════════════
# 三、 部门增删接口 (Departments Overrides)
# ═══════════════════════════════════════════════════════════

def create_dept(state, category: str, name: str, intro: str = "", staff: Optional[List[str]] = None,
                headcount: str = "") -> Tuple[bool, str]:
    """4. 增加一个部门。分类必填且只认「外廷」（外诸司）与「内廷」（二十四衙门），
    它决定这个衙署落在哪张登记表里，写错直接报错、不猜不兜底。"""
    if category not in ("外廷", "内廷"):
        return False, f"增加部门的分类只能写「外廷」或「内廷」，收到【{category}】。"
    if category == "内廷":
        from . import depts_neiting
        depts_neiting.DEPTS[name] = {
            "intro": intro or f"大明内廷新设机构 {name}",
            "alias": [name],
            "history": [{"era": "天启七年", "text": f"圣旨敕设{name}"}],
            "suboffices": [],
            "staff": [{"title": f"{name}掌印太监"}],
            "headcount": headcount or "掌印太监一员，属官若干",
            "routine": ["办理内廷特旨差使"],
            "figures": [],
            "anecdotes": [],
            "quotes": [],
            "sources": ["《大明会典》"]
        }
        # 编制也要进官职表：不然新部门打开是空表，铨选也无从补人
        from . import positions as _pos
        if not _pos.exists(f"{name}掌印太监", name):
            _pos.add(title=f"{name}掌印太监", kind="京官", category=name, start="1627-08")
        return True, f"已在内廷二十四衙门增设新机构【{name}】。"
    else:
        from . import depts_waizhusi
        depts_waizhusi.DEPTS[name] = {
            "intro": intro or f"大明外廷新设诸司 {name}",
            "alias": [name],
            "history": [{"era": "天启七年", "text": f"敕令增置{name}"}],
            "suboffices": [],
            "staff": [{"title": f"{name}正卿"}],
            "headcount": headcount or "正卿一人，少卿二人，属官员役若干",
            "routine": ["掌理特设外朝事务"],
            "figures": [],
            "anecdotes": [],
            "quotes": [],
            "sources": ["《明史职官志》"]
        }
        # 编制也要进官职表：不然新部门打开是空表，铨选也无从补人
        from . import positions as _pos
        if not _pos.exists(f"{name}正卿", name):
            _pos.add(title=f"{name}正卿", kind="京官", category=name, start="1627-08")
        return True, f"已在外廷增设新衙署机构【{name}】。"


def delete_dept(state, category: str, name: str) -> Tuple[bool, str]:
    """5. 删除一个部门。部门按名字在「外诸司／二十四衙门」两张表里找，
    category 参数只为兼容旧调用点保留，不参与判断；六部不属可裁撤之列。"""
    from . import depts, depts_neiting, depts_waizhusi
    deleted = False
    if name in depts_neiting.DEPTS:
        depts_neiting.DEPTS.pop(name, None)
        deleted = True
    if name in depts_waizhusi.DEPTS:
        depts_waizhusi.DEPTS.pop(name, None)
        deleted = True
    if deleted:
        # 编制一并撤掉：官职表里 category=本部门 的京官槽整批删除
        from . import positions as _pos
        for row in _pos.rows(kind="京官", category=name):
            _pos.remove(row["title"], name)
        return True, f"已正式裁撤衙署机构【{name}】。"
    if name in depts.DEPTS:
        return False, f"【{name}】是六部，不在可裁撤范围内（可裁的是外诸司与二十四衙门）。"
    return False, f"未找到待裁撤的衙署机构【{name}】。"


# ═══════════════════════════════════════════════════════════
# 三、 吏部超控接口 (Personnel Overrides)
# ═══════════════════════════════════════════════════════════

def replace_civil_official(state, province: str, office_title: str, old_name: str, new_name: str) -> Tuple[bool, str]:
    """6. 更换一个文册档案的官员。"""
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    from .officials import list_active, update_official
    for o in list_active():
        if (o.get("province") == pname or o.get("location") == pname or not province) and \
           (office_title in o.get("title", "") or not office_title) and \
           (o.get("name") == old_name or not old_name):
            update_official(o["id"], name=new_name)
            return True, f"吏部文册档案：已将{pname}【{o.get('title')}】由 {old_name} 更换为 {new_name}。"
    return False, f"未在{pname}文册中找到符合条件的官员 {old_name} {office_title}。"


def replace_dept_staff_unified(state, dept_name: str, arg2: str, arg3: str) -> Tuple[bool, str]:
    """更换中央官员：
    模式一：更换中央官员 [部门名] [官职] [新官] 如 更换中央官员 司礼监 司礼监掌印太监 王承恩
    模式二：更换中央官员 [部门名] [原官] [新官] 如 更换中央官员 司礼监 魏忠贤 王承恩
    """
    from .officials import list_active, update_official, add_official
    from .char_cards import get_card, patch_state

    # 先判定 arg2 是否匹配某在任官员姓名 即模式二：原官 -> 新官
    old_officer = None
    for o in list_active():
        if o.get("name") == arg2:
            old_officer = o
            break

    if old_officer:
        # 模式二：按原官换新官
        old_name = arg2
        new_name = arg3
        title = old_officer.get ("title", f"{dept_name}要职")
        update_official(old_officer["id"], name=new_name)
        # 旧官转赋闲
        if get_card(old_name):
            patch_state(old_name, {"状态": "赋闲", "现职": "致仕离任"})
        # 新官置在职
        if get_card(new_name):
            patch_state(new_name, {"状态": "在职", "现职": title, "驻地": "北直隶"})
        return True, f"【{dept_name}】官员任免：已将原官【{old_name}】去职，特简【{new_name}】接任【{title}】。"
    else:
        # 模式一：按职位换新官 arg2为官职，arg3为新官姓名
        office_title = arg2
        new_name = arg3
        matched_old = None
        for o in list_active():
            if office_title in o.get("title", "") and o.get("level") == "capital":
                matched_old = o
                break
        if matched_old:
            old_name = matched_old.get("name", "")
            update_official(matched_old["id"], name=new_name)
            if old_name and get_card(old_name):
                patch_state(old_name, {"状态": "赋闲", "现职": "致仕离任"})
            if get_card(new_name):
                patch_state(new_name, {"状态": "在职", "现职": office_title, "驻地": "北直隶"})
            return True, f"【{dept_name}】官员任免：已将【{office_title}】由【{old_name}】更换为【{new_name}】。"
        else:
            # 该职位无人（缺员槽）：直接补人，不新建职位行
            from . import positions as _pos
            if not _pos.assign(office_title, new_name, "1627-08"):
                return False, f"职位库里没有【{office_title}】这个职位，不能凭空任免。"
            if get_card(new_name):
                patch_state(new_name, {"状态": "在职", "现职": office_title, "驻地": "北直隶"})
            return True, f"【{dept_name}】官员任免：特简命【{new_name}】担任【{office_title}】。"


def delete_local_officer(state, officer_name: str) -> Tuple[bool, str]:
    """删除地方官员 [原官名] 弹劾革职撤缺专线，注销官缺，并将人物卡状态置为赋闲。"""
    from .officials import list_active, delete_official
    from .char_cards import get_card, patch_state

    officer_name = (officer_name or "").strip()
    found = False
    for o in list_active():
        if o.get("name") == officer_name:
            delete_official(o["id"])
            found = True

    c_card = get_card(officer_name)
    if c_card:
        patch_state((officer_name, {"状态": "赋闲", "现职": "致仕革职"}))
        found = True

    if found:
        return True, f"吏部案卷处分：已将地方官员【{officer_name}】正式革职削籍，注销官缺档案。"
    return False, f"未找到在任地方官员【{officer_name}】。"


def _person_exists(name: str) -> bool:
    """人名是否真的在人名录或人物卡里——「查不到就报错」的判据。"""
    from .char_cards import get_card
    try:
        if get_card(name):
            return True
    except Exception:
        pass
    try:
        from .officials import _conn
        conn = _conn()
        row = conn.execute("SELECT 1 FROM positions WHERE person=? LIMIT 1", (name,)).fetchone()
        conn.close()
        return bool(row)
    except Exception:
        return False


def add_dept_staff(state, dept_name: str, office_title: str, officer_name: Optional[str] = None) -> Tuple[bool, str]:
    """在六部、二十四衙门或外廷诸司管理层增设职位与配员。

    部门必须真的存在——否则会凭空造出一个衙门里没有的官员（连着人名录一起脏）。"""
    from .officials import SURNAMES, GIVEN_NAMES
    from . import depts, depts_neiting, depts_waizhusi, positions as _pos
    d_dict = next((d for d in (depts.DEPTS, depts_neiting.DEPTS, depts_waizhusi.DEPTS)
                   if dept_name in d), None)
    if d_dict is None:
        return False, f"没有【{dept_name}】这个衙署，增设职位无从谈起。"
    name = officer_name or (random.choice(SURNAMES) + random.choice(GIVEN_NAMES))
    full_title = office_title if dept_name in office_title else dept_name + office_title
    if _pos.exists(full_title, dept_name):
        return False, f"【{dept_name}】的管理层里已经有【{full_title}】这个职位。"
    _pos.add(title=full_title, person=name, kind="京官", category=dept_name, start="1627-08")
    return True, f"【{dept_name}】现任管理层：已增设职位【{full_title}】，推举【{name}】充任履职。"


def delete_dept_staff(state, dept_name: str, office_title: str) -> Tuple[bool, str]:
    """在六部、二十四衙门或外廷诸司管理层裁撤职位。

    部门不存在 → 报错；撤人只在「官职名里带着本部门名」的那位身上动手，
    免得撤惜薪司的掌印太监却撤到司礼监那位。"""
    from . import depts, depts_neiting, depts_waizhusi, positions as _pos
    d_dict = next((d for d in (depts.DEPTS, depts_neiting.DEPTS, depts_waizhusi.DEPTS)
                   if dept_name in d), None)
    if d_dict is None:
        return False, f"没有【{dept_name}】这个衙署。"

    full_title = office_title if dept_name in office_title else dept_name + office_title
    if not _pos.exists(full_title, dept_name):
        return False, f"【{dept_name}】的管理层里没有【{office_title}】这个职位。"
    holder = _pos.occupant(full_title, dept_name)
    _pos.remove(full_title, dept_name)
    if holder:
        return True, f"【{dept_name}】现任管理层：已正式裁撤职位【{office_title}】。"
    return True, f"【{dept_name}】现任管理层：已撤编职位【{office_title}】 该职现无在任者。"


def add_civil_office(state, province: str, office_title: str, official_name: Optional[str] = None) -> Tuple[bool, str]:
    """7. 在文册档案内增加一个职位 由逻辑引擎接入随机人物。"""
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    from .officials import SURNAMES, GIVEN_NAMES
    from . import positions as _pos
    full = f"{pname}{office_title}"
    name = official_name or (random.choice(SURNAMES) + random.choice(GIVEN_NAMES))
    if _pos.exists(full, pname):
        _pos.assign(full, name, "1627-08", category=pname)
    else:
        _pos.add(title=full, person=name, kind="文职", category=pname, start="1627-08")
    return True, f"吏部文册档案：已在{pname}增设职位【{office_title}】，特授【{name}】到任履职。"


def delete_civil_office(state, province: str, office_title: str) -> Tuple[bool, str]:
    """8. 在文册档案内删除一个职位。"""
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    from . import positions as _pos
    for o in _pos.incumbents(kind="文职", category=pname):
        if office_title in o.get("title", ""):
            _pos.remove(o["title"], pname)
            return True, f"吏部文册档案：已正式裁撤{pname}职位【{o.get('title')}】。"
    return False, f"未在{pname}找到待裁撤的文职【{office_title}】。"




def parse_department_modifier_content(state, content_str: str):
    """解析部门修正的多词条组合内容，返回 (effects, errors)。

    口径 玩家定义：**词条表是唯一真相源**（engine/modifier_terms.MODIFIER_TERMS）。
    写法不在表里的一律判**词条撰写错误**，如实进 errors，绝不静默丢弃。
    支持逗号、顿号、分号或空格分隔多个词条。
    """
    from .modifier_terms import find_literal, find_scoped, classes_of

    text = (content_str or "").strip()
    errors: List[str] = []
    effects: List[Dict[str, Any]] = []
    if not text:
        return effects, ["修正内容为空"]

    classes = ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]

    # 指定地区可写的省名：只认当前属明朝控制的省份
    prov_names: List[Tuple[str, str]] = []
    if state is not None and hasattr(state, "provinces"):
        from .localdata import under_ming_control
        for pid, p in (state.provinces or {}).items():
            if under_ming_control(p):
                prov_names.append((pid, p.get("name", pid)))

    items = re.split(r"[,，;；\n]+|\s+(?=[^\s]{2,}\s*[+\-增减降升]\s*\d)", text)
    for item in items:
        tok = item.strip()
        if not tok:
            continue

        # 0) 流量词条：白银 / 黄金 只收 `N万两/年`（凭空生成，按月折算入账）
        m_flow = re.match(r"^(.+?)\s*(\d+(?:\.\d+)?)\s*万两\s*/\s*年$", tok)
        if m_flow:
            lit_flow = find_literal(m_flow.group(1))
            if lit_flow and lit_flow[0]["target"] in ("silver_flow", "gold_flow"):
                annual = float(m_flow.group(2))
                monthly = round(annual / 12.0, 4)
                effects.append({
                    "target": lit_flow[0]["target"], "annual_wan": annual,
                    "monthly_wan": monthly, "term": lit_flow[0]["term"],
                    "desc": "%s %g万两/年（每月 %g万两）"
                            % (lit_flow[0]["term"], annual, monthly),
                })
                continue
            if lit_flow:
                errors.append("「%s」不是流量词条：只有白银、黄金收 N万两/年 这种写法" % tok)
                continue
            errors.append("「%s」不是合法词条：表里没有这个字段名" % tok)
            continue

        m_val = re.search(r"([+\-增减降升])\s*(\d+(?:\.\d+)?)\s*(%|百分点|点)?", tok)
        if not m_val:
            errors.append("「%s」缺变化幅度：词条要写成「词条名+数值」如 中央腐败程度-10%%" % tok)
            continue
        op_sign = "-" if m_val.group(1) in ("-", "减", "降") else "+"
        val_num = float(m_val.group(2))
        is_pct = (m_val.group(3) == "%")
        delta = -val_num if op_sign == "-" else val_num

        # 1) 先认带前缀的写法：指定地区 / 指定阶级
        hit = find_scoped(tok, "province", prov_names) if prov_names else None
        hit_kind, prov_id, prov_name = None, None, None
        if hit:
            hit_kind, prov_id = "province", hit[2]
            prov_name = dict(prov_names).get(prov_id, prov_id)
        if hit is None:
            cls_hit = find_scoped(tok, "class", [(c, c) for c in classes])
            if cls_hit:
                hit, hit_kind = cls_hit, "class"
        if hit is None:
            lit = find_literal(tok)
            if lit:
                hit, hit_kind = lit, "literal"

        if hit is None:
            errors.append("「%s」不是合法词条：表里没有这个字段名" % tok)
            continue

        entry = hit[0]
        target = entry["target"]

        if target in ("silver_flow", "gold_flow"):
            errors.append("「%s」只收流量写法：写成「%s6万两/年」，不收「%s+100」"
                          % (tok, entry["term"], entry["term"]))
            continue

        # 2) 按词条打到对应系统 描述文案沿用原来的口径
        if target == "financial_rate":
            f_delta = delta / 100.0 if (is_pct or abs(delta) >= 1.0) else delta
            effects.append({"target": "financial_rate", "delta": f_delta,
                            "desc": "替代率f%+.1f%%" % (f_delta * 100)})
        elif target == "capacity":
            matched = classes_of(tok) or list(classes)
            effects.append({
                "target": "capacity",
                "classes": matched,
                "delta_pct": delta / 100.0 if (is_pct or abs(delta) >= 1.0) else delta,
                "desc": "%s产能%+.2f%%" % ("全阶级" if len(matched) == len(classes) else "、".join(matched),
                                          delta),
            })
        elif target == "attitude":
            matched = classes_of(tok) or list(classes)
            effects.append({
                "target": "attitude",
                "classes": matched,
                "delta": delta if not is_pct else 0.0,
                "delta_pct": delta / 100.0 if is_pct else None,
                "is_pct": is_pct,
                "desc": "%s态度%+.1f%s" % ("全阶级" if len(matched) == len(classes) else "、".join(matched),
                                          delta, "%" if is_pct else "点"),
            })
        elif target in ("prov_corruption", "local_corruption"):
            if hit_kind != "province":
                errors.append("「%s」要带省名：写成「陕西%s+6%%」这样" % (tok, entry["term"]))
                continue
            effects.append({
                "target": "prov_corruption", "province_id": prov_id, "province_name": prov_name,
                "delta": delta, "is_pct": is_pct,
                "desc": "%s腐败%+.1f%s" % (prov_name, delta, "%" if is_pct else "点"),
            })
        elif target == "all_local_corruption":
            effects.append({"target": "all_local_corruption", "delta": delta, "is_pct": is_pct,
                            "desc": "全国地方腐败%+.1f%s" % (delta, "%" if is_pct else "点")})
        elif target == "prestige":
            effects.append({"target": "prestige", "delta": delta, "is_pct": is_pct,
                            "desc": "皇室威望%+.1f%s" % (delta, "%" if is_pct else "点")})
        elif target == "central_corruption":
            effects.append({"target": "central_corruption", "delta": delta, "is_pct": is_pct,
                            "desc": "中央腐败%+.1f%s" % (delta, "%" if is_pct else "点")})
        else:
            # 其余全国类词条：edu_growth / higher_edu_growth / population_birth /
            # population_death / carrying_pct / demand
            effects.append({"target": target, "delta": delta, "is_pct": is_pct,
                            "desc": "%s%+.1f%s" % (entry["term"], delta, "%" if is_pct else entry["unit"])})

    return effects, errors



# ═══════════════════════════════════════════════════════════
# 修正：统一模型（口径 玩家定义）
#   修正只有一个物种：{名称, 效果列表, 介绍, 显示位置}。
#   法律、行政纪律、独立地区修正、部门修正都只是**显示位置不同**，不是不同种类。
#   刷新次序：先有修正，再决定它显示在哪（显示位置是参数）。
#   物理存放：位置是省 → 该省 local.custom_modifiers；其余位置 → state.department_modifiers。
#   部门列表两条（吏部行政纪律 / 刑部现行法律）是旧存档与种子数据，
#   统一指令找得到就改它们，找不到才新建。
# ═══════════════════════════════════════════════════════════

def _location_province(state, location):
    """显示位置若是一个省，返回 (pid, 省名)；否则 (None, None)。

    只认真正的省 id 或省名，不做模糊兜底（部门名如「刑部」必须落到部门修正那条路）。
    """
    if not location:
        return None, None
    text = str(location).strip()
    provinces = state.provinces or {}
    if text in provinces:
        return text, PROV_REV.get(text, text)
    for pid, p in provinces.items():
        if p.get("name") == text:
            return pid, text
    return None, None


def _is_scope_token(state, tok: str) -> bool:
    """修正范围的合法写法：中央／全国／本省／全省，或一个省名。"""
    if tok in ("中央", "全国", "本省", "全省"):
        return True
    return _get_prov_id(tok) is not None


def _split_effects_intro(rest: str) -> Tuple[str, str]:
    """把「修正效果… 介绍」拆开：含数值与正负号的是词条，其余从第一个起都算介绍。"""
    eff, intro = [], []
    for tok in re.split(r"\s+", (rest or "").strip()):
        if intro or not re.search(r"[+\-]?\d", tok):
            intro.append(tok)
        else:
            eff.append(tok)
    return "，".join(eff), " ".join(intro).strip()


def create_modifier(state, location: str, name: str, content_str: str,
                    scope: str = "", intro: str = "",
                    duration_months=None) -> Tuple[bool, str]:
    """创建修正 <显示位置> <修正项目> <修正范围> <修正效果> <介绍>。

    显示位置＝界面名（吏部／户部／礼部／兵部／刑部／工部／内阁／司礼监／都察院／通政司，或某个省名）；
    「中央」是修正范围的取值，不是界面，写了要报错。修正范围＝中央／全国／某省。
    """
    if _norm_mod_name(location) in ("中央", "朝廷"):
        return False, ("「中央」是修正范围的取值，不是界面：显示位置请写 吏部／户部／礼部／兵部／刑部／工部"
                       "／内阁／司礼监／都察院／通政司，或某个省名。")
    effects, errors = parse_department_modifier_content(state, content_str)
    if errors:
        return False, "词条撰写错误：" + "；".join(errors) + "。可用词条见 设置-修正词条。"
    if not effects:
        return False, "未识别到有效的修正词条 内容：%s。" % content_str
    if not location or not name:
        return False, "创建修正要写全：创建修正 <显示位置> <名称> <效果>。"
    # 一个名字只能有一条修正：同一个名字在别的显示位置已经有过，就当场报错，
    # 不许偷偷在第二处再建一份（那就成了两份真相）。
    kind, holder, exist = find_modifier(state, name)
    pid_new, _pn = _location_province(state, location)
    if kind is not None and _norm_mod_name(exist.get("name") if isinstance(exist, dict) else "") == _norm_mod_name(name):
        where = (exist.get("location") or exist.get("dept") or "") if isinstance(exist, dict) else ""
        same_place = (kind == "dept" and exist.get("dept") == location) or \
                     (kind == "province" and pid_new and holder[0] == pid_new)
        if not same_place:
            return False, ("已存在同名修正【%s】，显示在%s；请先删除或改名，"
                           "一个名字只留一条修正。" % (name, where or "部门列表"))
    summary = "，".join(e["desc"] for e in effects)
    pid_scope, _pn_scope = _location_province(state, location)
    # 修正范围：写明了就用写明的，没写按显示位置推（省界面＝该省，其余＝中央）
    scope_text = scope or ("该省" if pid_scope else "中央")
    row = {"name": name, "location": location, "dept": location,
           "scope": scope_text, "intro": intro or "",
           "content_str": content_str, "effects": effects, "summary": summary}
    pid, pname = _location_province(state, location)
    if pid:
        loc = state.provinces[pid].setdefault("local", {})
        cmods = [m for m in (loc.get("custom_modifiers") or []) if m.get("name") != name]
        cap = next((e for e in effects if e.get("target") == "capacity"), None)
        row.update({
            # 旧读法（地区修正表按产能占比读 effect_pct）继续可用
            "effect_pct": float(cap.get("delta_pct") or 0.0) if cap else 0.0,
            "effect_pct_display": ("%+.1f%%" % (float(cap.get("delta_pct") or 0.0) * 100)) if cap else "-",
            "affected_classes": list(cap.get("classes") or []) if cap else [],
            "duration_months": duration_months,   # None＝永久，tick 不按到期清除
            "note": "超控设立的地区修正",
        })
        cmods.append(row)
        loc["custom_modifiers"] = cmods
        return True, "已在【%s】设立修正【%s】%s。" % (pname, name, summary)
    state.department_modifiers[name] = row
    from .tech_effects import recompute_financial_rate
    recompute_financial_rate(state)      # 替代率词条要真生效，不是只显示
    return True, "已在【%s】设立修正【%s】%s。" % (location, name, summary)


def _norm_mod_name(name: str) -> str:
    """修正名归一：去掉书名号、括号与空白——法律条目叫《问刑条例》，指令里写 问刑条例。"""
    s = str(name or "")
    for ch in "《》〈〉「」『』【】（）()〔〕[] 　":
        s = s.replace(ch, "")
    return s


def find_modifier(state, name: str):
    """按名称找修正，返回 (kind, holder, row)；找不到 (None, None, None)。

    查找次序固定：部门修正 → 各省地区修正 → 吏部行政纪律 → 刑部现行法律。
    名字先按原样比，再按归一比（书名号/括号/空白不算差别）。
    """
    want = _norm_mod_name(name)
    if name in (getattr(state, "department_modifiers", {}) or {}):
        return "dept", state.department_modifiers, state.department_modifiers[name]
    for key, row in (getattr(state, "department_modifiers", {}) or {}).items():
        if _norm_mod_name(key) == want:
            return "dept", state.department_modifiers, row
    for pid, p in (state.provinces or {}).items():
        for m in ((p.get("local") or {}).get("custom_modifiers") or []):
            if _norm_mod_name(m.get("name")) == want:
                return "province", (pid, p), m
    from . import depts_libu, depts_xingbu
    for b in depts_libu.ADMIN_DISCIPLINE_BUFFS:
        if _norm_mod_name(b.get("name")) == want:
            return "list_libu", depts_libu, b
    for l in depts_xingbu.ACTIVE_LAWS:
        if _norm_mod_name(l.get("name")) == want:
            return "list_xingbu", depts_xingbu, l
    return None, None, None


# 部门列表条目能写的字段（法律与行政纪律的存储字段就这几个）
_LIST_ROW_TARGETS = {
    "central_corruption": "central_corruption_pct",
    "all_local_corruption": "local_corruption_pct",
    "prov_corruption": "local_corruption_pct",
    "carrying_pct": "carrying_pct",
}


def _write_list_row(row, effects) -> str:
    """把统一词条效果写进部门列表条目的字段；写到它没有的字段就如实报错。"""
    for e in effects:
        field = _LIST_ROW_TARGETS.get(e["target"])
        if not field:
            return ("【%s】显示在部门列表里，只支持 中央腐败程度／全国所有地方腐败程度／全国承载力 "
                    "三个词条" % row.get("name"))
        row[field] = round(float(e.get("delta") or 0.0) / 100.0, 4)
    if "central_corruption_pct" in row:
        row["central_desc"] = "%+.0f%%" % (float(row["central_corruption_pct"]) * 100)
    if "local_corruption_pct" in row:
        row["local_desc"] = "%+.0f%%" % (float(row["local_corruption_pct"]) * 100)
    if "carrying_pct" in row:
        row["carrying_desc"] = "%+.0f%%" % (float(row["carrying_pct"]) * 100)
    if "effect_desc" in row:
        row["effect_desc"] = "地方腐败 %+.0f%% 中央腐败 %+.0f%%" % (
            float(row.get("local_corruption_pct") or 0.0) * 100,
            float(row.get("central_corruption_pct") or 0.0) * 100)
    return ""


def modify_modifier(state, name: str, content_str: str) -> Tuple[bool, str]:
    """修改修正 <名称> <效果…>：不改变它显示在哪，只改效果。"""
    effects, errors = parse_department_modifier_content(state, content_str)
    if errors:
        return False, "词条撰写错误：" + "；".join(errors) + "。可用词条见 设置-修正词条。"
    if not effects:
        return False, "未识别到有效的修正词条 内容：%s。" % content_str
    kind, holder, row = find_modifier(state, name)
    if kind is None:
        return False, "未找到修正【%s】。" % name
    if kind in ("list_libu", "list_xingbu"):
        bad = _write_list_row(row, effects)
        if bad:
            return False, bad
        return True, "已更新修正【%s】（显示在%s）。" % (
            name, "吏部行政纪律" if kind == "list_libu" else "刑部现行法律")
    row["effects"] = effects
    row["content_str"] = content_str
    row["summary"] = "，".join(e["desc"] for e in effects)
    if kind == "province":
        cap = next((e for e in effects if e.get("target") == "capacity"), None)
        row["effect_pct"] = float(cap.get("delta_pct") or 0.0) if cap else 0.0
        row["affected_classes"] = list(cap.get("classes") or []) if cap else []
    from .tech_effects import recompute_financial_rate
    recompute_financial_rate(state)
    return True, "已更新修正【%s】（显示在%s）。" % (
        name, row.get("location") or row.get("dept") or "")


def delete_modifier(state, name: str) -> Tuple[bool, str]:
    """删除修正 <名称>：几处存储里按名字找，找到即删。"""
    kind, holder, row = find_modifier(state, name)
    if kind is None:
        return False, "未找到修正【%s】。" % name
    if kind == "dept":
        for key in [k for k in state.department_modifiers
                    if _norm_mod_name(k) == _norm_mod_name(name)]:
            del state.department_modifiers[key]
        from .tech_effects import recompute_financial_rate
        recompute_financial_rate(state)      # 替代率词条要真生效，删改后同样要重算
        return True, "已删除修正【%s】（原显示在%s）。" % (
            name, row.get("location") or row.get("dept") or "")
    if kind == "province":
        pid, p = holder
        loc = p.setdefault("local", {})
        loc["custom_modifiers"] = [m for m in (loc.get("custom_modifiers") or [])
                                   if m.get("name") != name]
        return True, "已删除修正【%s】（原显示在%s）。" % (name, p.get("name", pid))
    from . import depts_libu, depts_xingbu
    if kind == "list_libu":
        depts_libu.ADMIN_DISCIPLINE_BUFFS = [b for b in depts_libu.ADMIN_DISCIPLINE_BUFFS
                                             if b.get("name") != name]
        return True, "已删除修正【%s】（原显示在吏部行政纪律）。" % name
    depts_xingbu.ACTIVE_LAWS = [l for l in depts_xingbu.ACTIVE_LAWS if l.get("name") != name]
    return True, "已删除修正【%s】（原显示在刑部现行法律）。" % name


def flow_modifier_totals(state) -> Dict[str, float]:
    """所有修正里的白银/黄金生成额（万两/年）。"""
    silver = gold = 0.0
    rows = list((getattr(state, "department_modifiers", {}) or {}).values())
    for p in (state.provinces or {}).values():
        rows.extend(((p.get("local") or {}).get("custom_modifiers") or []))
    for r in rows:
        for e in (r.get("effects") or []):
            if e.get("target") == "silver_flow":
                silver += float(e.get("annual_wan") or 0.0)
            elif e.get("target") == "gold_flow":
                gold += float(e.get("annual_wan") or 0.0)
    return {"silver_annual": round(silver, 4), "gold_annual": round(gold, 4)}


def apply_flow_modifiers_monthly(state) -> List[str]:
    """每月把白银/黄金词条**凭空生成**的银钱入账：白银进国库，黄金进私库黄金。"""
    tot = flow_modifier_totals(state)
    notes = []
    s_m = round(tot["silver_annual"] / 12.0, 4)
    g_m = round(tot["gold_annual"] / 12.0, 4)
    if s_m:
        state.treasury = round(float(state.treasury) + s_m, 2)
        notes.append("凭空生成白银%+.4g万两/月 国库:%.2f万两" % (s_m, state.treasury))
    if g_m:
        state.gold_treasury = round(float(state.gold_treasury) + g_m, 2)
        notes.append("凭空生成黄金%+.4g万两/月 私库黄金:%.2f万两" % (g_m, state.gold_treasury))
    return notes








# ═══════════════════════════════════════════════════════════
# 四、 户部超控接口 (Revenue Overrides)
# ═══════════════════════════════════════════════════════════

def create_tax_item(state, tax_id: str, name: str, category: str, rate: float,
                    schedule_desc: str = "公历每月1日", target_type: str = "capacity",
                    target_classes: Optional[List[str]] = None, desc: str = "") -> Tuple[bool, str]:
    """10. 增加户部赋税体系的任何一个赋税条目。

    分类只认体系里既有的七类，税目修正必须为正——以前随便写个分类、写个负数都收。"""
    from . import depts_hubu
    TAX_CATEGORIES = ["田赋", "盐税", "关税", "人头税", "杂税", "加派", "捐纳"]
    if category not in TAX_CATEGORIES:
        return False, "税目分类只认 %s，收到【%s】。" % ("／".join(TAX_CATEGORIES), category)
    try:
        rate_val = float(rate)
    except (TypeError, ValueError):
        return False, "税目修正必须是数字，收到【%s】。" % rate
    if rate_val <= 0:
        return False, "税目修正必须大于 0，收到 %s。" % rate_val
    taxes = depts_hubu.TAX_DEFINITIONS
    taxes = [t for t in taxes if t.get("id") != tax_id and t.get("name") != name]
    taxes.append({
        "id": tax_id or f"tax_{len(taxes)+1}",
        "name": name,
        "category": category or "常赋",
        "desc": desc or f"国家新定税赋 {name}",
        "target_type": target_type,
        "target_classes": target_classes or ["all_physical"],
        "schedule": "monthly_1st",
        "schedule_desc": schedule_desc,
        "rate": float(rate),
        "rate_display": float(rate),
        "calc_desc": f"全国生产规模 × 税目修正 {float(rate)} 按比例分摊",
        # 公式里只有「税目修正」这一个量，没有「税率」；rate = 税目修正 ÷ 10
        "calc_formula": "月征额 = 课税基数 × 税目修正 ÷ 10",
        # 与 TAX_DEFINITIONS 里原有税目同一个口径：ⓘ 的「机制」那一批要有话可说
        "mechanism": "超控新设税目：月征额 = 课税基数 × 税目修正 ÷ 10，税目修正与课税基数随朝廷诏令改动。",
    })
    depts_hubu.TAX_DEFINITIONS = taxes
    return True, f"户部赋税体系：已增设税目【{name}】 {category}，税目修正 {rate}，征期 {schedule_desc}。"


def delete_tax_item(state, tax_id_or_name: str) -> Tuple[bool, str]:
    """11. 删除户部赋税体系的任何一个赋税条目。"""
    from . import depts_hubu
    taxes = depts_hubu.TAX_DEFINITIONS
    new_taxes = [t for t in taxes if t.get("id") != tax_id_or_name and t.get("name") != tax_id_or_name]
    if len(new_taxes) < len(taxes):
        depts_hubu.TAX_DEFINITIONS = new_taxes
        return True, f"户部赋税体系：已蠲除废除税目【{tax_id_or_name}】。"
    return False, f"未在户部税则中找到税目【{tax_id_or_name}】。"


# ═══════════════════════════════════════════════════════════
# 五、 礼部超控接口 (Rites Overrides)
# ═══════════════════════════════════════════════════════════

def set_diplomatic_relation(state, country_name_or_id: str, relation: str = "藩属",
                            trade_status: str = "贸易") -> Tuple[bool, str]:
    """12. 修改外交事务的关系状态、贸易状态。"""
    from . import depts_rites
    countries = depts_rites.DIPLOMATIC_COUNTRIES
    for c in countries:
        if c.get("id") == country_name_or_id or c.get("name") == country_name_or_id or country_name_or_id in c.get("name", ""):
            c["relation"] = relation
            c["trade_status"] = trade_status
            return True, f"礼部外交事务：已调整对【{c.get('name')}】关系为【{relation}】，贸易状态为【{trade_status}】。"
    return False, f"未找到外交属国或势力【{country_name_or_id}】。"




def modify_population_rate_modifier(state, name: str, birth_delta: float = 0.0,
                                    death_delta: float = 0.0, delete: bool = False) -> Tuple[bool, str]:
    """14. 增加或删除一条人口自然变动修正 对每月出生/死亡比例的加减；比例见 economy.population_rates。"""
    mods = state.pop_rate_modifiers      # 开局就有这个字段，缺了就是错，不兜底

    if delete:
        state.pop_rate_modifiers = [m for m in mods if m.get("name") != name]
        return True, f"人口自然变动：已撤销修正条目【{name}】。"

    new_mods = [m for m in mods if m.get("name") != name]
    new_mods.append({
        "name": name,
        "birth_delta": float(birth_delta),
        "death_delta": float(death_delta),
        "desc": f"{name} 月出生 {float(birth_delta)*100:+.3f}% 月死亡 {float(death_delta)*100:+.3f}%",
    })
    state.pop_rate_modifiers = new_mods
    return True, (f"人口自然变动：已设定修正【{name}】 月出生 {float(birth_delta)*100:+.3f}% "
                  f"月死亡 {float(death_delta)*100:+.3f}%。")


# ═══════════════════════════════════════════════════════════
# 六、 兵部超控接口 (War Overrides)
# ═══════════════════════════════════════════════════════════

def modify_pay_category(state, cat_id: str, name: str, monthly_target: float,
                        annual_target: float = 0.0, delete: bool = False) -> Tuple[bool, str]:
    """14. 调整辽饷征收额。

    口径（玩家定义）：辽饷就是税目 liaoxiang，界面在兵部。
    所以这里只做一件事：把「月征额」换算成该税目的「税目修正」，
    走 modify_tax_rate 同一条路、同一个字段（rate / rate_display），
    不再自己写一份 LIAOXIANG_CONFIG，也不再自己往 state 上写一份月额。
    """
    from . import depts_hubu
    if delete:
        return False, "辽饷是朝廷加派的名目，只能改不能废；要停征请用「修改税目修正 liaoxiang 0.0001」压到最低。"
    row = depts_hubu.liaoxiang_tax_row(state)
    if row is None:
        return False, "税目表里找不到辽饷税目 liaoxiang，无法调整。"
    base = float(row.get("base_val") or 0.0)
    if base <= 0:
        return False, "辽饷税基为零（全国物理总产能为 0），无法反算税目修正。"
    # 税目修正 rate_display = 月额 ÷ 税基 × 10（rate = 税基 × rate 得到月额，见 calc_hubu_taxes）
    rate_display = round(float(monthly_target) / base * 10.0, 10)
    ok, msg = depts_hubu.modify_tax_rate(state, depts_hubu.LIAOXIANG_TAX_ID, rate_display)
    if not ok:
        return ok, msg
    return True, ("兵部粮饷筹措：辽饷加派已调整为 %.2f 万两/月（%s）。"
                  "辽饷是税目 liaoxiang，税额随全国物理总产能浮动，此处改的是它的税目修正 %g。"
                  % (float(monthly_target), msg, rate_display))


def add_military_office(state, region_or_province: str, office_title: str, officer_name: Optional[str] = None) -> Tuple[bool, str]:
    """15. 在武官人事内增加一个职位（kind=武职，category=省/战区）。"""
    from .officials import SURNAMES, GIVEN_NAMES
    from . import positions as _pos
    name = officer_name or (random.choice(SURNAMES) + random.choice(GIVEN_NAMES))
    if _pos.exists(office_title, region_or_province):
        _pos.assign(office_title, name, "1627-08", category=region_or_province)
    else:
        _pos.add(title=office_title, person=name, kind="武职",
                 category=region_or_province, start="1627-08")
    return True, f"兵部武官人事：已在【{region_or_province}】增设武职【{office_title}】，推补【{name}】领军视事。"


def delete_military_office(state, region_or_province: str, office_title: str) -> Tuple[bool, str]:
    """16. 删除武官人事的一个职位。"""
    from . import positions as _pos
    for o in _pos.incumbents(kind="武职", category=region_or_province or None):
        if office_title in o.get("title", ""):
            _pos.remove(o["title"], o["category"])
            return True, f"兵部武官人事：已裁撤武职【{o.get('title')}】。"
    return False, f"未找到待裁撤的武官职位【{office_title}】。"


def replace_military_officer(state, region_or_province: str, office_title: str, old_name: str, new_name: str) -> Tuple[bool, str]:
    """17. 更换一个武官人事内的官员。"""
    from . import positions as _pos
    for o in _pos.incumbents(kind="武职", category=region_or_province or None):
        if (office_title in o.get("title", "") or not office_title) and \
           (o.get("person") == old_name or not old_name):
            _pos.update(o["id"], person=new_name, status="active")
            return True, f"兵部武官人事：已将【{o.get('title')}】由 {old_name} 调任换为 {new_name}。"
    return False, f"未在武职名册中找到官员 {old_name} {office_title}。"


# ═══════════════════════════════════════════════════════════
# 七、 刑部超控接口 (Justice Overrides)
# ═══════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════
# 八、 工部超控接口 (Works Overrides)
# ═══════════════════════════════════════════════════════════

def modify_armament(state, arm_id: str, name: str, category: str = "ranged",
                    unit_cost: float = 0.0, monthly_capacity: int = 0,
                    unit: str = "杆", desc: str = "", delete: bool = False) -> Tuple[bool, str]:
    """19. 在器物制造中修改，增加，或删除一个条目的内容。"""
    from . import depts_works
    cat_map = depts_works.ARMAMENT_CATALOG
    if delete:
        if arm_id in cat_map:
            cat_map.pop(arm_id, None)
            return True, f"工部器物制造：已停止制造并剔除军器规格【{name or arm_id}】。"
        return False, f"未找到军器条目【{arm_id}】。"

    cat_map[arm_id] = {
        "id": arm_id,
        "category": category,
        "name": name,
        "unit_cost": float(unit_cost),
        "monthly_capacity": int(monthly_capacity),
        "unit": unit,
        "monthly_cost": round(float(unit_cost) * int(monthly_capacity) / 10000.0, 2),
        "foreign": False,
        "desc": desc or f"工部制式军器 {name}"
    }
    return True, f"工部器物制造：已更新/录入军器制造条目【{name}】 单价 {unit_cost}两，月产 {monthly_capacity}{unit}。"


def parse_project_buff(state, buff_text: str):
    """工程的「修正」解析：**与修正走同一张词条表、同一个解析器**，没有第二套。

    返回 (buff, errors)。词条表里没有的写法一律返回错误，由调用方如实拒绝。
    """
    text = (buff_text or "").strip()
    if not text:
        return {}, []
    effects, errors = parse_department_modifier_content(state, text)
    if errors:
        return {}, errors
    buff: Dict[str, Any] = {}
    pct: Dict[str, float] = {}
    flat: Dict[str, float] = {}
    for eff in effects:
        target = eff.get("target")
        if target == "silver_flow":
            buff["silver"] = round(buff.get("silver", 0.0) + float(eff.get("annual_wan") or 0.0), 4)
            continue
        if target == "gold_flow":
            buff["gold"] = round(buff.get("gold", 0.0) + float(eff.get("annual_wan") or 0.0), 4)
            continue
        if target != "capacity":
            continue
        cls_list = eff.get("classes") or []
        if eff.get("delta_pct") is not None:
            for c in cls_list:
                pct[c] = round(pct.get(c, 0.0) + float(eff.get("delta_pct") or 0.0), 4)
        else:
            for c in cls_list:
                flat[c] = round(flat.get(c, 0.0) + float(eff.get("delta") or 0.0), 2)
    if pct:
        buff["pct"] = pct
    if flat:
        buff["flat"] = flat
    return buff, []


def create_project(state, province: str, name: str, kind: str = "工程建筑", buff_text: str = "",
                   desc: str = "", cost: float = 0.0, build_months: int = 0,
                   scope: str = "本省") -> Tuple[bool, str]:
    """20. 在工程建设中创建一个地方工程或矿场。

    六个属性：名称 / 修正(buff) / 范围 / 建筑类型 / 状态 / 造价。
    建筑类型是五类之一：国家建筑 / 军事建筑 / 工程建筑 / 灾害建筑 / 矿场。
    工期只是新建时的临时属性：月度花费 ＝ 造价 ÷ 工期 不另存；年维护费 ＝ 造价 × 1%。
    """
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    if pid not in state.provinces:
        return False, f"未找到省份 {province}"

    loc = state.provinces[pid].setdefault("local", {})
    projs = loc.setdefault("projects", [])

    # 检查是否已存在
    for p in projs:
        if p.get("name") == name:
            return False, f"{pname}已存在同名工程【{name}】。"

    cost = float(cost or 0.0)
    build_months = int(build_months or 0)
    status = "运营中" if build_months <= 0 else "未完工"

    buff, buff_errors = parse_project_buff(state, buff_text)
    if buff_errors:
        return False, "词条撰写错误：" + "；".join(buff_errors) + "。可用词条见 设置-修正词条。"
    if desc:
        buff["desc"] = desc

    proj_entry = {
        "name": name,
        "buff": buff,
        "scope": str (scope or "本省"),
        "kind": kind,
        "status": status,
        "cost": round(cost, 2),
    }
    projs.append(proj_entry)

    # 需要工期的新建工程：录入建造清单按月扣费，月款 = 造价 ÷ 工期
    if build_months > 0 and cost > 0:
        under_const = getattr(state, "under_construction_projects", None)
        if under_const is None:
            state.under_construction_projects = []
            under_const = state.under_construction_projects
        under_const.append({
            "province_id": pid,
            "name": name,
            "remain_months": build_months,
            "total_months": build_months,
            "cost": round(cost, 2),
        })
        return True, (f"工部工程营建：已在{pname}立项开工【{name}】 {kind}，造价 {cost:.2f} 万两，"
                      f"工期 {build_months} 个月，月支库银 {cost / build_months:.2f} 万两入地方窖藏。")

    return True, f"工部工程营建：已在{pname}直接落成投运工程【{name}】 造价 {cost:.2f} 万两。"


def set_project_damage(state, province: str, name: str, percent) -> Tuple[bool, str]:
    """工程损毁程度：把某处工程的损毁写成百分比（0~100），生效按 (1−损毁) 折算。"""
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    if pid not in state.provinces:
        return False, f"未找到省份 {province}"
    try:
        pct = float(str(percent).replace("%", ""))
    except (TypeError, ValueError):
        return False, f"损毁程度要写数字（0~100），收到【{percent}】。"
    if not 0 <= pct <= 100:
        return False, f"损毁程度只能在 0~100 之间，收到【{pct}】。"
    loc = state.provinces[pid].setdefault("local", {})
    for pj in loc.get("projects") or []:
        if pj.get("name") == name:
            from .projects import set_damage
            v = set_damage(pj, pct / 100.0)
            return True, f"工部营建：{pname}工程【{name}】损毁程度记为 {round(v * 100)}%。"
    return False, f"未在{pname}找到工程【{name}】。"


def delete_project(state, province: str, name: str) -> Tuple[bool, str]:
    """21. 在工程建设/水利管理中彻底删除一个地方工程或矿场 同步到地方。"""
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    if pid not in state.provinces:
        return False, f"未找到省份 {province}"

    loc = state.provinces[pid].setdefault("local", {})
    projs = loc.get("projects") or []
    new_projs = [p for p in projs if p.get("name") != name]
    if len(new_projs) < len(projs):
        loc["projects"] = new_projs
        # 同步清除正在建造的项目
        under_const = getattr(state, "under_construction_projects", [])
        state.under_construction_projects = [c for c in under_const if not (c.get("province_id") == pid and c.get("name") == name)]
        return True, f"工部营建：已从{pname}彻底拆除/销号工程【{name}】。"
    return False, f"未在{pname}找到工程【{name}】。"


# ═══════════════════════════════════════════════════════════
# 九、 建造工程月度结算驱动 (Tick Construction)
# ═══════════════════════════════════════════════════════════

def tick_construction_monthly(state) -> List[str]:
    """每月 1 日凌晨结算正在营建的工程花费与工期进展。
    建造花费以月为周期从国库扣除到地方窖藏；工期归零后转为运营中。
    """
    notes = []
    under_const = getattr(state, "under_construction_projects", None)
    if not under_const:
        return notes

    remaining = []
    for item in under_const:
        pid = item["province_id"]
        pname = PROV_REV.get(pid, pid)
        pname_full = state.provinces.get(pid, {}).get("name", pid)
        name = item["name"]
        # 月款 = 造价 ÷ 工期 工期是新建时的临时属性
        months = max(1, int(item.get("total_months") or 1))
        cost = round(float(item.get("cost") or 0.0) / months, 4)

        # 1. 扣除国库，注入地方窖藏
        actual_cost = min(float(state.treasury), cost)
        state.treasury = round(max(0.0, float(state.treasury) - actual_cost), 2)
        state.local_reserve = round(float(state.local_reserve) + actual_cost, 2)
        _record_national_tx(state, "支出", actual_cost, "工部工程款", f"拨付{pname_full}【{name}】营建月款 {actual_cost:.2f}万两入地方窖藏")

        item["remain_months"] -= 1
        if item["remain_months"] <= 0:
            # 竣工：状态转为 运营中
            loc = state.provinces.get(pid, {}).get("local", {})
            for p in loc.get("projects", []):
                if p.get("name") == name:
                    p["status"] = "运营中"
            notes.append (f"【工部大工告竣】{pname_full}重点工程【{name}】已全部竣工，正式交付投产运营！")
            CONSOLE.log("works", f"工程竣工：{name}", f"{pname_full}工程如期告成")
        else:
            remaining.append(item)

    state.under_construction_projects = remaining
    return notes


# ═══════════════════════════════════════════════════════════
# 十、 通用指令解析器与执行分发中心 (Operation Dispatcher)
# ═══════════════════════════════════════════════════════════

def execute_operation_command(state, cmd: str) -> Tuple[bool, str]:
    """执行单条标准超控指令或自然语言命令。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return False, "指令为空"

    # 1. 地方军队删除
    m_del_army = re.search(r"删除军队\s+([^\s]+)\s+([^\s]+)", cmd) or re.search(r"消灭军队\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_del_army:
        return delete_army(state, m_del_army.group(1), m_del_army.group(2))

    # 2. 修正：统一指令（口径 修正只有一个物种，显示位置是参数）
    m_new_mod4 = re.match(r"^创建修正\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$", cmd)
    if m_new_mod4 and _is_scope_token(state, m_new_mod4.group(3)):
        eff_str, intro_str = _split_effects_intro(m_new_mod4.group(4))
        return create_modifier(state, m_new_mod4.group(1), m_new_mod4.group(2), eff_str,
                               scope=m_new_mod4.group(3), intro=intro_str)
    m_new_mod = re.match(r"^创建修正\s+(\S+)\s+(\S+)\s+(.+)$", cmd)
    if m_new_mod:
        return create_modifier(state, m_new_mod.group(1), m_new_mod.group(2), m_new_mod.group(3))
    m_edit_mod = re.match(r"^修改修正\s+(\S+)\s+(.+)$", cmd)
    if m_edit_mod:
        return modify_modifier(state, m_edit_mod.group(1), m_edit_mod.group(2))
    m_kill_mod = re.match(r"^删除修正\s+(\S+)$", cmd)
    if m_kill_mod:
        return delete_modifier(state, m_kill_mod.group(1))

    # 【不再保留兼容写法】此处原有「已收敛的旧写法」的别名分支，已按纪律删除：
    #   同一个量只留一条路，写错就报错。旧写法（创建地区修正／删除地区修正）现在
    #   一律落到「无法识别的指令」。要改修正就走统一指令：
    #   创建修正 <显示位置> <修正项目> <修正范围> <修正效果> <介绍>；
    #   修改修正 <修正项目> <修正效果> <介绍>；删除修正 <修正项目>。

    # 3. 部门增删、部门职位编制与中央官员任免
    # 增加部门：分类必填且只认「外廷／内廷」，写错直接报错（不猜、不兜底）
    m_add_dept = re.search(r"增加部门\s+(\S+)\s+(\S+)", cmd)
    if m_add_dept:
        return create_dept(state, m_add_dept.group(1), m_add_dept.group(2))
    # 删除部门：部门按名字找，只有一个参数；多写一个词就是写错，报错
    m_del_dept = re.search(r"删除部门\s+(.+)$", cmd) or re.search(r"裁撤部门\s+(.+)$", cmd)
    if m_del_dept:
        target_name = m_del_dept.group(1).strip()
        if len(target_name.split()) > 1:
            return False, ("删除部门只有一个参数（部门名称），写成 %r 是错的；"
                           "要裁撤请写「删除部门 惜薪司」" % cmd)
        return delete_dept(state, "", target_name)

    # 3.1 增加部门职位 [部门名] [官职]
    m_add_dpos = re.search(r"增加部门职位\s+([^\s]+)\s+([^\s]+)", cmd) or re.search(r"增加部门管理层\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_add_dpos:
        return add_dept_staff(state, m_add_dpos.group(1), m_add_dpos.group(2))

    # 3.2 删除部门职位 [部门名] [官职]
    m_del_dpos = re.search(r"删除部门职位\s+([^\s]+)\s+([^\s]+)", cmd) or re.search(r"删除部门管理层\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_del_dpos:
        return delete_dept_staff(state, m_del_dpos.group(1), m_del_dpos.group(2))

    # 3.3 更换中央官员 支持 [部门] [官职] [新官] 与 [部门] [原官] [新官]
    m_rep_cnt = re.search(r"更换中央官员\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_rep_cnt:
        return replace_dept_staff_unified(state, m_rep_cnt.group(1), m_rep_cnt.group(2), m_rep_cnt.group(3))

    # 【不再保留兼容写法】此处原有「已收敛的旧写法」的别名分支，已按纪律删除：
    #   同一个量只留一条路，写错就报错。旧写法（更换部门管理层 旧四词格式）现在一律落到
    #   「无法识别的指令」，换人请写：更换中央官员 <部门名> <官职或原官> <新官>。

    # 3.5 删除地方官员 [原官名] 革职撤缺专线
    m_del_loc = re.search(r"删除地方官员\s+([^\s]+)", cmd)
    if m_del_loc:
        return delete_local_officer(state, m_del_loc.group(1))

    # 3.6 人物属性与党派绑定指令  需求1
    m_set_fac = re.search(r"(?:修改党派到人名|修改人物党派|绑定党派)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_set_fac:
        return set_person_faction(m_set_fac.group(1), m_set_fac.group(2))
    m_set_loc = re.search(r"(?:修改人物位置|修改位置)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_set_loc:
        return set_person_location(m_set_loc.group(1), m_set_loc.group(2), state)
    m_set_off = re.search(r"(?:修改人物官职|修改官职)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_set_off:
        return set_person_office(m_set_off.group(1), m_set_off.group(2))
    m_set_sts = re.search(r"(?:修改人物状态|修改状态)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_set_sts:
        return set_person_status(m_set_sts.group(1), m_set_sts.group(2))

    # 【不再保留兼容写法】此处原有「已收敛的旧写法」的别名分支，已按纪律删除：
    #   同一个量只留一条路，写错就报错。旧写法（创建部门修正／删除部门修正／将部门修正为／修改部门修正）现在
    #   一律落到「无法识别的指令」。要改修正就走统一指令：
    #   创建修正 <显示位置> <修正项目> <修正范围> <修正效果> <介绍>；
    #   修改修正 <修正项目> <修正效果> <介绍>；删除修正 <修正项目>。

    # 4. 吏部文册官职
    m_rep_civ = re.search(r"更换文官\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_rep_civ:
        return replace_civil_official(state, m_rep_civ.group(1), m_rep_civ.group(2), m_rep_civ.group(3), m_rep_civ.group(4))
    m_add_civ = re.search(r"增加文职\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_add_civ:
        return add_civil_office(state, m_add_civ.group(1), m_add_civ.group(2))
    m_del_civ = re.search(r"删除文职\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_del_civ:
        return delete_civil_office(state, m_del_civ.group(1), m_del_civ.group(2))

    # 5. 户部税目
    m_add_tax = re.search(r"增加税目\s+([^\s]+)\s+([^\s]+)\s+([+-]?\d*\.?\d+)", cmd)
    if m_add_tax:
        return create_tax_item(state, m_add_tax.group(1), m_add_tax.group(1), m_add_tax.group(2), float(m_add_tax.group(3)))
    m_mod_tax = re.search(r"修改税目修正\s+([^\s]+)\s+([+-]?\d*\.?\d+)", cmd)
    if m_mod_tax:
        from . import depts_hubu
        return depts_hubu.modify_tax_rate(state, m_mod_tax.group(1), float(m_mod_tax.group(2)))
    m_del_tax = re.search(r"删除税目\s+([^\s]+)", cmd)
    if m_del_tax:
        return delete_tax_item(state, m_del_tax.group(1))

    # 6. 礼部外交与教育
    m_dipl = re.search(r"修改外交关系\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_dipl:
        return set_diplomatic_relation(state, m_dipl.group(1), m_dipl.group(2), m_dipl.group(3))

    # 7. 兵部武职与粮饷
    m_pay = re.search(r"修改粮饷加派\s+([+-]?\d*\.?\d+)", cmd)
    if m_pay:
        return modify_pay_category(state, "liaoxiang", "辽饷加派", float(m_pay.group(1)))
    m_add_mil = re.search(r"增加武职\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_add_mil:
        return add_military_office(state, m_add_mil.group(1), m_add_mil.group(2))
    m_del_mil = re.search(r"删除武职\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_del_mil:
        return delete_military_office(state, m_del_mil.group(1), m_del_mil.group(2))
    m_rep_mil = re.search(r"更换武官\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_rep_mil:
        return replace_military_officer(state, m_rep_mil.group(1), m_rep_mil.group(2), m_rep_mil.group(3), m_rep_mil.group(4))

    # 8. 刑部法律
    # 【不再保留兼容写法】此处原有「已收敛的旧写法」的别名分支，已按纪律删除：
    #   同一个量只留一条路，写错就报错。旧写法（修改法律／删除法律）现在
    #   一律落到「无法识别的指令」。要改修正就走统一指令：
    #   创建修正 <显示位置> <修正项目> <修正范围> <修正效果> <介绍>；
    #   修改修正 <修正项目> <修正效果> <介绍>；删除修正 <修正项目>。

    # 9. 工部工程与水利
    # 新建军事建筑：无产能修正，只写介绍
    m_add_mil = re.search(r"新建军事建筑\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s+([+-]?\d*\.?\d+)\s+(\d+)", cmd)
    if m_add_mil:
        return create_project(state, m_add_mil.group(1), m_add_mil.group(2), kind="军事建筑",
                              desc=m_add_mil.group(3), cost=float(m_add_mil.group(4)),
                              build_months=int(m_add_mil.group(5)))
    # 新建国家建筑 / 工程建筑 / 灾害建筑 / 矿场：修正 + 介绍 + 造价 + 工期
    m_add_pj = re.search(r"新建\s*(国家建筑|工程建筑|灾害建筑|矿场)\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)\s+([+-]?\d*\.?\d+)\s+(\d+)", cmd)
    if m_add_pj:
        return create_project(state, m_add_pj.group(2), m_add_pj.group(3), kind=m_add_pj.group(1),
                              buff_text=m_add_pj.group(4), desc=m_add_pj.group(5),
                              cost=float(m_add_pj.group(6)), build_months=int(m_add_pj.group(7)))
    m_del_pj = re.search(r"删除工程\s+([^\s]+)\s+([^\s]+)", cmd)
    if m_del_pj:
        return delete_project(state, m_del_pj.group(1), m_del_pj.group(2))
    m_dmg_pj = re.match(r"^工程损毁程度\s+(\S+)\s+(\S+)\s+([0-9]+(?:\.[0-9]+)?)%?$", cmd)
    if m_dmg_pj:
        return set_project_damage(state, m_dmg_pj.group(1), m_dmg_pj.group(2),
                                  m_dmg_pj.group(3))

    # 10. 国库／地方／朝局这一组数值：一律按表里的语法严格匹配。
    #     以前这七条走的是 execute_single_command 的关键字子串匹配——缺数字当 1、
    #     缺省份落到北直隶、参数写反也照做。现在改成锚定的正则：参数缺一个就落到
    #     「无法识别的指令」，不再猜。
    NUM = r"([+-]?\d+(?:\.\d+)?)$"
    m = re.match(r"^调拨地方窖藏到国库\s+(\S+)\s+" + NUM, cmd)
    if m:
        return _transfer_checked(state, m.group(1), float(m.group(2)), transfer_local_reserve_to_treasury,
                                 "调拨地方窖藏到国库")
    m = re.match(r"^拨付国库到地方\s+(\S+)\s+" + NUM, cmd)
    if m:
        return _transfer_checked(state, m.group(1), float(m.group(2)), transfer_treasury_to_local_reserve,
                                 "拨付国库到地方")
    m = re.match(r"^赈济\s+(\S+)\s+(\S+)\s+" + NUM, cmd)
    if m:
        prov, cls, amt = m.group(1), m.group(2), float(m.group(3))
        pid = _get_prov_id(prov)
        if not pid:
            return False, "赈济：没有【%s】这个省" % prov
        if amt <= 0:
            return False, "赈济：每月额度必须大于 0，收到 %s" % amt
        res = relieve_class(state, prov, cls, amt)
        if res.startswith("【赈济未执行】"):
            return False, res
        return True, res
    m = re.match(r"^地方基建程度\s+(\S+)\s+" + NUM, cmd)
    if m:
        prov, v = m.group(1), float(m.group(2))
        if not _get_prov_id(prov):
            return False, "地方基建程度：没有【%s】这个省" % prov
        return True, change_local_infra(state, PROV_REV.get(_get_prov_id(prov), prov), v)
    m = re.match(r"^地方腐败程度\s+(\S+)\s+" + NUM, cmd)
    if m:
        prov, v = m.group(1), float(m.group(2))
        if not _get_prov_id(prov):
            return False, "地方腐败程度：没有【%s】这个省" % prov
        return True, change_local_corruption(state, PROV_REV.get(_get_prov_id(prov), prov), v)
    m = re.match(r"^中央腐败程度\s+" + NUM, cmd)
    if m:
        return True, change_central_corruption(state, float(m.group(1)))
    m = re.match(r"^皇室威望\s+" + NUM, cmd)
    if m:
        return True, change_prestige(state, float(m.group(1)))

    # 11. 剩下只有「阶级人口／阶级态度」还没进表，按严格写法匹配；其余一律认不出
    res_str = execute_single_command(state, cmd)
    if res_str is None:
        return False, "无法识别的指令：%s" % cmd
    return True, res_str


# ═══════════════════════════════════════════════════════════
# 十一、 原有 20 种基础数值操作兼容保留
# ═══════════════════════════════════════════════════════════

def transfer_local_reserve_to_treasury(state, province: str, amount: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    amount = max(0.0, float(amount))
    avail = state.local_reserve
    actual = min(avail, amount)
    state.local_reserve = max(0.0, round(state.local_reserve - actual, 2))
    state.treasury = round(state.treasury + actual, 2)
    _record_national_tx(state, "收入", actual, "地方解缴", f"从{pname}地方窖藏调银解缴国库 {actual:.2f}万两")
    return f"{pname}窖藏解缴国库+{actual:.2f}万两 国库:{state.treasury:.2f}万两"


def transfer_local_reserve_to_privy(state, province: str, amount: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    amount = max(0.0, float(amount))
    avail = state.local_reserve
    actual = min(avail, amount)
    state.local_reserve = max(0.0, round(state.local_reserve - actual, 2))
    state.privy_treasury = round(state.privy_treasury + actual, 2)
    _record_privy_tx(state, "收入", actual, "地方进奉", f"从{pname}地方窖藏解缴私库 私库 {actual:.2f}万两")
    return f"{pname}进奉私库+{actual:.2f}万两 私库:{state.privy_treasury:.2f}万两"


def transfer_local_gold_to_treasury(state, province: str, amount: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    amount = max(0.0, float(amount))
    avail = state.gold_local_reserve
    actual = min(avail, amount)
    state.gold_local_reserve = max(0.0, round(state.gold_local_reserve - actual, 4))
    state.gold_treasury = round(state.gold_treasury + actual, 4)
    _record_national_tx(state, "收入", actual, "黄金起获", f"从{pname}起获黄金 {actual:.2f}万两 入国库")
    return f"{pname}起获黄金+{actual:.2f}万两 国库黄金:{state.gold_treasury:.2f}万两"


def add_local_infra(state, province: str, points: float) -> str:
    """抬高基建程度。基建不设上限（其它量的 0–100 截断照旧），只挡负数。"""
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    pts = max(0.0, float(points))
    loc["infra"] = round(float(loc.get("infra", 0)) + pts, 1)
    return f"{pname}基建+{pts} 现:{loc['infra']}"


def reduce_local_corruption(state, province: str, pct: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    p = max(0.0, float(pct))
    loc["corruption"] = max(0.0, round(float(loc.get("corruption", 50)) - p, 1))
    return f"{pname}腐败-{p} 现:{loc['corruption']}"


def reduce_central_corruption(state, pct: float) -> str:
    p = max(0.0, float(pct))
    state.central_corruption = max(0.0, round(getattr(state, "central_corruption", 50.0) - p, 1))
    return f"中央腐败-{p} 现:{state.central_corruption}"


def add_class_pop(state, province: str, class_name: str, count: int) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    cls = loc.get("classes", {}).get(class_name, {})
    cnt = max(0, int(count))
    cur_exact = int(cls.get("pop_exact") or round(float(cls.get("pop", 0)) * 10000))
    cls["pop_exact"] = cur_exact + cnt
    cls["pop"] = round(cls["pop_exact"] / 10000.0, 4)
    return f"{pname}{class_name}人口+{cnt}"


def add_class_attitude(state, province: str, class_name: str, points: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    cls = loc.get("classes", {}).get(class_name, {})
    pts = max(0.0, float(points))
    cls["attitude"] = min(100.0, round(float(cls.get("attitude", 60)) + pts, 3))
    return f"{pname}{class_name}态度+{pts} 现:{cls['attitude']}"


def reduce_class_attitude(state, province: str, class_name: str, points: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    cls = loc.get("classes", {}).get(class_name, {})
    pts = max(0.0, float(points))
    cls["attitude"] = max(0.0, round(float(cls.get("attitude", 60)) - pts, 3))
    return f"{pname}{class_name}态度-{pts} 现:{cls['attitude']}"


def add_prestige(state, amount: float) -> str:
    pts = max(0.0, float(amount))
    state.prestige_bonus = getattr(state, "prestige_bonus", 0.0) + pts
    from .depts_rites import calc_prestige
    pr = calc_prestige(state)["value"]
    return f"威望+{amount:.1f} 现:{pr:.1f}"


def transfer_treasury_to_local_reserve(state, province: str, amount: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    amount = max(0.0, float(amount))
    actual = min(float(state.treasury), amount)
    state.treasury = round(max(0.0, float(state.treasury) - actual), 2)
    state.local_reserve = round(float(state.local_reserve) + actual, 2)
    _record_national_tx(state, "支出", actual, "拨付地方", f"从国库拔银 {actual:.2f}万两 拨入{pname}地方窖藏")
    return f"国库拨付{pname}-{actual:.2f}万两 国库:{state.treasury:.2f}万两"


def transfer_privy_to_local_reserve(state, province: str, amount: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    amount = max(0.0, float(amount))
    actual = min(float(state.privy_treasury), amount)
    state.privy_treasury = round(max(0.0, float(state.privy_treasury) - actual), 2)
    state.local_reserve = round(float(state.local_reserve) + actual, 2)
    _record_privy_tx(state, "支出", actual, "私库拨付地方", f"皇帝发内帑银 {actual:.2f}万两 注入{pname}地方窖藏")
    return f"私库拨付{pname}-{actual:.2f}万两 私库:{state.privy_treasury:.2f}万两"


def transfer_treasury_gold_to_local(state, province: str, amount: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    amount = max(0.0, float(amount))
    actual = min(float(state.gold_treasury), amount)
    state.gold_treasury = round(max(0.0, float(state.gold_treasury) - actual), 4)
    state.gold_local_reserve = round(float(state.gold_local_reserve) + actual, 4)
    _record_national_tx(state, "支出", actual, "黄金拨付", f"从国库拨付黄金 {actual:.2f}万两 入{pname}地方")
    return f"国库黄金拨发{pname}-{actual:.2f}万两 国库黄金:{state.gold_treasury:.2f}万两"


def reduce_local_infra(state, province: str, points: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    pts = max(0.0, float(points))
    loc["infra"] = max(0.0, round(float(loc.get("infra", 0)) - pts, 1))
    return f"{pname}基建-{pts} 现:{loc['infra']}"


def add_local_corruption(state, province: str, pct: float) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    p = max(0.0, float(pct))
    loc["corruption"] = min(100.0, round(float(loc.get("corruption", 50)) + p, 1))
    return f"{pname}腐败+{p} 现:{loc['corruption']}"


def add_central_corruption(state, pct: float) -> str:
    p = max(0.0, float(pct))
    state.central_corruption = min(100.0, round(getattr(state, "central_corruption", 50.0) + p, 1))
    return f"中央腐败+{p} 现:{state.central_corruption}"


def reduce_class_pop(state, province: str, class_name: str, count: int) -> str:
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    cls = loc.get("classes", {}).get(class_name, {})
    cnt = max(0, int(count))
    cur_exact = int(cls.get("pop_exact") or round(float(cls.get("pop", 0)) * 10000))
    cls["pop_exact"] = max(0, cur_exact - cnt)
    cls["pop"] = round(cls["pop_exact"] / 10000.0, 4)
    return f"【负面操作】灾变兵燹，{pname}【{class_name}】人口减少 {cnt} 人 现有人口 {cls['pop_exact']} 人。"


def reduce_prestige(state, amount: float) -> str:
    pts = max(0.0, float(amount))
    state.prestige_penalty = getattr(state, "prestige_penalty", 0.0) + pts
    from .depts_rites import calc_prestige
    pr = calc_prestige(state)["value"]
    return f"威望-{amount:.1f} 现:{pr:.1f}"


def apply_reward_text(state, reward_text: str, default_province: str = "shaanxi") -> List[str]:
    """解析并实际执行章奏奖惩效果 reward_processed / reward_unprocessed / selected_reward。
    支持中英文逗号、顿号、分号分隔的多条指令组合，全面覆盖 10 类正负操作。
    建议数值转换规范：
    - 白银：AI 若返回两 如 1000两、2000两，折算为万两 1000两 = 0.1万两；若已为万两，直接使用；
    - 黄金：AI 若返回两 如 500两，折算为万两 500两 = 0.05万两；
    - 态度/腐败/基建/威望：支持 0.1~1.0 或点数增减。
    """
    notes = []
    if not state or not reward_text:
        return notes

    clauses = re.split(r"[,，;；\n]+", str(reward_text).strip())

    for clause in clauses:
        c = clause.strip()
        if not c or len(c) < 3:
            continue

        pname, cname = _extract_prov_and_class(c)
        if pname == "beizhili" and default_province and default_province != "beizhili":
            for prov_candidate in [default_province, PROV_REV.get(default_province, "")]:
                if prov_candidate:
                    pname = _get_prov_id(prov_candidate)
                    break

        m_num = re.search(r"(\d+(?:\.\d+)?)", c)
        raw_val = float(m_num.group(1)) if m_num else 0.1

        # 1. 白银调拨 建议数值 0-100000 两
        if "银" in c:
            val_wan = raw_val / 10000.0 if  "万两" not in c and raw_val > 50.0 else raw_val
            if any (k in c for k in ["增加地方窖藏银两到国库", "调拨地方窖藏到国库", "地方解缴国库", "银两到国库", "解缴国库"]):
                notes.append(transfer_local_reserve_to_treasury(state, pname, val_wan))
                continue
            if any (k in c for k in ["减少国库银两到地方窖藏", "拨付国库到地方", "减少国库银两", "动支太仓", "发太仓银", "国库支出"]):
                notes.append(transfer_treasury_to_local_reserve(state, pname, val_wan))
                continue
            if any (k in c for k in ["增加地方窖藏银两到私库", "地方进奉内帑", "银两到私库"]):
                notes.append(transfer_local_reserve_to_privy(state, pname, val_wan))
                continue
            if any (k in c for k in ["减少私库银两到地方窖藏", "发内帑", "内帑拨赈"]):
                notes.append(transfer_privy_to_local_reserve(state, pname, val_wan))
                continue

        # 2. 黄金储备 建议数值 0-10000 两
        if "金" in c:
            val_wan = raw_val / 10000.0 if  "万两" not in c and raw_val > 50.0 else raw_val
            if any (k in c for k in ["增加地方黄金到国库", "起获地方黄金", "黄金到国库"]):
                notes.append(transfer_local_gold_to_treasury(state, pname, val_wan))
                continue
            if any (k in c for k in ["减少国库黄金到地方", "拨付黄金"]):
                notes.append(transfer_treasury_gold_to_local(state, pname, val_wan))
                continue

        # 3. 阶级态度调整 支持 0.1~1.0 或点数
        if "态度" in c:
            is_reduce = any (neg in c for neg in ["降低", "减少", "下降", "削减", "-"])
            target_classes = []
            for cls_cand in ["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]:
                if cls_cand in c or  cls_cand == "百工" and "白工" in c:
                    target_classes.append(cls_cand)
            if not target_classes:
                target_classes = [cname or "农民"]

            for tc in target_classes:
                if is_reduce:
                    notes.append(reduce_class_attitude(state, pname, tc, raw_val))
                else:
                    notes.append(add_class_attitude(state, pname, tc, raw_val))
            continue

        # 4. 腐败程度调整
        if "腐败" in c:
            is_reduce = any (neg in c for neg in ["降低", "减少", "整肃", "肃清", "下降", "-"])
            if "中央" in c:
                if is_reduce:
                    notes.append(reduce_central_corruption(state, raw_val))
                else:
                    notes.append(add_central_corruption(state, raw_val))
            else:
                if is_reduce:
                    notes.append(reduce_local_corruption(state, pname, raw_val))
                else:
                    notes.append(add_local_corruption(state, pname, raw_val))
            continue

        # 5. 基建程度
        if "基建" in c or "工程" in c:
            if any (k in c for k in ["降低", "减少", "破坏", "-"]):
                notes.append(reduce_local_infra(state, pname, raw_val))
            else:
                notes.append(add_local_infra(state, pname, raw_val))
            continue

        # 6. 人口增减
        if "人口" in c:
            exact_cnt = int(raw_val * 10000) if raw_val < 100 else int(raw_val)
            if any (k in c for k in ["降低", "减少", "伤亡", "流亡", "-"]):
                notes.append(reduce_class_pop(state, pname, cname or "农民", exact_cnt))
            else:
                notes.append(add_class_pop(state, pname, cname or "农民", exact_cnt))
            continue

        # 7. 皇室威望
        if "威望" in c:
            if any (k in c for k in ["降低", "减少", "削减", "受损", "-"]):
                notes.append(reduce_prestige(state, raw_val))
            else:
                notes.append(add_prestige(state, raw_val))
            continue

        # 8. 官员革职/删除官员超控处理 严格对齐标准指令
        if any (k in c for k in ["删除地方官员", "更换中央官员", "修改人物状态"]):
            try:
                from .officials import list_active, delete_official
                from .char_cards import get_card, patch_state

                # 8.1 更换中央官员 [部门名] [官职] [新官] 或 [部门名] [原官] [新官]
                m_rep_cnt = re.search(r"更换中央官员\s+([^\s]+)\s+([^\s]+)\s+([^\s]+)", c)
                if m_rep_cnt:
                    ok, msg = replace_dept_staff_unified(state, m_rep_cnt.group(1), m_rep_cnt.group(2), m_rep_cnt.group(3))
                    if ok:
                        notes.append(msg)
                    continue

                # 8.2 删除地方官员 [原官名]
                m_del = re.search(r"删除地方官员\s+([^\s，,。]+)", c)
                if m_del:
                    t_name = m_del.group(1).strip()
                    c_card = get_card(t_name)
                    if c_card:
                        patch_state((t_name, {"状态": "赋闲", "现职": "致仕革职"}))
                    for o in list_active():
                        if o.get("name") == t_name or t_name in o.get("name", ""):
                            delete_official(o["id"])
                    notes.append (f"删除官员:{t_name}")
                    continue

                # 8.3 修改人物状态 [姓名] [状态]
                m_st = re.search(r"修改人物状态\s+([^\s]+)\s+([^\s]+)", c)
                if m_st:
                    ok, msg = set_person_status(m_st.group(1), m_st.group(2))
                    if ok:
                        notes.append(msg)
                    continue
            except Exception:
                pass
            continue

    return notes


def change_local_infra(state, province: str, value: float) -> str:
    """基建程度一条指令：正值抬高、负值压低。"""
    return add_local_infra(state, province, value) if value >= 0 else reduce_local_infra(state, province, -value)


def change_local_corruption(state, province: str, value: float) -> str:
    """地方腐败程度一条指令：正值加剧、负值压低。"""
    return add_local_corruption(state, province, value) if value >= 0 else reduce_local_corruption(state, province, -value)


def change_central_corruption(state, value: float) -> str:
    """中央腐败程度一条指令：正值加剧、负值压低。"""
    return add_central_corruption(state, value) if value >= 0 else reduce_central_corruption(state, -value)


def change_prestige(state, value: float) -> str:
    """皇室威望一条指令：正值增益、负值折损。"""
    return add_prestige(state, value) if value >= 0 else reduce_prestige(state, -value)


def relieve_class(state, province: str, class_name: str, amount_wan: float) -> str:
    """赈济：给该省该阶级定下每月赈济额。

    真相源是 loc.classes[阶级].support（两/月）：经济系统每月按它从国库出银、
    等额折成该阶级的供给（见 economy.relief_matter_wan）。
    """
    pid = _get_prov_id(province)
    pname = PROV_REV.get(pid, province)
    loc = state.provinces.get(pid, {}).get("local", {})
    cls = (loc.get("classes") or {}).get(class_name)
    if cls is None:
        return "【赈济未执行】%s 没有【%s】这个阶级。" % (pname, class_name)
    add_liang = float(amount_wan) * 10000.0
    cls["support"] = round(float(cls.get("support") or 0.0) + add_liang, 2)
    return "%s%s赈济额 %+.2f万两/月，现为 %.2f万两/月（由国库按月出银）。" % (
        pname, class_name, float(amount_wan), cls["support"] / 10000.0)


def _transfer_checked(state, province: str, amount: float, fn, label: str):
    """调拨类指令的公共校验：省要真的存在、数额要为正，再动手挪银子。"""
    pid = _get_prov_id(province)
    if not pid:
        return False, "%s：没有【%s】这个省" % (label, province)
    if amount <= 0:
        return False, "%s：数额必须大于 0，收到 %s" % (label, amount)
    return True, fn(state, PROV_REV.get(pid, province), amount)


def execute_single_command(state, cmd: str) -> Optional[str]:
    """阶级人口／阶级态度两条（还没进超控指令表）的严格解析。

    以前这里是「原有 20 类基础数值操作」的关键字子串匹配：数值取全串第一个数字、
    没有数字就当 1、省份缺省落到北直隶、还认「增加人口／提升态度」一堆别名，
    写错了照样"成功"。已按纪律删掉——表里有的指令都走 execute_operation_command
    的锚定正则，这里只剩下还没进表的这两类，写法固定，缺参数就返回 None。
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return None

    NUM = r"([+-]?\d+(?:\.\d+)?)$"
    for pat, sign in ((r"^增加地方各阶级人口\s+(\S+)\s+(\S+)\s+" + NUM, 1),
                      (r"^减少地方各阶级人口\s+(\S+)\s+(\S+)\s+" + NUM, -1)):
        m = re.match(pat, cmd)
        if m:
            prov, cls, raw = m.group(1), m.group(2), float(m.group(3))
            pid = _get_prov_id(prov)
            if not pid or cls not in CLASSES:
                return None
            exact = int(raw * 10000) if raw < 100 else int(raw)
            pname = PROV_REV.get(pid, prov)
            return (add_class_pop if sign > 0 else reduce_class_pop)(state, pname, cls, exact)

    for pat, sign in ((r"^增加地方各阶级态度\s+(\S+)\s+(\S+)\s+" + NUM, 1),
                      (r"^降低地方各阶级态度\s+(\S+)\s+(\S+)\s+" + NUM, -1)):
        m = re.match(pat, cmd)
        if m:
            prov, cls, pts = m.group(1), m.group(2), float(m.group(3))
            pid = _get_prov_id(prov)
            if not pid or cls not in CLASSES:
                return None
            pname = PROV_REV.get(pid, prov)
            return (add_class_attitude if sign > 0 else reduce_class_attitude)(state, pname, cls, pts)

    return None


# ═══════════════════════════════════════════════════════════
# 超控指令表 OVERRIDE_COMMANDS 唯一真相源
# ═══════════════════════════════════════════════════════════
# 每条 (group, cmd, params, example, intro, impl)
# - cmd     指令写法，参数用尖括号占位
# - params  参数说明，空串表示无参数
# - example 一条能直接抄的示例
# - impl    真正执行它的函数名，便于核对"帮助文本写了但派发器不认"这种偏差
# 帮助文本 get_all_operations_schema 由这张表生成，不许再手写第二份。
OVERRIDE_COMMANDS: List[Dict[str, str]] = [
    # 1 地方军队
    {"group": "地方军队管理", "cmd": "删除军队 <省份> <军队名称>", "params": "省份与军队一律写名称",
     "example": "删除军队 四川 奢寅部叛军", "intro": "把一支地方军队从军册上抹掉", "impl": "delete_army"},
    # 2 独立地区修正
    # 3 衙署与中央官员
    {"group": "衙署部门与中央官员", "cmd": "增加部门 <分类> <部门名称>",
     "params": "分类必填，只能取 外廷（外诸司）或 内廷（二十四衙门），决定新衙署落在哪张登记表",
     "example": "增加部门 外廷 军器总督司", "intro": "新增一个衙署", "impl": "create_dept"},
    {"group": "衙署部门与中央官员", "cmd": "删除部门 <部门名称>",
     "params": "部门按名字找，不用写分类；六部不在此列，只有外诸司与二十四衙门可以裁撤",
     "example": "删除部门 混堂司", "intro": "裁撤一个衙署", "impl": "delete_dept"},
    {"group": "衙署部门与中央官员", "cmd": "增加部门职位 <部门名> <官职>", "params": "",
     "example": "增加部门职位 兵仗局 军械监理太监", "intro": "给衙署加一个编制职位", "impl": "add_dept_staff"},
    {"group": "衙署部门与中央官员", "cmd": "删除部门职位 <部门名> <官职>",
     "params": "官职写全称，编制里就是「部门名+职位」的样子（如 惜薪司掌印太监）",
     "example": "删除部门职位 惜薪司 惜薪司掌印太监", "intro": "撤掉一个编制职位", "impl": "delete_dept_staff"},
    {"group": "衙署部门与中央官员", "cmd": "更换中央官员 <部门名> <官职或原官> <新官>", "params": "第二种写法直接用原官名替换官职",
     "example": "更换中央官员 司礼监 司礼监掌印太监 王承恩", "intro": "换掉在京衙署里的现任官员",
     "impl": "replace_dept_staff_unified"},
    # 4 吏部
    {"group": "吏部人事与行政纪律", "cmd": "更换文官 <省份> <官职> <原官名> <新官名>", "params": "",
     "example": "更换文官 河南 河南巡抚 郭增光 李自成", "intro": "换掉一名地方文官", "impl": "replace_civil_official"},
    {"group": "吏部人事与行政纪律", "cmd": "增加文职 <省份> <官职>",
     "params": "官职写「XX道佥事」这类具名职位；同名职位已存在则改为补人，不重复建位。",
     "example": "增加文职 陕西 督粮道佥事", "intro": "在文册档案里增设一个地方文职", "impl": "add_civil_office"},
    {"group": "吏部人事与行政纪律", "cmd": "删除文职 <省份> <官职>", "params": "按省名与官职名撤缺",
     "example": "删除文职 陕西 延安府知府", "intro": "裁撤一个地方文职", "impl": "delete_civil_office"},
    {"group": "吏部人事与行政纪律", "cmd": "删除地方官员 <原官名>", "params": "革职撤缺专线",
     "example": "删除地方官员 贾承渊", "intro": "注销官缺并把该员置为赋闲", "impl": "delete_local_officer"},
    # 5 户部
    {"group": "户部赋税体系", "cmd": "增加税目 <税名> <分类> <税目修正>",
     "params": "分类取田赋／盐税／关税／人头税／杂税／加派／捐纳；税目修正就是公式里的那个量：月征额 = 课税基数 × 税目修正 ÷ 10，公式里没有「税率」",
     "example": "增加税目 海舶新课 关税 0.005", "intro": "新增一个税目", "impl": "create_tax_item"},
    {"group": "户部赋税体系", "cmd": "修改税目修正 <税名> <税目修正>",
     "params": "与「增加税目」指的是同一个量、同一条公式（月征额 = 课税基数 × 税目修正 ÷ 10）",
     "example": "修改税目修正 丁银 8", "intro": "改一个税目的税目修正", "impl": "depts_hubu.modify_tax_rate"},
    {"group": "户部赋税体系", "cmd": "删除税目 <税名>", "params": "",
     "example": "删除税目 坊厢银", "intro": "废除一个税目", "impl": "delete_tax_item"},
    # 6 礼部
    {"group": "礼部外交与科举教育", "cmd": "修改外交关系 <国家名称> <关系> <贸易状态>",
     "params": "关系取藩属、和平、战争；贸易状态取贸易、禁贸",
     "example": "修改外交关系 朝鲜 藩属 贸易", "intro": "改一国的外交关系与通商状态", "impl": "set_diplomatic_relation"},
    {"group": "礼部外交与科举教育", "cmd": "创建修正 礼部 <修正名> 全国 <受教育人口年增/+N%> <介绍>",
     "params": "教育修正也是修正：词条用 受教育人口年增 或 受高等教育人口年增，走同一条统一修正指令",
     "example": "创建修正 礼部 各府设义学 全国 受教育人口年增+5% 各府州县广设义学",
     "intro": "礼部界面里的教育修正：教育修正也是修正，走同一条统一修正指令",
     "impl": "create_modifier"},
    # 7 兵部
    {"group": "兵部武职与粮饷", "cmd": "修改粮饷加派 <月征数额万两>", "params": "改的是税目辽饷加派的税目修正",
     "example": "修改粮饷加派 30.0",
     "intro": "辽饷本指是税目，只是界面摆在兵部：本指令与修改税目修正走同一条路、同一个字段",
     "impl": "modify_pay_category"},
    {"group": "兵部武职与粮饷", "cmd": "增加武职 <省份或区域> <武职名称>", "params": "",
     "example": "增加武职 辽东 广宁守备", "intro": "增设一个武职", "impl": "add_military_office"},
    {"group": "兵部武职与粮饷", "cmd": "删除武职 <省份或区域> <武职名称>", "params": "",
     "example": "删除武职 云南 镇守云南总兵官", "intro": "裁撤一个武职", "impl": "delete_military_office"},
    {"group": "兵部武职与粮饷", "cmd": "更换武官 <省份或区域> <武职名称> <原名> <新名>",
     "params": "省份、武职名称、原名三者都要与武职名册对得上（名册见兵部武官人事）",
     "example": "更换武官 云南 镇守云南总兵官 沐启元 沐天波", "intro": "换掉一名武官", "impl": "replace_military_officer"},
    # 8 刑部
    # 9 工部
    {"group": "工部器物与工程建设", "cmd": "新建军事建筑 <省份> <名称> <介绍> <造价万两> <工期月数>", "params": "工程的六个属性：名称／修正／范围／建筑类型／状态／造价。工期只是新建时的临时属性，月度花费 ＝ 造价 ÷ 工期，年维护费 ＝ 造价 × 1%。",
     "example": "新建军事建筑 陕西 榆林新边墙 遮蔽延绥东路 18 12", "intro": "新建一处军事建筑", "impl": "create_project"},
    {"group": "工部器物与工程建设", "cmd": "新建国家建筑 <省份> <名称> <修正> <介绍> <造价万两> <工期月数>", "params": "修正按修正词条写。工程的六个属性：名称／修正／范围／建筑类型／状态／造价。工期只是新建时的临时属性，月度花费 ＝ 造价 ÷ 工期，年维护费 ＝ 造价 × 1%。",
     "example": "新建国家建筑 南直隶 南京外城 全国所有阶级产能+3% 增筑外郭以卫陵寝 200 24",
     "intro": "新建一处国家建筑", "impl": "create_project"},
    {"group": "工部器物与工程建设", "cmd": "新建工程建筑 <省份> <名称> <修正> <介绍> <造价万两> <工期月数>", "params": "修正按修正词条写。工程的六个属性：名称／修正／范围／建筑类型／状态／造价。工期只是新建时的临时属性，月度花费 ＝ 造价 ÷ 工期，年维护费 ＝ 造价 × 1%。",
     "example": "新建工程建筑 北直隶 天津水田新垦 农民产能+10% 开田三千亩 8 6",
     "intro": "新建一处工程建筑", "impl": "create_project"},
    {"group": "工部器物与工程建设", "cmd": "新建灾害建筑 <省份> <名称> <修正> <介绍> <造价万两> <工期月数>", "params": "修正按修正词条写。工程的六个属性：名称／修正／范围／建筑类型／状态／造价。工期只是新建时的临时属性，月度花费 ＝ 造价 ÷ 工期，年维护费 ＝ 造价 × 1%。",
     "example": "新建灾害建筑 河南 开封新石堤 全国所有阶级产能+5% 御黄汛护城 16 8",
     "intro": "新建一处防灾建筑", "impl": "create_project"},
    {"group": "工部器物与工程建设", "cmd": "新建矿场 <省份> <名称> <修正> <介绍> <造价万两> <工期月数>",
     "params": "矿场修正写白银若干万两每年或黄金若干两每年。工程的六个属性：名称／修正／范围／建筑类型／状态／造价。工期只是新建时的临时属性，月度花费 ＝ 造价 ÷ 工期，年维护费 ＝ 造价 × 1%。",
     "example": "新建矿场 云南 个旧锡矿 白银6万两/年 官办锡场 30 10", "intro": "新建一处矿场", "impl": "create_project"},
    {"group": "工部器物与工程建设", "cmd": "删除工程 <省份> <工程名称>", "params": "",
     "example": "删除工程 陕西 延绥镇边墙", "intro": "拆除一处工程", "impl": "delete_project"},
    {"group": "工部器物与工程建设", "cmd": "工程损毁程度 <省份> <工程名称> <百分比>",
     "params": "百分比 0~100，0 为完好、100 为全毁。工程的一切效果按 (1−损毁程度) 折算。",
     "example": "工程损毁程度 陕西 灵州黄河石堤 40",
     "intro": "记录或修复一处工程的损毁程度", "impl": "set_project_damage"},
    # 10 基础数值
    {"group": "基础调库与国策数值", "cmd": "调拨地方窖藏到国库 <省份> <万两>", "params": "",
     "example": "调拨地方窖藏到国库 陕西 5", "intro": "把地方窖藏解缴国库", "impl": "transfer_local_reserve_to_treasury"},
    {"group": "基础调库与国策数值", "cmd": "拨付国库到地方 <省份> <万两>", "params": "",
     "example": "拨付国库到地方 河南 3", "intro": "从国库拨银到地方窖藏", "impl": "transfer_treasury_to_local_reserve"},
    {"group": "基础调库与国策数值", "cmd": "赈济 <省份> <阶级> <万两>",
     "params": "给该省该阶级定下每月赈济额，由国库按额出银、等额折成该阶级供给；阶级必写（宗室／士绅／商人／农民／百工／百役／流民）",
     "example": "赈济 陕西 农民 5", "intro": "赈济指定省份的指定阶级", "impl": "relieve_class"},
    {"group": "基础调库与国策数值", "cmd": "地方基建程度 <省份> <数值>",
     "params": "一条指令一个词条：正值抬高、负值压低，例如 -5 就是压低 5 点；只写数值，不写增加降低",
     "example": "地方基建程度 陕西 5", "intro": "改一个省的基建程度", "impl": "change_local_infra"},
    {"group": "基础调库与国策数值", "cmd": "地方腐败程度 <省份> <数值>",
     "params": "正值加剧、负值压低，例如 -3 就是压低 3 个百分点；只写数值，不写增加降低",
     "example": "地方腐败程度 河南 -3", "intro": "改一个省的地方腐败程度", "impl": "change_local_corruption"},
    {"group": "基础调库与国策数值", "cmd": "中央腐败程度 <数值>",
     "params": "正值加剧、负值压低；只写数值，不写增加降低",
     "example": "中央腐败程度 -2", "intro": "改中央腐败程度", "impl": "change_central_corruption"},
    {"group": "基础调库与国策数值", "cmd": "皇室威望 <数值>",
     "params": "正值增益、负值折损；只写数值，不写增加降低",
     "example": "皇室威望 -5", "intro": "改皇室威望", "impl": "change_prestige"},
    # 11 人物
    {"group": "人物面板数据调整", "cmd": "修改党派到人名 <姓名> <党派>", "params": "同步绑定人物卡与官员数据库",
     "example": "修改党派到人名 秦良玉 阉党", "intro": "改一个人的党派", "impl": "set_person_faction"},
    {"group": "人物面板数据调整", "cmd": "修改人物位置 <姓名> <省份>", "params": "位置只到省份，京师本城写北直隶",
     "example": "修改人物位置 魏忠贤 南直隶", "intro": "改一个人所在省份", "impl": "set_person_location"},
    {"group": "人物面板数据调整", "cmd": "修改人物官职 <姓名> <官衔>", "params": "",
     "example": "修改人物官职 郭增光 中书舍人", "intro": "改一个人的官衔", "impl": "set_person_office"},
    {"group": "人物面板数据调整", "cmd": "修改人物状态 <姓名> <状态>", "params": "状态只取在职、居乡、赋闲、死亡四种；名下有在任官职的人锁定为在职",
     "example": "修改人物状态 田尔耕 赋闲", "intro": "改一个人的状态并同步人物卡", "impl": "set_person_status"},
    # 12 部门修正
    # ── 修正（统一指令）：修正只有一个物种，显示位置是参数 ──
    {"group": "修正（统一指令）", "cmd": "创建修正 <显示位置> <修正项目> <修正范围> <修正效果> <介绍>",
     "params": "显示位置＝界面的名字：吏部／户部／礼部／兵部／刑部／工部／内阁／司礼监／都察院／通政司，"
               "或某个省名（「中央」是修正范围的取值，不是界面）。"
               "修正范围＝中央／全国／本省／全省／某省名。"
               "修正效果＝修正词条组合，逗号分隔。介绍＝一句话，写在效果之后。",
     "example": "创建修正 刑部 《海防新例》 中央 中央腐败程度-5% 重刊律例，整肃刑名",
     "intro": "先有修正，再决定它显示在哪：显示位置是省就进该省的地区修正表，其余进该部门的修正栏。",
     "impl": "create_modifier"},
    {"group": "修正（统一指令）", "cmd": "修改修正 <修正项目> <修正效果> <介绍>",
     "params": "按修正项目找，几处存储里都能找到；只改效果与介绍，不改它显示在哪",
     "example": "修改修正 《大明律》 中央腐败程度-3% 重刊律例",
     "intro": "只改效果，不改它显示在哪。",
     "impl": "modify_modifier"},
    {"group": "修正（统一指令）", "cmd": "删除修正 <修正项目>",
     "params": "按修正项目找，找到即删",
     "example": "删除修正 《大明律》",
     "intro": "删除一条修正，不论它显示在哪。",
     "impl": "delete_modifier"},

    # ── 国家 AI 专用（ai=True 的条目才会进 {{operation_ai}} 清单） ──
    {"group": "宣战", "cmd": "宣战 <国家名称>", "params": "", "example": "宣战 朝鲜",
     "intro": "对该国转入战争状态并禁贸", "impl": "set_diplomatic_relation", "ai": True},
    {"group": "求和", "cmd": "求和 <国家名称>", "params": "仅在引擎开放时可用", "example": "求和 朝鲜",
     "intro": "发起求和外交会晤", "impl": "set_diplomatic_relation", "ai": True},
    {"group": "贸易", "cmd": "贸易 <国家名称>", "params": "仅在引擎开放时可用", "example": "贸易 朝鲜",
     "intro": "发起贸易外交会晤，含进口装备", "impl": "set_diplomatic_relation", "ai": True},
    {"group": "军队调动", "cmd": "军队调动 <军队名称> <目标省份>", "params": "", "example": "军队调动 满清八旗 辽东",
     "intro": "把本国一支部队调往目标省份", "impl": "armies.dispatch_army", "ai": True},
    {"group": "修改军队战役指令", "cmd": "修改军队战役指令 <军队名称> <项目> <值>", "params": "",
     "example": "修改军队战役指令 满清八旗 任务指令 决战",
     "intro": "改本国一支部队的作战姿态",
     "detail": "项目与可选值严格限于：\n- 任务指令: 决战／巡逻／守备\n"
               "- 交战风险: 避战／低风险交战／中风险交战／高风险交战／逢敌必战\n"
               "- 撤退优先级: 从不撤退／低撤退优先级／高撤退优先级\n"
               "- 战役指令: 自由文本  例: 寻机决战",
     "impl": "armies.update_army_naval_posture", "ai": True},

]


def term_help_lines() -> str:
    """可用的修正词条：词条表是效果栏的合法用词全集，不在表里的写法一律判撰写错误。"""
    from .modifier_terms import MODIFIER_TERMS
    lines = []
    for t in MODIFIER_TERMS:
        lines.append("     - %s：%s" % (t["term"], " 或 ".join(t["value_rules"])))
    return "\n".join(lines)
def operations_schema_from_table() -> str:
    """由 OVERRIDE_COMMANDS 里**非国家 AI 专用**的条目生成全域指令清单。

    宣战／求和／贸易／军队调动／修改军队战役指令 标了 ai，只能走 execute_ai_operations，
    全域派发器不认，因此不进这份清单——否则 AI 会被塞进自己执行不了的指令。"""
    rows = [r for r in OVERRIDE_COMMANDS if not r.get("ai")]
    head = ["【逻辑引擎系统超控指令清单】", "你可以使用以下标准指令修改游戏底层状态 每行一条："]
    groups: List[str] = []
    for row in rows:
        if row["group"] not in groups:
            groups.append(row["group"])
    out = list(head)
    for gi, g in enumerate(groups, start=1):
        out.append("%d. %s：" % (gi, g))
        for row in [r for r in rows if r["group"] == g]:
            if not row["impl"]:
                out.append("   - %s  例: %s" % (row["cmd"], row["example"]))
                continue
            if row["example"].startswith(row["cmd"].split(" <")[0]):
                out.append("   - %s  例: %s" % (row["cmd"], row["example"]))
            else:
                out.append("   - %s" % row["cmd"])
                out.append("       %s" % row["example"])
            if row["params"]:
                out.append("     参数：%s" % row["params"])
        if g == "修正（统一指令）":
            out.append("   可选修正词条支持逗号分隔组合，词条表如下：")
            out.append(term_help_lines())
            out.append("   不在词条表里的写法一律判词条撰写错误，指令会被拒绝")
    return "\n".join(out).strip()


def get_all_operations_schema() -> str:
    """给 AI 的超控指令清单：由 OVERRIDE_COMMANDS 与修正词条表生成，不手写第二份。"""
    return operations_schema_from_table()


# 国家 AI 专用超控指令 Prompt/diguo_ai.txt 的 {{operation_ai}}
# ═══════════════════════════════════════════════════════════

def get_ai_operations_schema() -> str:
    """国家 AI 专用超控指令清单：由表里 ai=True 的条目生成，不再手写第二份。

    条目一律是「- 指令 <参数>」，补充说明（detail）不带「-」，只缩进——
    否则 AI 会把「任务指令: 决战」也当成一条指令照发。"""
    ai_rows = [r for r in OVERRIDE_COMMANDS if r.get("ai")]
    groups: List[str] = []
    for r in ai_rows:
        if r["group"] not in groups:
            groups.append(r["group"])
    lines = ["【国家 AI 专用超控指令清单】"]
    for i, g in enumerate(groups, 1):
        lines.append("%d. %s：" % (i, g))
        for r in [x for x in ai_rows if x["group"] == g]:
            tail = "  例: %s" % r["example"]
            if r.get("params"):
                tail += "  " + r["params"]
            lines.append("   - %s%s" % (r["cmd"], tail))
            if r.get("detail"):
                for dl in str(r["detail"]).split("\n"):
                    t = dl.strip().lstrip("-").strip()
                    if t:
                        lines.append("      " + t)
    return "\n".join(lines)


AI_OPERATIONS_DOC = get_ai_operations_schema()

# 项目名 -> update_army_naval_posture 的关键字参数 兼容国家 AI 的自然说法
_AI_POSTURE_FIELDS = {
    "任务指令": "mission", "任务": "mission",
    "交战风险": "risk", "交战倾向": "risk",
    "撤退优先级": "retreat", "撤退倾向": "retreat",
    "战役指令": "battle_order",
}

_AI_POSTURE_VALID = {
    "mission": ["决战", "巡逻", "守备"],
    "risk": ["避战", "低风险交战", "中风险交战", "高风险交战", "逢敌必战"],
    "retreat": ["从不撤退", "低撤退优先级", "高撤退优先级"],
}


def execute_ai_operation(state, cmd: str, faction: str = "") -> Tuple[bool, str]:
    """执行一条国家 AI 专用超控指令，返回 (ok, msg)。

    `faction` 是下令一方 国家 AI 的势力名，如 满清；缺省视作明。
    名册是全局一份、任何势力的部队都在里面，所以军队类指令一律带归属校验：
    只能调本国部队、只能改本国部队的战役指令。
    """
    from .armies import dispatch_army, update_army_naval_posture, owner_of

    cmd = (cmd or "").strip()
    if not cmd:
        return False, "空指令"
    if state is None:
        return False, "系统状态异常"
    cmd_owner = owner_of(faction) or (faction if faction in ("ming",) else "") or "ming"

    # 1-3) 宣战 / 求和 / 贸易
    m = re.match(r"^(宣战|求和|贸易)\s+(\S+)$", cmd)
    if m:
        act, country = m.group(1), m.group(2)
        if act == "宣战":
            return set_diplomatic_relation(state, country, "战争", "禁贸")
        if act == "求和":
            return set_diplomatic_relation(state, country, "和平", "禁贸")
        return set_diplomatic_relation(state, country, "和平", "贸易")

    # 4) 军队调动
    m = re.match(r"^军队调动\s+(\S+)\s+(\S+)$", cmd)
    if m:
        army, target = m.group(1), m.group(2)
        pid = PROV_MAP.get(target) or (target if target in state.provinces else "")
        if not pid or pid not in state.provinces:
            return False, f"未找到目标省份【{target}】"
        return dispatch_army(army, pid, state, owner=cmd_owner)

    # 5) 修改军队战役指令
    m = re.match(r"^修改军队战役指令\s+(\S+)\s+(\S+)\s+(.+)$", cmd)
    if m:
        army, field, value = m.group(1), m.group(2), m.group(3).strip()
        key = _AI_POSTURE_FIELDS.get(field)
        if not key:
            return False, f"未知项目【{field}】 限：任务指令／交战风险／撤退优先级／战役指令"
        allowed = _AI_POSTURE_VALID.get(key)
        if allowed and value not in allowed:
            return False, f"【{field}】取值不合法：{value} 限 {'／'.join(allowed)}"
        return update_army_naval_posture(army, owner=cmd_owner, **{key: value})

    return False, f"不是合法的国家 AI 指令：{cmd}"


def execute_ai_operations(state, cmds, faction: str = "") -> List[str]:
    """批量执行某国国家 AI 本季度的指令清单，返回逐条执行明细。"""
    logs = []
    for c in (cmds or []):
        ok, msg = execute_ai_operation(state, c, faction=faction)
        logs.append(("✓ " if ok else "✗ ") + str(msg))
    return logs


def execute_operation(state, cmd: str) -> str:
    """旧接口兼容：执行命令行并返回消息字符串。"""
    ok, msg = execute_operation_command(state, cmd)
    return msg


OPERATIONS_DOC = get_all_operations_schema()
