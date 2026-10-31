"""大明王朝 1627 · 20类正面与负面操作指令系统与账单明细测试。

测试覆盖：
1. 10类正面操作功能与账单交易记录。
2. 10类负面操作功能与账单交易记录。
3. 地方人口精确到个位数（包括各阶级人口与总人口）。
4. session.run() 命令行解析与执行。
5. 国库与私库收支表与账单记录隔离验证（无虚构常例预计支出）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_operations.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_operations_pos.json"))
from engine.session import GameSession
from engine.state import WorldState
from engine.operations import (
    execute_operation,
    transfer_local_reserve_to_treasury,
    transfer_local_reserve_to_privy,
    transfer_local_gold_to_treasury,
    add_local_infra,
    reduce_local_corruption,
    reduce_central_corruption,
    add_class_pop,
    add_class_attitude,
    add_prestige,
    transfer_treasury_to_local_reserve,
    transfer_privy_to_local_reserve,
    transfer_treasury_gold_to_local,
    reduce_local_infra,
    add_local_corruption,
    add_central_corruption,
    reduce_class_pop,
    reduce_class_attitude,
    reduce_prestige,
    OPERATIONS_DOC,
)
from engine.localdata import class_pop_exact, population_exact, population
from engine.depts_hubu import calc_treasury_balance_sheets, calc_hubu_basic_stats


def test_positive_operations():
    s = GameSession()
    st = s.state

    # 1. 增加地方窖藏银两到国库
    old_res = st.local_reserve
    old_tr = st.treasury
    msg = transfer_local_reserve_to_treasury(st, "北直隶", 20.0)
    assert "充入太仓国库" in msg
    assert st.treasury == old_tr + 20.0
    assert st.local_reserve == old_res - 20.0
    assert st.national_ledger[0]["type"] == "收入"
    assert st.national_ledger[0]["item"] == "地方解缴"
    assert st.national_ledger[0]["amount"] == 20.0

    # 2. 增加地方窖藏银两到私库
    old_res = st.local_reserve
    old_privy = st.privy_treasury
    msg = transfer_local_reserve_to_privy(st, "北直隶", 15.0)
    assert "解入皇帝内帑" in msg
    assert st.privy_treasury == old_privy + 15.0
    assert st.local_reserve == old_res - 15.0
    assert st.privy_ledger[0]["type"] == "收入"
    assert st.privy_ledger[0]["item"] == "地方进呈"
    assert st.privy_ledger[0]["amount"] == 15.0

    # 3. 增加地方黄金到国库
    old_gold_res = st.gold_local_reserve
    old_gold_tr = st.gold_treasury
    msg = transfer_local_gold_to_treasury(st, "云南", 2.5)
    assert "充入国库黄金储备" in msg
    assert st.gold_treasury == old_gold_tr + 2.5
    assert st.gold_local_reserve == old_gold_res - 2.5
    assert st.national_ledger[0]["type"] == "收入"
    assert st.national_ledger[0]["item"] == "黄金解库"

    # 4. 增加地方基建程度
    old_infra = st.provinces["henan"]["local"]["infra"]
    msg = add_local_infra(st, "河南", 10)
    assert "基建程度" in msg
    assert st.provinces["henan"]["local"]["infra"] == min(100, old_infra + 10)

    # 5. 降低地方腐败
    old_corr = st.provinces["shaanxi"]["local"]["corruption"]
    msg = reduce_local_corruption(st, "陕西", 5.0)
    assert "腐败程度" in msg
    assert st.provinces["shaanxi"]["local"]["corruption"] == max(0.0, round(old_corr - 5.0, 1))

    # 6. 降低中央腐败程度
    old_c_corr = st.central_corruption
    msg = reduce_central_corruption(st, 5.0)
    assert "中央腐败程度" in msg
    assert st.central_corruption == max(0.0, round(old_c_corr - 5.0, 1))

    # 7. 增加地方各阶级人口（精确到个位数）
    c_data = st.provinces["shaanxi"]["local"]["classes"]["农民"]
    old_exact = class_pop_exact(c_data)
    msg = add_class_pop(st, "陕西", "农民", 1234)
    assert "增加 1,234 人" in msg
    assert class_pop_exact(c_data) == old_exact + 1234

    # 8. 增加地方各阶级态度
    old_att = c_data["attitude"]
    msg = add_class_attitude(st, "陕西", "农民", 10)
    assert "态度+10" in msg
    assert c_data["attitude"] == min(100, old_att + 10)

    # 10. 增加皇室威望
    old_bonus = getattr(st, "prestige_bonus", 0.0)
    msg = add_prestige(st, 5.0)
    assert "皇室威望增加" in msg
    assert st.prestige_bonus == old_bonus + 5.0


def test_negative_operations():
    s = GameSession()
    st = s.state

    # 1. 减少国库银两到地方窖藏
    old_res = st.local_reserve
    old_tr = st.treasury
    msg = transfer_treasury_to_local_reserve(st, "北直隶", 20.0)
    assert "补充" in msg
    assert st.treasury == max(0.0, old_tr - 20.0)
    assert st.local_reserve == old_res + 20.0
    assert st.national_ledger[0]["type"] == "支出"
    assert st.national_ledger[0]["item"] == "拨付地方"

    # 2. 减少私库银两到地方窖藏
    old_res = st.local_reserve
    old_privy = st.privy_treasury
    msg = transfer_privy_to_local_reserve(st, "北直隶", 15.0)
    assert "皇帝内帑拨发" in msg
    assert st.privy_treasury == max(0.0, old_privy - 15.0)
    assert st.local_reserve == old_res + 15.0
    assert st.privy_ledger[0]["type"] == "支出"
    assert st.privy_ledger[0]["item"] == "内帑拨赈"

    # 3. 减少国库黄金到地方
    old_gold_res = st.gold_local_reserve
    old_gold_tr = st.gold_treasury
    msg = transfer_treasury_gold_to_local(st, "云南", 2.0)
    assert "国库黄金储备拨出" in msg
    assert st.gold_treasury == max(0.0, old_gold_tr - 2.0)
    assert st.gold_local_reserve == old_gold_res + 2.0
    assert st.national_ledger[0]["type"] == "支出"
    assert st.national_ledger[0]["item"] == "黄金外拨"

    # 4. 降低地方基建程度
    old_infra = st.provinces["henan"]["local"]["infra"]
    msg = reduce_local_infra(st, "河南", 10)
    assert "基建程度" in msg
    assert st.provinces["henan"]["local"]["infra"] == max(0, old_infra - 10)

    # 5. 增加地方腐败
    old_corr = st.provinces["shaanxi"]["local"]["corruption"]
    msg = add_local_corruption(st, "陕西", 5.0)
    assert "腐败程度" in msg
    assert st.provinces["shaanxi"]["local"]["corruption"] == min(100.0, round(old_corr + 5.0, 1))

    # 6. 增加中央腐败程度
    old_c_corr = st.central_corruption
    msg = add_central_corruption(st, 5.0)
    assert "中央腐败程度" in msg
    assert st.central_corruption == min(100.0, round(old_c_corr + 5.0, 1))

    # 7. 减少地方各阶级人口（精确到个位数）
    c_data = st.provinces["shaanxi"]["local"]["classes"]["农民"]
    old_exact = class_pop_exact(c_data)
    msg = reduce_class_pop(st, "陕西", "农民", 500)
    assert "减少 500 人" in msg
    assert class_pop_exact(c_data) == old_exact - 500

    # 8. 降低地方各阶级态度
    old_att = c_data["attitude"]
    msg = reduce_class_attitude(st, "陕西", "农民", 10)
    assert "态度-10" in msg
    assert c_data["attitude"] == max(0, old_att - 10)

    # 10. 降低皇室威望
    old_pen = getattr(st, "prestige_penalty", 0.0)
    msg = reduce_prestige(st, 5.0)
    assert "皇室威望下降" in msg
    assert st.prestige_penalty == old_pen + 5.0


def test_session_run_operations():
    s = GameSession()

    res = s.run("增加地方窖藏银两到国库 北直隶 30")
    assert any("【正面操作】" in r and "太仓国库" in r for r in res)

    res = s.run("增加地方窖藏银两到私库 北直隶 20")
    assert any("【正面操作】" in r and "皇帝内帑" in r for r in res)

    res = s.run("增加地方黄金到国库 云南 5")
    assert any("【正面操作】" in r and "黄金储备" in r for r in res)

    res = s.run("降低地方腐败 陕西 10")
    assert any("【正面操作】" in r and "腐败程度" in r for r in res)

    res = s.run("降低中央腐败程度 5")
    assert any("【正面操作】" in r and "中央腐败" in r for r in res)

    res = s.run("增加地方各阶级人口 陕西 农民 1000")
    assert any("【正面操作】" in r and "1,000 人" in r for r in res)

    res = s.run("减少国库银两到地方窖藏 北直隶 10")
    assert any("【负面操作】" in r and "太仓国库拨出" in r for r in res)

    res = s.run("减少私库银两到地方窖藏 北直隶 10")
    assert any("【负面操作】" in r and "皇帝内帑拨发" in r for r in res)

    res = s.run("降低地方基建程度 河南 5")
    assert any("【负面操作】" in r and "基建程度" in r for r in res)

    res = s.run("降低皇室威望 5")
    assert any("【负面操作】" in r and "皇室威望下降" in r for r in res)


def test_population_exact_units():
    s = GameSession()
    st = s.state

    for pid, p in st.provinces.items():
        if p.get("owner") == "ming":
            loc = p.get("local", {})
            total_exact = population_exact(loc)
            cls = loc.get("classes", {})
            sum_exact = sum(class_pop_exact(c) for c in cls.values())
            assert total_exact == sum_exact, f"Province {pid} total pop exact mismatch"
            assert total_exact > 0

    stats = calc_hubu_basic_stats(st)
    assert stats["total_actual_pop_exact"] > 0
    assert len(stats["classes_table"]) == 7
    for c in stats["classes_table"]:
        assert c["population_exact"] > 0
        assert isinstance(c["population_exact"], int)


def test_treasury_separation_and_no_fictitious_lists():
    s = GameSession()
    sheets = calc_treasury_balance_sheets(s.state)
    nt = sheets["national_treasury"]
    pt = sheets["privy_treasury"]

    # National Treasury has incomes, transactions, balance
    assert "incomes" in nt
    assert "transactions" in nt
    assert "balance" in nt
    # 支出项＝六部/外诸司/二十四衙门各部门年费（数据在 world/department_expenses.json）
    assert "expenses" in nt and nt["expenses"], "太仓国库支出表要有各部门年费"
    dep_names = {e["name"] for e in nt["expenses"]}
    assert {"吏部", "户部", "礼部", "兵部", "刑部", "工部"} <= dep_names, dep_names
    assert "内阁" in dep_names and "司礼监" in dep_names, "外诸司与二十四衙门也要在内"
    assert nt["total_annual_expense"] > 0
    # 户部只做统计：三类归属的小计之和＝合计
    subs = nt["expense_subtotals"]
    assert set(subs) == {"六部", "外诸司", "二十四衙门"}, subs
    assert abs(sum(subs.values()) - nt["total_annual_expense"]) < 0.05, (subs, nt["total_annual_expense"])
    # 部门年费是部门自己的属性：/api/dept 的载荷里要带
    from engine.depts import build_dept
    from engine.depts_hubu import department_expense_of
    li = department_expense_of("吏部")
    assert build_dept("吏部", s.state)["annual_expense"] == li["annual"], li
    # 宫殿、城门这类设施不是部门，没有年费这个属性（返回 None，界面据此不显示该栏）
    assert department_expense_of("午门") is None, "午门不是部门，不该有年费"
    assert build_dept("午门", s.state)["annual_expense"] is None, "设施页面不该显示年费栏"

    # Privy Treasury has only silver_balance, gold_balance, transactions (ledger)
    assert "silver_balance" in pt
    assert "gold_balance" in pt
    assert "transactions" in pt
    assert "incomes" not in pt
    assert "expenses" not in pt
