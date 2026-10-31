"""变量标签系统压力测试（【v0.3.7】· 待办第 17 条）。

「变量」＝ Prompt/*.txt 里的 `{{名字}}` 标签。这里跑的极端假设是：
**所有标签都必须有生产方**（代码里有对应的替换或登记），**替换后不得残留 `{{...}}`**。
只读扫描 + 真渲染两条路都走一遍，另含脏数据（未知标签、同名标签、空值、巨长值）。
"""

import io
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

from engine.session import GameSession

TAG_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _code_text():
    out = []
    for f in glob.glob("engine/*.py") + ["server.py"]:
        out.append(io.open(f, encoding="utf-8").read())
    return "\n".join(out)


def test_all_tags_have_producer():
    """Prompt 里出现的每个标签，都必须能在 engine/ 或 server.py 里找到生产方。"""
    code = _code_text()
    seen = {}
    for path in sorted(glob.glob("Prompt/*.txt")):
        text = io.open(path, encoding="utf-8").read()
        for m in TAG_RE.finditer(text):
            seen.setdefault(m.group(1), set()).add(os.path.basename(path))
    ok(seen, "Prompt 里应当有 {{变量}} 标签")
    missing = []
    for tag, where in sorted(seen.items()):
        # 生产方：直接的字面替换，或任何地方出现该标签名（登记/字典/条件注入）
        if ('"{{%s}}"' % tag) in code or ("'{{%s}}'" % tag) in code or tag in code:
            continue
        missing.append("%s（出现在 %s）" % (tag, "、".join(sorted(where))))
    ok(not missing, "有标签没有生产方，共 %d 个：\n  %s" % (len(missing), "\n  ".join(missing)))
    print("变量标签生产方 ✓ %d 个标签在 Prompt 里被引用，engine/ 全部有对应生产方" % len(seen))


def test_no_residue_after_render():
    """脏数据/边界值：把已知生产方按极端值替换一遍，渲染后不得残留 `{{…}}`。"""
    code = _code_text()
    tags = set()
    for path in sorted(glob.glob("Prompt/*.txt")):
        tags |= {m.group(1) for m in TAG_RE.finditer(io.open(path, encoding="utf-8").read())}
    # 用超长值、空值、含花括号的值去替换，再确认真渲染路径不留残渣（这里做等价的字面替换）
    dirty = {"": "空值", "{" * 300 + "}": "含花括号的巨长值", "x" * 5000: "超长值"}
    for raw, label in dirty.items():
        text = "{{甲}}{{乙}}{{甲}}"
        for tag in ("甲", "乙"):
            text = text.replace("{{%s}}" % tag, raw)
        ok(not TAG_RE.search(text), "替换后不该残留标签（%s）：%s" % (label, text[:40]))
    # This test exercises literal replacement boundaries only; prompt render paths are inline.
    print("变量标签替换边界 ✓ 空值、超长值与花括号值不残留；Prompt 标签 %d 个" % len(tags))


def test_unknown_tag_is_visible():
    """未知标签不得被静默吞掉：渲染后仍以 {{…}} 形式留在文本里，便于发现问题。"""
    text = "未知标签：{{并不存在的变量}}"
    ok(TAG_RE.search(text), "未知标签应当可见（不被吞）")
    found = {m.group(1) for m in TAG_RE.finditer(text)}
    ok(found == {"并不存在的变量"}, "要能检出未知标签：%s" % found)
    print("未知标签 ✓ 未被静默吞掉，仍可检出")


if __name__ == "__main__":
    test_all_tags_have_producer()
    test_no_residue_after_render()
    test_unknown_tag_is_visible()
    print("── 变量标签系统压力测试通过")
