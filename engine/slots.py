"""章奏数据空位替换：{{时间 + 地点 + 数据类}} 三要素强制格式。

规则来源：Prompt/zhixian.txt 的 <rules4> 玩家撰写。
- 时间格式必须为：年+月+日 如 1627年8月1日
- 地点必须为：「国家」或 16 省之一 省份数据必须带省名
- 数据类取自 {{element}} 注入清单，AI 不可自造
- AI 不写单位；替换时由代码补上单位

查找策略 玩家确认：快照表中「不晚于目标日期的最近一条实际记录」
 回溯取值；目标日期晚于全部快照 -> 最新快照；无法解析 -> 保留原文。

数据字典 ELEMENTS是唯一数据类来源，后续新增/改名数据只需改此表。
省份数据类全部来自地方数据 local：人口与镇军人数为派生值，
税收总额 = Σ 产出 × 税收比例 见 engine/localdata.py。
"""

import re

SLOT_RE = re.compile(r"\{\{([^{}]+)\}\}")

# ── 数据字典 {{element}} 的来源；改这里即改 AI 可见数据类──
# field: 快照中的字段名 | province: 是否省份数据 | unit: 替换时附加的单位
# fmt: 数值格式 %.0f 整数类 / %.1f 数量类
ELEMENTS = {
    # 全局数据类 地点=国家
    "内库余额":     {"field": "treasury",   "province": False, "unit": "万两", "fmt": "%.1f"},
    "人口":         {"field": "population", "province": False, "unit": "万",   "fmt": "%.0f"},
    "陆军人数":     {"field": "army",       "province": False, "unit": "万人", "fmt": "%.1f"},
    "海军人数":     {"field": "navy",       "province": False, "unit": "万人", "fmt": "%.1f"},
    # 省份数据类 地点=省名，必须带省名
    "地方人口":      {"field": "population", "province": True, "unit": "万",   "fmt": "%.0f"},
    "镇军人数":      {"field": "garrison_count", "province": True, "unit": "万人", "fmt": "%.1f"},
    "基建程度":      {"field": "infra",      "province": True, "unit": "",    "fmt": "%.0f"},
    "税收总额":      {"field": "tax",        "province": True, "unit": "万两", "fmt": "%.1f"},
    "扶持总额":      {"field": "support",    "province": True, "unit": "两",   "fmt": "%.0f"},
}

PROVINCES = ["北直隶", "山东", "山西", "河南", "陕西", "南直隶", "四川",
             "湖广", "江西", "浙江", "福建", "广东", "广西", "云南", "贵州", "辽东"]

DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def build_element_text():
    """生成 {{element}} 注入文本 分组：全局/省份数据类。"""
    globs = [k for k, v in ELEMENTS.items() if not v["province"]]
    provs = [k for k, v in ELEMENTS.items() if v["province"]]
    return "全局数据类：%s\n省份数据类：%s" % ("、".join(globs), "、".join(provs))


def build_province_text():
    """生成 {{province}} 注入文本。"""
    return "、".join(PROVINCES)


def parse_slot(key):
    """解析三要素 -> ((y,m,d), place, element)；缺任一要素返回 None。

    格式 Prompt/zhixian.txt rules4，玩家定义：
      {{时间，地点，数据类}}，如 {{1627年8月1日，国家，内库余额}}
      {{1630年9月9日，江西，镇军人数}}
    分隔符兼容中文/英文逗号。
    """
    parts = [p.strip() for p in re.split(r"[，,]", key)]
    if len(parts) != 3:
        return None
    time_part, place_part, elem_part = parts
    m = DATE_RE.search(time_part)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if place_part == "国家":
        place = "国家"
    elif place_part in PROVINCES:
        place = place_part
    else:
        return None
    if elem_part not in ELEMENTS:
        return None
    meta = ELEMENTS[elem_part]
    # 数据类与地点必须匹配：全局类=国家；省份类=省名
    if not meta["province"] and place != "国家":
        return None
    if meta["province"] and place == "国家":
        return None
    return (y, mo, d), place, elem_part


def _find_snapshot(snaps, y, mo, d):
    """回溯查找：不晚于目标日期、且最接近的实际记录。"""
    best = None
    for s in snaps:
        if (s["year"], s["month"], s["day"]) <= (y, mo, d):
            best = s
        else:
            break  # snaps 按时间有序，后续都更大
    return best


def resolve(key, snaps):
    """解析并查值 -> 替换文本 含单位；失败返回 None。"""
    parsed = parse_slot(key)
    if parsed is None:
        return None
    (y, mo, d), place, elem = parsed
    meta = ELEMENTS[elem]
    snap = _find_snapshot(snaps, y, mo, d)
    if snap is None:
        return None
    if meta["province"]:
        v = (snap.get("provinces") or {}).get(place, {}).get(meta["field"])
    else:
        if place != "国家":
            return None
        v = snap.get(meta["field"])
    if v is None:
        return None
    num = meta["fmt"] % v
    return num + meta["unit"] if meta["unit"] else num


def fill_slots(text, snaps):
    """替换文本中所有 {{时间，地点，数据类}} 空位；无法解析的保留原文。"""
    def rep(m):
        v = resolve(m.group(1), snaps)
        return v if v is not None else "{{%s}}" % m.group(1)
    return SLOT_RE.sub(rep, text)


def has_unresolved(text):
    """检查是否仍有未解析空位。"""
    return bool(SLOT_RE.search(text))
