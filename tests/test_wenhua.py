"""文华殿藏书表测试：一科技一书，得书只看科技是否解锁。"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_wenhua.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_wenhua_pos.json"))
from engine.session import GameSession
from engine import wenhua, tech


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_table():
    with open("world/wenhua.json", encoding="utf-8") as f:
        books = json.load(f).get("books", [])
    techs = tech.all_techs()
    ok(len(books) == len(techs), "藏书 %d 部，科技 %d 条，应一一对应" % (len(books), len(techs)))
    ids = [b["id"] for b in books]
    ok(len(ids) == len(set(ids)), "书 id 不得重复")
    by_name = {t["name"]: t for t in techs}
    for b in books:
        ok(b["from_tech"] in {t["id"] for t in techs}, "%s 挂在不存在的前置科技上" % b["id"])
        ok(b["name"].startswith("《") and b["name"].endswith("》"), "%s 的书名要加书名号" % b["id"])
        ok(len(b["desc"]) >= 15, "%s 的书描述太短" % b["id"])
        ok(b["tech_name"] in by_name, "%s 的科技名不存在" % b["id"])
    dm = wenhua.book_of_tech(by_name["量子力学"]["id"])
    ok(dm["name"] == "《量子力学教程》", "量子力学那本书应是《量子力学教程》，实际 %s" % dm["name"])
    ok("周世勋" in dm["desc"], "量子力学书描述要写到作者")
    print("藏书表 ✓ %d 部，一书一科技，书名与书描述齐备" % len(books))


def test_owned_follows_unlock():
    s = GameSession()
    v = wenhua.wenhua_view(s.state)
    ok(v["total"] == len(tech.all_techs()) and v["owned"] == 0, "开局一部书都没得")
    x = next(b for b in v["books"] if b["tech_name"] == "高等数学")
    ok(not x["owned"] and x["desc"], "未得的书同样带描述（前端本来就只列已有的书）")

    root = next(t for t in tech.all_techs() if t["name"] == "初等数学")
    tech.unlock(s.state, root["id"])          # 根节点可直接解锁
    v2 = wenhua.wenhua_view(s.state)
    ok(v2["owned"] == 1, "解锁一条科技就该得一部书，实际 %d" % v2["owned"])
    y = next(b for b in v2["books"] if b["tech_name"] == "初等数学")
    ok(y["owned"] and y["desc"], "已得的书要有描述")
    ok([b["name"] for b in wenhua.owned_books(s.state)] == [y["name"]], "已得清单要一致")
    ok(not next(b for b in v2["books"] if b["tech_name"] == "高等数学")["owned"],
       "前置未完成的科技不得书")
    print("得书 ✓ 开局无书，解锁一条科技即得一部")


if __name__ == "__main__":
    test_table()
    test_owned_follows_unlock()
    print("── 文华殿藏书测试全部通过")
