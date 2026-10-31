"""角色卡库：1627 年 天启七年中央/边境/各方势力角色档案。

卡片结构 与毛文龙范卡一致，另附可变状态：
  basic    基本信息：姓名 / 字 / 号 / 阵营 / 生年 / 籍贯 / 官职
  性格     性格条目列表：[{"标题", "述"}]
  生平     编年列表：[{"年", "事"}]
  state    可变状态 可实时修改并反复保存：现职 / 驻地 / 状态 / 亲信 / 备注

读写接口：
  load_cards()            全量读取
  get_card(name)          读取单卡
  list_cards()            名录 含阵营/党派/官职/现职/驻地/状态
  save_card(name, card)   整卡写入 覆盖
  patch_state(name, patch) 只改 state 字段 增量、实时修改用
  add_year(name, item)    生平追加一条 重复写入支持
"""
import json
import os
import threading

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_BASE, "data", "character_cards.json")
_LOCK = threading.Lock()


def _read():
    with open(_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write(cards):
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _PATH)


# 内存中运行时缓存 不修改本地基础 json 源码文件，进程重启自动恢复初始状态
_RUNTIME_CARDS = None

def _get_cards_dict():
    global _RUNTIME_CARDS
    if _RUNTIME_CARDS is None:
        _RUNTIME_CARDS = _read()
    return _RUNTIME_CARDS


def load_cards():
    with _LOCK:
        return dict(_get_cards_dict())


def get_card(name):
    if not isinstance(name, str):
        if isinstance(name, dict):
            name = name.get("name") or ""
        else:
            name = str(name or "")
    if not name:
        return None
    with _LOCK:
        return _get_cards_dict().get(name)


# ── 人物卡标识词表 一处定义──
# 卡片 state.状态 是史实叙事文字 如「在任 权倾朝野」。
# 机器识别一律走 status 属性：前端只比较属性值，不再用中文正则猜状态。
STATUS_VALUES =  "在职", "赋闲", "居乡", "死亡", "其他"
_STATUS_KEYWORDS = (
    ("死亡",  ("死", "卒", "殁", "薨")),
    ("在职",  ("在任", "在职")),
    ("居乡",  ("致仕", "家居", "削籍", "闲居")),
    ("赋闲",  ("读书待举", "赋闲")),
)


def status_of(raw: str) -> str:
    """由卡片叙事文字定状态属性 唯一一处。"""
    s = (raw or "").strip()
    if not s:
        return "其他"
    for val, kws in _STATUS_KEYWORDS:
        if any(k in s for k in kws):
            return val
    return "其他"


def card_view(name):
    """人物卡统一读接口：原卡 + 归一属性 状态 / 党派 / 位置。

    前端名录与人物详情一律读这里的属性，不再自行用中文正则或关键词判断。
    """
    from .factions import normalize_faction
    from .officials import canonical_location, has_active_office, current_office
    c = get_card(name)
    if not c:
        return None
    out = dict(c)
    out["basic"] = dict(c.get("basic") or {})
    office = current_office(name)          # 官职只认职位库绑定，没有就是空，不兜底
    out["basic"]["官职"] = office
    out["官职"] = office
    st = c.get("state") or {}
    # 动态提取的官职只要还在任，状态就锁死在【在职】
    out["status"] = "在职" if has_active_office(name) else status_of(st.get("状态"))
    out["党派"] = normalize_faction((c.get("basic") or {}).get ("阵营", ""))
    out["location"] = canonical_location(st.get ("驻地"))
    return out


def list_cards():
    from .factions import normalize_faction   # 党派归约：阉党 / 东林党 / 无党派
    from .officials import canonical_location, has_active_office, current_office
    with _LOCK:
        cards = _get_cards_dict()
    out = []
    for name, c in cards.items():
        b = c.get("basic", {})
        st = c.get("state", {})
        out.append({
            "name": name,
            "阵营": b.get("阵营", ""),
            "党派": normalize_faction(b.get("阵营", "")),
            "官职": current_office(name),
            "现职": st.get("现职", ""),
            "驻地": st.get("驻地", ""),
            "location": canonical_location(st.get("驻地", "")),
            "状态": "在职" if has_active_office(name) else st.get("状态", ""),
            "status": "在职" if has_active_office(name) else status_of(st.get("状态", "")),
        })
    return out


def save_card(name, card):
    """整卡写入运行时内存 游戏生命周期内生效，不写死到本地数据文件，重启即重置。"""
    with _LOCK:
        cards = _get_cards_dict()
        cards[name] = card
    return True


def patch_state(name, patch):
    """只更新 state 字段 仅内存生效。"""
    with _LOCK:
        cards = _get_cards_dict()
        if name not in cards:
            raise KeyError(name)
        st = cards[name].setdefault("state", {})
        st.update(patch)
        return dict(st)


def add_year(name, item):
    """生平追加一条 仅内存生效。"""
    with _LOCK:
        cards = _get_cards_dict()
        if name not in cards:
            raise KeyError(name)
        cards[name].setdefault ("生平", [].append(item))
    return True
