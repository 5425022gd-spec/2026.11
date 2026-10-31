"""角色卡系统：可反复写入、保存、实时修改的角色数据。

存储：data/cards/<id>.json 每角色一个文件，可手改、可程序写。
结构：
  id / name / zi 字 / faction 阵营 / birth 生年 / hometown 籍贯 /
  office 历任官职 / current_office 当前官职，实时可变 /
  location 定位，实时可变 / status active/…，实时可变 /
  personality 性格 / bio 简介，供词符匹配 / timeline 生平时间线 /
  memory NPC 记忆，对话总结实时追加 / vars 扩展变量，实时修改

API：
  load_card(cid) / save_card(card) / list_cards() / update_card(cid, **fields)
"""

import json
import os

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_DIR = os.path.join(_BASE, "data", "cards")


def _ensure_dir():
    os.makedirs(CARDS_DIR, exist_ok=True)


def card_path(cid):
    return os.path.join(CARDS_DIR, "%s.json" % cid)


_RUNTIME_CARDS_MAP = {}

def save_card(card):
    """写入运行时内存 不持久化写入磁盘文件。"""
    if not card.get("id"):
        return False
    _RUNTIME_CARDS_MAP[card["id"]] = card
    return True


def load_card(cid):
    """读取一张角色卡 优先内存，次读初始文件。"""
    if cid in _RUNTIME_CARDS_MAP:
        return _RUNTIME_CARDS_MAP[cid]
    try:
        with open(card_path(cid), encoding="utf-8") as f:
            c = json.load(f)
            _RUNTIME_CARDS_MAP[cid] = c
            return c
    except (OSError, ValueError):
        return None


def list_cards():
    """全部角色卡 含实时字段的快照。"""
    _ensure_dir()
    out = []
    for fn in sorted(os.listdir(CARDS_DIR)):
        if fn.endswith(".json"):
            c = load_card(fn[:-5])
            if c:
                out.append(c)
    return out


def update_card(cid, **fields):
    """实时修改角色卡字段 如官职变动/定位/状态/记忆追加。

    例：update_card("yuanchonghuan", current_office=" 辞官",
                    location="广东东莞", status="retired")
    memory 字段传列表会整体覆盖；用 append_memory(追加。)
    """
    card = load_card(cid)
    if card is None:
        return None
    for k, v in fields.items():
        card[k] = v
    save_card(card)
    return card


def append_memory(cid, entry):
    """追加 NPC 记忆条目 对话总结等，实时保存。"""
    card = load_card(cid)
    if card is None:
        return None
    card.setdefault("memory", []).append(entry)
    save_card(card)
    return card


def get_memories(cid):
    """拉取 NPC 记忆 防记忆丢失。"""
    card = load_card(cid)
    return (card or {}).get("memory", [])
