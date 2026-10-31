"""文风闸：大臣发言用现代普通话（不是文言文、不是半文半白的白话），书描述也不许写成文言腔。"""

import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# AI 扮演大臣发言的提示词（集议只有廷议一条路，召对已删除）
SPEAKING = ["Prompt/conference_2.txt"]

# 一眼就是文言腔的词（用于粗查书描述与发言样式示例）
CLASSICAL = ["臣以为", "亦是", "固当", "然则", "不啻", "伏惟", "十不存五", "未敢",
             "者，", "矣。", "焉。", "之谓"]


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_speaking_prompts_are_modern():
    for path in SPEAKING:
        s = io.open(path, encoding="utf-8").read()
        ok("现代普通话" in s, "%s 要写明发言用现代普通话" % path)
        ok("不是文言文" in s, "%s 要标明不是文言文" % path)
        ok("半文半白" not in s.split("<rules1>")[0] or "不是半文半白" in s,
           "%s 的文风要求里不该再出现「半文半白」" % path)
        # 样式示例也不该是文言
        for line in s.splitlines():
            if line.strip().startswith('{"name"'):
                bad = [w for w in CLASSICAL if w in line]
                ok(not bad, "%s 的示例仍是文言腔 %s：%s" % (path, bad, line.strip()[:40]))
    print("发言文风 ✓ 两份发言提示词都写明现代普通话、非文言非白话，示例也改过")


def test_book_descriptions_are_modern():
    with open("world/wenhua.json", encoding="utf-8") as f:
        books = json.load(f)["books"]
    bad = []
    for b in books:
        for w in CLASSICAL:
            if w in b["desc"]:
                bad.append((b["tech_name"], w))
    ok(not bad, "书描述里还有文言腔：%s" % bad[:6])
    ok(all(len(b["desc"]) >= 20 for b in books), "书描述不得短到没有内容")
    ok(all("《" in b["desc"] and "》" in b["desc"] for b in books), "书描述里要写到书名")
    # 抽查样板
    qm = next(b for b in books if b["tech_name"] == "量子力学")
    ok("周世勋" in qm["desc"] and "九章" in qm["desc"], "量子力学那条要照给定的写法")
    print("书描述 ✓ %d 部都是现代普通话，无文言腔" % len(books))


if __name__ == "__main__":
    test_speaking_prompts_are_modern()
    test_book_descriptions_are_modern()
    print("── 文风测试全部通过")
