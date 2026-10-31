# -*- coding: utf-8 -*-
"""超控指令表回归测试。

OVERRIDE_COMMANDS 是唯一真相源：AI 提示词里的清单、设置页两张表都从它生成。
所以这张表自己必须先站得住：

  ① 每条都有 impl，且 impl 真的存在于 operations 模块里；
  ② 每条 example 都是能直接抄去用的——全域条目走 execute_operation_command，
     国家 AI 专用条目走 execute_ai_operations，都不许报「无法识别的指令」；
  ③ {{operation}} 只含全域指令，国家 AI 专用指令（ai=True）不许混进去；
  ④ {{operation}} 里带上修正词条表——不然提示词一边说「不在词条表里就判错」，
     一边又不给表。

① ② 是「帮助文本写了、派发器不认」这类偏差的唯一防线，改表或改派发器都要跑它。

说明：部门/法律/纪律/税目这些登记表是模块级的，超控指令会就地增删，
所以每条用例之前把它们重置回初始快照，否则前一条建的东西会让后一条失真。
"""
import os, tempfile
import copy
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

# 本测试真会执行「改名／裁撤／调任」这类指令，必须打在临时的人名录上，
# 否则会把玩家的 db/officials.db 改脏。必须在 import engine.* 之前设好。
_TMP_DB = os.path.join(tempfile.gettempdir(), "dsh_ops_schema_test_officials.db")
for _suffix in ("", "-journal", "-wal", "-shm"):
    try:
        os.remove(_TMP_DB + _suffix)
    except OSError:
        pass
os.environ["OFFICIALS_DB_PATH"] = _TMP_DB

from engine.session import GameSession
import engine.operations as O
import engine.depts_hubu as hb
import engine.depts_libu as lb
import engine.depts_neiting as nt
import engine.depts_waizhusi as wz
import engine.depts_xingbu as xb

MODS = [(wz, "DEPTS"), (nt, "DEPTS"), (lb, "ADMIN_DISCIPLINE_BUFFS"),
        (xb, "ACTIVE_LAWS"), (hb, "TAX_DEFINITIONS")]
SNAP = {}
for _m, _a in MODS:
    _o = getattr(_m, _a)
    SNAP[(id(_m), _a)] = (type(_o), copy.deepcopy(list(_o) if isinstance(_o, list) else dict(_o)))


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def restore():
    for m, a in MODS:
        kind, data = SNAP[(id(m), a)]
        obj = getattr(m, a)
        if kind is list:
            obj[:] = copy.deepcopy(data)
        else:
            obj.clear()
            obj.update(copy.deepcopy(data))


def _fresh():
    """每条用例前把人社侧也复位：示例里本来就有「裁人／改人名」的指令，
    不重置的话后一条会找不到人（那是用例互相污染，不是表错了）。"""
    restore()
    from engine import officials as _off
    _off.init_db(force=True)          # 打在本测试的临时库上，见文件开头
    return GameSession().state


def test_impl_exists():
    """impl 可以写同模块的函数名，也可以写「模块.函数」（跨模块执行的那种）。"""
    import importlib
    missing = []
    for r in O.OVERRIDE_COMMANDS:
        name = r.get("impl") or ""
        mod, _, attr = name.rpartition(".")
        try:
            target = importlib.import_module("engine." + mod) if mod else O
            found = hasattr(target, attr)
        except Exception:
            found = False
        if not found:
            missing.append((r["group"], r["cmd"], name))
    ok(not missing, "表里的 impl 必须真的存在，缺这些：%s" % missing)
    print("超控表 impl ✓ %d 条指令的执行函数都在" % len(O.OVERRIDE_COMMANDS))


def test_examples_run():
    """表里的示例必须能直接抄：全域走全域派发器，AI 专用走 AI 派发器。"""
    bad = []
    for r in O.OVERRIDE_COMMANDS:
        ex = r.get("example") or ""
        ok(ex, "每条指令都要有可抄的示例：%s" % r["cmd"])
        try:
            st = _fresh()
            if r.get("ai"):
                out = O.execute_ai_operations(st, [ex], faction="ming")
                ok(isinstance(out, list) and len(out) == 1,
                   "AI 派发器要一条指令返回一条结果：%s → %r" % (ex, out))
                if "无法识别" in out[0]:
                    bad.append((r["group"], ex, out[0]))
            else:
                done, msg = O.execute_operation_command(st, ex)
                if not done:
                    bad.append((r["group"], ex, msg))
        except Exception as exc:
            bad.append((r["group"], ex, "%s: %s" % (type(exc).__name__, exc)))
    ok(not bad, "这些示例照抄就失败（表与派发器不一致）：\n  " +
       "\n  ".join("%s ｜ %s → %s" % b for b in bad))
    print("超控示例 ✓ %d 条示例逐条实测，全部被派发器接受" % len(O.OVERRIDE_COMMANDS))


def test_prompt_split():
    """全域清单只放全域指令；国家 AI 专用只放国家 AI 清单。"""
    import re
    all_doc = O.get_all_operations_schema()
    ai_doc = O.get_ai_operations_schema()

    def listed(doc, key):
        # 只认「- 指令名 <参数>」这种条目行；正文里顺口提到「贸易」不算
        return bool(re.search(r"(?m)^\s*-\s*" + re.escape(key) + r"[\s<]", doc))

    leaked, missing = [], []
    for r in O.OVERRIDE_COMMANDS:
        if not r.get("ai"):
            continue
        key = r["cmd"].split(" <")[0]
        if listed(all_doc, key):
            leaked.append(key)
        if not listed(ai_doc, key):
            missing.append(key)
    ok(not leaked, "{{operation}} 里混进了国家 AI 专用指令（全域派发器不认）：%s" % leaked)
    ok(not missing, "{{operation_ai}} 缺这些国家 AI 专用指令：%s" % missing)
    ok("【国家 AI 专用超控指令清单】" in ai_doc, "AI 清单要有标题")
    print("超控清单 ✓ {{operation}} 只含全域指令，%d 条 AI 专用指令只在 {{operation_ai}}" %
          sum(1 for r in O.OVERRIDE_COMMANDS if r.get("ai")))


def test_term_table_injected():
    """{{operation}} 里必须真的带词条表，提示词才有资格说「不在表里就判错」。"""
    doc = O.get_all_operations_schema()
    from engine.modifier_terms import MODIFIER_TERMS
    ok(MODIFIER_TERMS, "MODIFIER_TERMS 不该为空")
    miss = [t["term"] for t in MODIFIER_TERMS if ("- %s：" % t["term"]) not in doc]
    ok(not miss, "{{operation}} 缺这些词条：%s" % miss[:8])
    print("超控词条表 ✓ {{operation}} 里带上了 %d 条修正词条" % len(MODIFIER_TERMS))


def test_strict_grammar():
    """写法错了必须报错，不许有兼容写法把错的悄悄改成对的。"""
    st = _fresh()
    cases = [
        ("删除部门 内廷 惜薪司", "删除部门只收一个参数"),
        ("增加部门 外朝 军器总督司", "分类只认 外廷／内廷"),
        ("增加部门 内廷", "缺部门名称"),
        ("删除部门", "缺部门名称"),
    ]
    bad = []
    for cmd, why in cases:
        try:
            done, msg = O.execute_operation_command(st, cmd)
        except Exception as exc:
            bad.append((cmd, "%s: %s" % (type(exc).__name__, exc)))
            continue
        if done:
            bad.append((cmd, "本该报错却成功了"))
    ok(not bad, "这些写错的指令必须报错：%s" % "\n  ".join("%s（%s）" % b for b in bad))
    # 正写必须通过
    done, msg = O.execute_operation_command(st, "增加部门 内廷 铸钱局")
    ok(done, "增加部门 内廷 铸钱局 应成功：%s" % msg)
    ok("内廷" in msg, "写了内廷就该落在内廷，实际：%s" % msg)
    print("超控写法纪律 ✓ 无兼容写法，%d 种写错一律报错" % len(cases))


if __name__ == "__main__":
    test_impl_exists()
    test_examples_run()
    test_strict_grammar()
    test_prompt_split()
    test_term_table_injected()
    print("── 超控指令表回归通过")
