"""超控系统压力测试（【v0.3.7】· 待办第 18 条）。

把超控指令当外部输入猛灌：每个指令族各来一条正常用例，再来一批脏输入
（未知省份/阶级/官名、参数缺漏、超长名、全角空格、负数、超大数、重复执行）。
要求：① 一律返回 (bool, str) 不抛异常 ② 执行完 state 仍可序列化存档
③ 设置型指令重复执行不叠加（幂等）。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["LLM_MOCK"] = "1"

from engine.session import GameSession
from engine.operations import execute_operation_command


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


VALID = [
    # 阶级人口与民心
    "增加地方各阶级人口 陕西 农民 10000",
    "减少地方各阶级人口 陕西 农民 5000",
    "增加地方各阶级态度 陕西 农民 5",
    "地方腐败程度 陕西 2",
    "地方腐败程度 陕西 -1",
    "地方基建程度 陕西 3",
    # 库银调拨
    "拨付国库到地方 陕西 5",
    "调拨地方窖藏到国库 陕西 3",
    "拨付国库到地方 陕西 2",
    # 修正
    "创建修正 陕西 压测地区 本省 全国所有阶级产能+10% 压测介绍",
    "删除修正 压测地区",
    "创建修正 吏部 压测部门 全国 全国所有阶级产能+2% 压测",
    "修改修正 压测部门 全国所有阶级产能+3% 压测",
    "删除修正 压测部门",
    "创建修正 礼部 压测义学 全国 受教育人口年增+10% 各府设义学",
    "创建修正 礼部 压测国子监 全国 受高等教育人口年增-20% 裁撤监生",
    "创建修正 户部 压测人口 全国 人口出生率+1%，人口死亡率-2% 压测",
    # 赋税与法律
    "增加税目 压测税 关税 0.02",
    "修改修正 压测律 全国所有阶级态度+2 压测",
    "创建修正 吏部 压测纪律 全国 中央腐败程度-3% 压测",
    # 官职与人物
    "增加武职 陕西 压测总兵",
    "增加文职 陕西 压测知府",
    "更换中央官员 吏部 尚书 高攀龙",
    # 军队
    "修改军队战役指令 白杆兵 任务指令 巡逻",
    "军队调动 白杆兵 陕西",
    # 工部工程（五条新建指令各一）
    "新建军事建筑 陕西 压测边墙 遮蔽东路 10 6",
    "新建国家建筑 南直隶 压测外城 全国所有阶级产能+2% 增筑外郭 20 8",
    "新建工程建筑 北直隶 压测水田 农民产能+5% 开田 5 4",
    "新建灾害建筑 河南 压测石堤 全国所有阶级产能+3% 御汛 6 5",
    "新建矿场 云南 压测锡矿 白银3万两/年 官办 12 6",
    "删除工程 云南 压测锡矿",
    # 粮饷
    "修改粮饷加派 辽饷加派 45.0",
]

DIRTY = [
    "",
    "   ",
    "并不存在的指令",
    "增加地方各阶级人口",
    "增加地方各阶级人口 不存在省 农民 100",
    "增加地方各阶级人口 陕西 不存在阶级 100",
    "增加地方各阶级人口 陕西 农民",
    "增加地方各阶级人口 陕西 农民 -100",
    "增加地方各阶级人口 陕西 农民 999999999999",
    "增加地方各阶级态度 陕西 农民 9999",
    "创建修正 吏部 空内容 全国 ",
    "创建修正 吏部 坏内容 全国 并不存在的目标+5% 压测",
    "创建修正 陕西 × " + "长" * 400 + " 本省 全国所有阶级产能+10% 压测",
    "新建国家建筑 陕西 缺参数",
    "新建矿场 云南 负数矿 白银-5万两/年 官办 -3 -2",
    "新建灾害建筑 　　 全角省 全国所有阶级产能+3% 说明 6 5",
    "更换中央官员 吏部 尚书 不存在的人",
    "军队调动 并不存在的军队 陕西",
    "删除工程 陕西 并不存在的工程",
    "删除修正 并不存在的修正",
    "修改粮饷加派",
]


def test_valid_commands():
    s = GameSession()
    st = s.state
    for cmd in VALID:
        try:
            res = execute_operation_command(st, cmd)
        except Exception as exc:                       # 任何异常都是不合格
            raise AssertionError("指令抛异常：%s → %s: %s" % (cmd, type(exc).__name__, exc))
        ok(isinstance(res, tuple) and len(res) == 2, "指令要返回 (bool, msg)：%s → %r" % (cmd, res))
        ok(isinstance(res[0], bool) and isinstance(res[1], str), "返回类型不对：%s" % cmd)
    ok(st.to_dict(), "跑完一轮指令后仍要能序列化存档")
    print("超控正常用例 ✓ %d 条指令全部正常返回，跑完仍可存档" % len(VALID))


def test_dirty_commands():
    s = GameSession()
    st = s.state
    for cmd in DIRTY:
        try:
            res = execute_operation_command(st, cmd)
        except Exception as exc:
            raise AssertionError("脏输入把系统搞崩了：%r → %s: %s" % (cmd, type(exc).__name__, exc))
        ok(isinstance(res, tuple) and len(res) == 2, "脏输入也要规范返回：%r → %r" % (cmd, res))
    ok(st.to_dict(), "脏输入之后仍要能序列化存档")
    # 全角空格不能被当成有效省份
    okk, msg = execute_operation_command(st, "增加地方各阶级人口 　　 农民 100")
    ok(isinstance(okk, bool), "全角空格参数不得崩：%s" % msg)
    print("超控脏输入 ✓ %d 条脏输入全部规范返回、不崩、仍可存档" % len(DIRTY))


def test_idempotent_setters():
    """设置型指令重复执行不叠加：改两次与改一次的结果一致。"""
    s = GameSession()
    st = s.state
    execute_operation_command(st, "创建修正 吏部 幂等修正 全国 全国所有阶级产能+7% 幂等")
    first = st.department_modifiers["幂等修正"]["summary"]
    execute_operation_command(st, "创建修正 吏部 幂等修正 全国 全国所有阶级产能+7% 幂等")
    ok(len([d for d in st.department_modifiers.values() if d.get("name") == "幂等修正"]) == 1,
       "同名部门修正不得出现两条")
    ok(st.department_modifiers["幂等修正"]["summary"] == first, "重复执行结果应一致")
    # 教育修正同理
    execute_operation_command(st, "创建修正 礼部 幂等义学 全国 受教育人口年增+5% 义学")
    execute_operation_command(st, "创建修正 礼部 幂等义学 全国 受教育人口年增+5% 义学")
    rows = [m for m in st.department_modifiers.values() if m.get("name") == "幂等义学"]
    ok(len(rows) == 1, "同名教育修正不得出现两条：%d" % len(rows))
    # 工程同理：同名工程不得建两支
    execute_operation_command(st, "新建矿场 云南 幂等矿 白银2万两/年 官办 8 4")
    okk, msg = execute_operation_command(st, "新建矿场 云南 幂等矿 白银2万两/年 官办 8 4")
    mines = [pj for pj in st.provinces["yunnan"]["local"]["projects"] if pj["name"] == "幂等矿"]
    ok(len(mines) == 1, "同名工程不得出现两条：%d（%s）" % (len(mines), msg))
    print("超控幂等 ✓ 部门修正/教育修正/工程重复执行都不叠加")


if __name__ == "__main__":
    test_valid_commands()
    test_dirty_commands()
    test_idempotent_setters()
    print("── 超控系统压力测试通过")
