# -*- coding: utf-8 -*-
"""文华殿藏书：科技解锁后刷新出的书 文华殿自己一套物品表，见 world/wenhua.json。

书与技术的关系：一条科技对应一部书 书里写的是这本书本身的内容，不是机制说明。
是否已得只看该科技是否解锁 —— 不另立一份"已得书"的账，免得两处对不上。
"""

import json
import os

from . import tech

WENHUA_FILE = os.path.join("world", "wenhua.json")

_CACHE = None


def load_books():
    """读取藏书表 只读缓存。"""
    global _CACHE
    if _CACHE is None:
        try:
            with open(WENHUA_FILE, "r", encoding="utf-8") as f:
                _CACHE = json.load(f).get("books", [])
        except Exception:
            _CACHE = []
    return _CACHE


def book_of_tech(tech_id):
    """某条科技对应的那部书。"""
    for b in load_books():
        if b.get("from_tech") == tech_id:
            return b
    return None


def is_owned(state, book):
    return tech.is_unlocked(state, book.get("from_tech"))


def owned_books(state):
    """已得的书 即已解锁科技的书。"""
    return [b for b in load_books() if is_owned(state, b)]


def wenhua_view(state):
    """文华殿界面要的数据：全目、已得几部、每部的书名与书描述。"""
    books = []
    for b in load_books():
        owned = is_owned(state, b)
        books.append({
            "id": b.get("id", ""),
            "name": b.get("name", ""),
            "desc": b.get("desc", ""),
            "tech_name": b.get("tech_name", ""),
            "line": b.get("line", ""),
            "owned": owned,
        })
    owned_n = sum(1 for b in books if b["owned"])
    return {"total": len(books), "owned": owned_n, "books": books}
