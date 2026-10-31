# -*- coding: utf-8 -*-
"""合并 hum/*.txt（玩家手写的角色介绍）到 data/character_cards.json。

格式（每文件）：
  可选首行 = 角色姓名（无则从"姓名："行取）
  基本信息：姓名：X 字Y / 阵营：X / 生年：X / 籍贯：X / 官职：X
  性格：每两条非空行为一组（标题 / 述）
  生平：每两条非空行为一组（年 / 事）

规则：
- basic 的 姓名/字/阵营/生年/籍贯/官职 用 hum 值；原有「号」与 state 保留
- 性格 / 生平 整体替换为 hum 内容
- 未匹配到角色的 hum 文件会报告（不写入）
"""

import json
import os
import re
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUM_DIR = os.path.join(_BASE, "hum")
SRC = os.path.join(_BASE, "data", "character_cards.json")

BASIC_KEYS = ("姓名", "字", "阵营", "生年", "籍贯", "官职")
SECTIONS = ("基本信息", "性格", "生平")

# hum 文件名与角色卡键名的别名映射（如 hum 写"张嫣"，卡键是"懿安皇后张嫣"）
NAME_ALIAS = {"张嫣": "懿安皇后张嫣"}


def parse_hum(path):
    """解析 hum 文件，返回 [(姓名, basic, 性格, 生平), ...]（一个文件可含多张卡）。

    卡片边界：'X角色卡' 标题行、'基本信息' 表头；首个'基本信息'之前的
    前导行（如首行姓名）并入第一块；无表头的整文件视为一张卡。
    """
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip() for l in f if l.strip()]
    blocks = []
    cur = []
    first_header_done = False
    for l in lines:
        if l == "基本信息":
            if not first_header_done:
                first_header_done = True
                cur.append(l)
            elif cur:
                blocks.append(cur)
                cur = [l]
            else:
                cur = [l]
        elif re.match(r"^.+角色卡\s*$", l):
            if cur:
                blocks.append(cur)
            cur = [l]
        else:
            cur.append(l)
    if cur:
        blocks.append(cur)
    return [_parse_block(b) for b in blocks]


def _parse_block(lines):
    name = None
    basic = {}
    xingge = []
    shengping = []
    sec = "基本信息"
    i = 0
    while i < len(lines):
        l = lines[i]
        if l in SECTIONS:
            sec = l
            i += 1
            continue
        if sec == "基本信息":
            m = re.match(r"^姓名[：:]\s*(.*)$", l)
            if m:
                val = m.group(1).strip()
                mm = re.match(r"^(.+?)\s*字\s*(\S+)$", val)
                if mm:
                    basic["姓名"] = mm.group(1).strip()
                    basic["字"] = mm.group(2).strip()
                else:
                    basic["姓名"] = val
                    basic["字"] = "不详"
                if name is None:
                    name = basic["姓名"]
            else:
                m = re.match(r"^([^：:]+)[：:]\s*(.*)$", l)
                if m:
                    basic[m.group(1).strip()] = m.group(2).strip()
            i += 1
        else:
            t = lines[i]
            c = lines[i + 1] if i + 1 < len(lines) else ""
            if sec == "性格":
                xingge.append({"标题": t, "述": c})
            else:
                shengping.append({"年": t, "事": c})
            i += 2
    return name, basic, xingge, shengping


def main():
    with open(SRC, encoding="utf-8") as f:
        cards = json.load(f)

    matched = []
    missed = []
    files = sorted(os.listdir(HUM_DIR))
    for fn in files:
        if not fn.endswith(".txt"):
            continue
        path = os.path.join(HUM_DIR, fn)
        for name, basic, xingge, shengping in parse_hum(path):
            key = NAME_ALIAS.get(name, name)
            if not name or key not in cards:
                missed.append((fn, name))
                continue
            c = cards[key]
            b = c.setdefault("basic", {})
            for k in BASIC_KEYS:
                if k in basic:
                    b[k] = basic[k]
            c["性格"] = xingge
            c["生平"] = shengping
            matched.append((fn, name, len(xingge), len(shengping)))

    with open(SRC, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)

    print("已合并 %d 个角色:" % len(matched))
    for fn, n, xg, sp in matched:
        print("  %-10s %-8s 性格%d 生平%d" % (fn, n, xg, sp))
    if missed:
        print("\n未匹配（未写入）:")
        for fn, n in missed:
            print("  %s: %s" % (fn, n))
    print("\n角色卡总数:", len(cards))


if __name__ == "__main__":
    main()
