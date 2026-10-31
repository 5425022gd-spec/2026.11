"""地方天气、自然灾害、黄汛、地震与瘟疫综合算法 v4。

设计规范：
1. 静态气候：每省月度温度表 全月唯一温度+ 季度降水量表，数据在 world/climate.json。
2. 地区划分：
   - 北方地区 north：陕西、山西、北直隶、山东、河南，及辽东、蒙古、后金。
   - 中部地区 central：四川、湖广、南直隶。
   - 南部地区 south：云南、贵州、广西、广东、福建、江西、浙江、东番。
3. 降水算法：
   - 小降水：每省抽 20~35 天，占季度降水量 30%~40%。
   - 春夏季风大降水：每省从排除小降水日期的剩余日期中抽取 春 4~6 天，夏 6~8 天，
     将大降水总量随机分配到这几天，方差平稳 微扰权重 0.85~1.15，总和精确闭合。
   - 秋冬冷锋大降水：秋 4~6 次冷锋，冬 6~8 次冷锋，自北向南三批推进。
     各省将大降水总量分配到各次冷锋过境日，方差平稳，总和精确闭合。
4. 台风：
   - 仅秋季刷新 1~3 个，落点仅限于：浙江、福建、广东、东番 东番权重×2。
   - 严禁在北直隶、山东、南直隶、广西、辽东刷新。
5. 极端天气与灾害判定：
   - 每个省份每季度独立判定。
   - 所有灾害基础发生概率减 50% 向下取整，小灾害影响减少 50% 向下取整，大灾害发生概率减少 50% 向下取整。
   - 旱涝互斥、寒暑互斥。
6. 灾害与瘟疫死亡人数判定：
   - 每次灾害总死亡人数 = 本省人口 × 0.1 × 产能惩罚 × 灾害种类修正 × 连续修正 × 灾害建筑或其他修正。
   - 连续修正：上季度天气灾害后本季度也发生天气灾害则连续修正+1 默认为1，未受灾季度重置为0。大小瘟疫不计入连续修正累加。
   - 灾害种类修正：小旱 0.01、小涝 0.01、小热浪 0.01、小寒潮 0.05、大旱 0.10、大涝 0.10、大热浪 0.10、大寒潮 0.20、小瘟疫 0.20、大瘟疫 0.40、黄汛 0.20、大黄汛 0.30。
   - 人口死亡均匀分布在灾害持续的每天内 每天扣除 1/持续天数。
7. 瘟疫系统：
   - 大灾难 大旱、大涝、大寒潮、大热浪发生后第 20 天触发瘟疫判定。小灾不触发。
   - 冬季天气灾难触发瘟疫的概率削减 80% ×0.2。
   - 触发时：80% 为小疫 持续本季剩余天数，全阶级产能额外-10%；
     20% 为大疫 持续到下季结束，这两个季度全阶级产能额外-25%。
8. 黄汛判定 河南/山东/南直隶：
   - 条件：夏季或秋季 + 本季度大涝 + 当日为大降水日。
   - 发生概率：基础 70%，按 `disaster_chance`（kind=yellow_flood）的唯一公式算，
     工程减灾只是其中一路修正（灵州黄河石堤 −20% → 56%）。
   - 发生后次日按 `disaster_chance`（kind=yellow_flood_major）判定是否为大黄汛（基础 10%）。
   - 黄汛：首月产能损失 18%（`disaster_penalty`）持续两个季度，每月衰减 3%；
     死亡人数按 `disaster_death` 的唯一公式算，种类修正 0.2。
   - 大黄汛：首月 36%，持续三个季度，每月衰减 4%，种类修正 0.3；
     全国黄河工程按 `damage_degree` 记损毁程度（基础 100%，加固类修正可减），
     生效一律按 (1−损毁程度) 折算——不写死「直接损坏」。
   - 灾害四量（概率 / 减产 / 死亡 / 损毁程度）都是修正 target，没有静态特殊处理。
9. 灾害建筑：数值写成词条，键是灾害种类，本模块只按词条汇总，不写死具体工程：
   - `buff.disaster`：`{"flood_minor": -0.70, "flood_major": -0.70}` 等，
     对应词条「小涝导致的产能损失」「大涝导致的产能损失」……
   - `buff.disaster_chance`：`{"yellow_flood": -0.20}` 对应「黄汛发生概率」。
   - 词条自己声明作用面（`area`）：涝类是本省，黄汛类是**全国**（黄河是一条水系）。
   - 只有**运营中**的工程生效；同类多个工程叠乘；损毁程度按 (1−损毁) 折算。
10. 历史地震脚本判定：
   - 根据公历日期精准触发历史著名大地震，造成人口伤亡与下月产能减产。
"""

import json
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from .logger import CONSOLE

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIMATE_PATH = os.path.join(_BASE, "world", "climate.json")

CONDITIONS = ["晴", "多云", "阴", "雨", "雪"]

# ── 地区划分 ──
REGIONS = {
    "north": ["shaanxi", "shanxi", "beizhili", "shandong", "henan", "liaodong",
              "shengjing", "haixi", "nenkeerqin", "suolun", "donghai", "kuwu",
              "wuliangha", "chahar", "tumote", "ordos"],
    "central": ["sichuan", "huguang", "nanzhili"],
    "south": ["yunnan", "guizhou", "guangxi", "guangdong", "fujian", "jiangxi", "zhejiang", "dongfan"],
}


def get_province_region(pid: str) -> str:
    """获取省份所属大区 north / central / south。"""
    for reg, pids in REGIONS.items():
        if pid in pids:
            return reg
    return "north"


# ── 春夏季风型与秋冬冷锋型天数/次数范围 ──
MONSOON_DAYS = {0: (4, 6), 1: (6, 8)}       # 春 4~6 天 / 夏 6~8 天
COLD_FRONT_COUNTS = {2: (4, 6), 3: (6, 8)}  # 秋 4~6 次 / 冬 6~8 次

# 冷锋路径：自北向南 3 批次
COLD_FRONT_BANDS = [
    ["beizhili", "shanxi", "shaanxi", "shandong", "liaodong"],             # 北批
    ["henan", "nanzhili", "huguang", "jiangxi", "zhejiang"],                # 中批
    ["fujian", "guangdong", "guangxi", "dongfan", "yunnan", "guizhou"],      # 南批
]

# ── 台风允许省份 禁止：北直隶、山东、南直隶、广西、辽东 ──
COASTAL_PROVINCES = {
    "zhejiang": 1,
    "fujian": 1,
    "guangdong": 1,
    "dongfan": 2,  # 东番权重 ×2
}

# 比例上下限
SMALL_DAY_MIN = 0.003    # 小降水单日下限 0.3%
SMALL_DAY_MAX = 0.025    # 小降水单日上限 2.5%
TYPHOON_MIN = 0.10       # 台风单次占落点省秋季总量下限 10%
TYPHOON_MAX = 0.25       # 台风单次占落点省秋季总量上限 25%

# ── 灾害种类修正系数表 用于死亡人数计算 ──
DISASTER_DEATH_COEFFS = {
    "小旱": 0.01,
    "小涝": 0.01,
    "小热浪": 0.01,
    "小寒潮": 0.05,
    "大旱": 0.10,
    "大涝": 0.10,
    "大热浪": 0.10,
    "大寒潮": 0.20,
    "小疫": 0.20,
    "大疫": 0.40,
    "黄汛": 0.20,
    "大黄汛": 0.30,
}

# ── 灾难类型定义 已应用：基础概率-50%向下取整，小灾影响-50%向下取整，大灾概率-50%向下取整 ──
# 结构：灾害主类, 触发概率, [ 小灾名, 小灾占比, 小灾减产, 小灾温偏, 小灾降水比, 小灾强度,  大灾名, 大灾占比, 大灾减产, 大灾温偏, 大灾降水比, 大灾强度])
DISASTER_TABLE = {
    "north": {
        0: [  # 春
            ("旱", 0.15, [("小旱", 0.95, 0.05, 1.5, 0.75, 40), ("大旱", 0.05, 0.30, 3.5, 0.45, 75)]),
            ("寒潮", 0.07, [("小寒潮", 0.90, 0.02, -3.0, 1.0, 40), ("大寒潮", 0.10, 0.15, -6.5, 1.0, 75)]),
            ("热浪", 0.02, [("小热浪", 0.95, 0.02, 3.0, 1.0, 40), ("大热浪", 0.05, 0.10, 5.5, 1.0, 75)]),
        ],
        1: [  # 夏
            ("旱", 0.17, [("小旱", 0.93, 0.05, 2.0, 0.70, 40), ("大旱", 0.07, 0.35, 4.0, 0.40, 75)]),
            ("涝", 0.10, [("小涝", 0.95, 0.02, -1.5, 1.30, 40), ("大涝", 0.05, 0.40, -3.0, 1.70, 80)]),
            ("热浪", 0.07, [("小热浪", 0.93, 0.02, 3.5, 1.0, 40), ("大热浪", 0.07, 0.20, 6.0, 1.0, 75)]),
        ],
        2: [  # 秋
            ("旱", 0.12, [("小旱", 0.95, 0.02, 1.5, 0.80, 40), ("大旱", 0.05, 0.15, 3.0, 0.50, 70)]),
            ("涝", 0.10, [("小涝", 0.93, 0.05, -1.5, 1.30, 40), ("大涝", 0.07, 0.30, -3.0, 1.60, 75)]),
            ("寒潮", 0.05, [("小寒潮", 0.93, 0.05, -3.5, 1.0, 40), ("大寒潮", 0.07, 0.30, -7.0, 1.0, 75)]),
        ],
        3: [  # 冬
            ("旱", 0.12, [("小旱", 0.95, 0.02, 1.5, 0.80, 35), ("大旱", 0.05, 0.20, 3.0, 0.50, 70)]),
            ("涝", 0.05, [("小涝", 0.95, 0.02, -1.5, 1.30, 35), ("大涝", 0.05, 0.15, -3.0, 1.50, 70)]),
            ("寒潮", 0.10, [("小寒潮", 0.95, 0.00, -3.0, 1.0, 35), ("大寒潮", 0.05, 0.15, -6.5, 1.0, 75)]),
        ],
    },
    "central": {
        0: [  # 春
            ("旱", 0.10, [("小旱", 0.95, 0.02, 1.5, 0.80, 40), ("大旱", 0.05, 0.15, 3.0, 0.50, 70)]),
            ("涝", 0.12, [("小涝", 0.93, 0.05, -1.5, 1.30, 40), ("大涝", 0.07, 0.30, -3.0, 1.60, 75)]),
            ("寒潮", 0.10, [("小寒潮", 0.93, 0.05, -3.0, 1.0, 40), ("大寒潮", 0.07, 0.25, -6.0, 1.0, 75)]),
            ("热浪", 0.02, [("小热浪", 0.95, 0.02, 3.0, 1.0, 40), ("大热浪", 0.05, 0.10, 5.0, 1.0, 70)]),
        ],
        1: [  # 夏
            ("旱", 0.15, [("小旱", 0.93, 0.05, 2.0, 0.70, 40), ("大旱", 0.07, 0.35, 4.0, 0.40, 75)]),
            ("涝", 0.10, [("小涝", 0.95, 0.05, -1.5, 1.30, 45), ("大涝", 0.05, 0.50, -3.5, 1.80, 85)]),
            ("热浪", 0.07, [("小热浪", 0.93, 0.02, 3.5, 1.0, 40), ("大热浪", 0.07, 0.15, 5.5, 1.0, 70)]),
        ],
        2: [  # 秋
            ("旱", 0.10, [("小旱", 0.95, 0.02, 1.5, 0.80, 40), ("大旱", 0.05, 0.15, 3.0, 0.50, 70)]),
            ("涝", 0.07, [("小涝", 0.95, 0.02, -1.5, 1.30, 40), ("大涝", 0.05, 0.20, -3.0, 1.60, 70)]),
            ("寒潮", 0.07, [("小寒潮", 0.93, 0.05, -3.0, 1.0, 40), ("大寒潮", 0.07, 0.30, -6.5, 1.0, 75)]),
        ],
        3: [  # 冬
            ("涝", 0.07, [("小涝", 0.95, 0.02, -1.5, 1.30, 35), ("大涝", 0.05, 0.15, -3.0, 1.50, 70)]),
            ("寒潮", 0.02, [("小寒潮", 0.95, 0.00, -3.0, 1.0, 35), ("大寒潮", 0.05, 0.10, -6.0, 1.0, 70)]),
        ],
    },
    "south": {
        0: [  # 春
            ("涝", 0.10, [("小涝", 0.95, 0.02, -1.5, 1.30, 40), ("大涝", 0.05, 0.15, -2.5, 1.50, 70)]),
            ("寒潮", 0.05, [("小寒潮", 0.95, 0.02, -2.5, 1.0, 35), ("大寒潮", 0.05, 0.15, -5.5, 1.0, 70)]),
            ("旱", 0.05, [("小旱", 0.95, 0.02, 1.5, 0.80, 35), ("大旱", 0.05, 0.10, 3.0, 0.55, 65)]),
            ("热浪", 0.02, [("小热浪", 0.95, 0.00, 2.5, 1.0, 35), ("大热浪", 0.05, 0.05, 4.5, 1.0, 65)]),
        ],
        1: [  # 夏
            ("旱", 0.10, [("小旱", 0.95, 0.02, 1.5, 0.80, 40), ("大旱", 0.05, 0.15, 3.5, 0.50, 70)]),
            ("涝", 0.12, [("小涝", 0.93, 0.05, -1.5, 1.35, 45), ("大涝", 0.07, 0.50, -3.5, 1.85, 85)]),
            ("热浪", 0.07, [("小热浪", 0.93, 0.02, 3.0, 1.0, 40), ("大热浪", 0.07, 0.20, 5.5, 1.0, 75)]),
        ],
        2: [  # 秋
            ("涝", 0.07, [("小涝", 0.93, 0.05, -1.5, 1.35, 45), ("大涝", 0.07, 0.50, -3.5, 1.80, 85)]),
            ("寒潮", 0.07, [("小寒潮", 0.93, 0.05, -3.0, 1.0, 40), ("大寒潮", 0.07, 0.30, -6.5, 1.0, 75)]),
            ("热浪", 0.05, [("小热浪", 0.95, 0.02, 3.0, 1.0, 40), ("大热浪", 0.05, 0.10, 5.0, 1.0, 70)]),
            ("旱", 0.02, [("小旱", 0.95, 0.00, 1.5, 0.85, 35), ("大旱", 0.05, 0.05, 2.5, 0.60, 65)]),
        ],
        3: [  # 冬
            ("涝", 0.07, [("小涝", 0.95, 0.02, -1.5, 1.30, 35), ("大涝", 0.05, 0.15, -3.0, 1.50, 70)]),
            ("寒潮", 0.02, [("小寒潮", 0.95, 0.00, -2.5, 1.0, 35), ("大寒潮", 0.05, 0.20, -5.5, 1.0, 75)]),
            ("旱", 0.05, [("小旱", 0.95, 0.00, 1.5, 0.85, 35), ("大旱", 0.05, 0.05, 2.5, 0.60, 65)]),
        ],
    },
}

# ── 瘟疫触发基础概率表 仅在大灾难后第 20 天判定 ──
PLAGUE_BASE_CHANCE = {
    "大旱": {"north": 0.25, "central": 0.35, "south": 0.30},
    "大涝": {"north": 0.30, "central": 0.40, "south": 0.45},
    "大寒潮": {"north": 0.10, "central": 0.15, "south": 0.10},
    "大热浪": {"north": 0.15, "central": 0.20, "south": 0.25},
}

# ── 历史真实地震脚本 ──
HISTORICAL_EARTHQUAKES = [
    {
        "id": "eq_1628_10_07",
        "date": (1628, 10, 7),
        "province_id": "beizhili",
        "location": "北直隶万全都指挥使司",
        "magnitude": 6.25,
        "death_rate": 0.0001,
        "penalty": 0.01,
        "penalty_days": 30,
    },
    {
        "id": "eq_1652_07_13",
        "date": (1652, 7, 13),
        "province_id": "yunnan",
        "location": "云南大理府",
        "magnitude": 7.0,
        "death_rate": 0.0001,
        "penalty": 0.01,
        "penalty_days": 30,
    },
    {
        "id": "eq_1654_07_21",
        "date": (1654, 7, 21),
        "province_id": "shaanxi",
        "location": "陕西布政使司巩昌府",
        "magnitude": 8.0,
        "death_rate": 0.001,
        "penalty": 0.02,
        "penalty_days": 30,
    },
    {
        "id": "eq_1668_07_25",
        "date": (1668, 7, 25),
        "province_id": "shandong",
        "location": "山东布政使司兖州府",
        "magnitude": 8.5,
        "death_rate": 0.001,
        "penalty": 0.05,
        "penalty_days": 30,
    },
    {
        "id": "eq_1679_09_02",
        "date": (1679, 9, 2),
        "province_id": "beizhili",
        "location": "北直隶顺天府",
        "magnitude": 8.0,
        "death_rate": 0.001,
        "penalty": 0.02,
        "penalty_days": 30,
    },
    {
        "id": "eq_1683_11_22",
        "date": (1683, 11, 22),
        "province_id": "shanxi",
        "location": "山西太原府",
        "magnitude": 8.0,
        "death_rate": 0.001,
        "penalty": 0.02,
        "penalty_days": 30,
    },
    {
        "id": "eq_1695_05_18",
        "date": (1695, 5, 18),
        "province_id": "shanxi",
        "location": "山西布政使司平阳府",
        "magnitude": 8.0,
        "death_rate": 0.001,
        "penalty": 0.02,
        "penalty_days": 30,
    },
]


def _load_climate() -> Dict[str, Any]:
    with open(CLIMATE_PATH, encoding="utf-8") as f:
        return json.load(f)


CLIMATE = _load_climate()


def climate_of(pid: str) -> Optional[Dict[str, Any]]:
    return CLIMATE.get(pid)


def month_temp(climate: Dict[str, Any], month: int) -> float:
    """月度温度表取值 全月唯一温度，无日波动。"""
    return climate["temp_month"][month - 1]


# ── 季节工具 简化历：每月 30 日，一年 360 日──
SEASON_NAMES = ["春", "夏", "秋", "冬"]


def season_of(month: int) -> int:
    return (month - 3) // 3 % 4


def season_start_month(season: int) -> int:
    return [3, 6, 9, 12][season]


def season_key(date: Dict[str, Any]) -> Tuple[int, int]:
    """季度键  气候年, 季节：冬季 12-2月归上年，其余归当年。"""
    s = season_of(date["month"])
    y = date["year"]
    if s == 3 and date["month"] in (1, 2):
        y -= 1
    return (y, s)


def day_in_season(date: Dict[str, Any]) -> int:
    """季度内第几天 1~90。"""
    m, d = date["month"], date["day"]
    start = season_start_month(season_of(m))
    if m >= start:
        return (m - start) * 30 + d
    return (m - start + 12) * 30 + d


def season_remain_days(date: Dict[str, Any]) -> int:
    """本季度剩余天数 含当日。"""
    return 90 - day_in_season(date) + 1


# ── 灾害建筑状态检查 ──
# 气候效果**不再按工程名写死在这里**：数值写成词条，落在工程 buff 里
# （world/provinces.json），本处只按词条口径汇总：
#   buff.disaster[kind]          该种类灾害的产能损失修正（负数＝减轻）
#   buff.disaster_chance[kind]   该种类灾害的发生概率修正
# 只有**运营中**的工程生效；损毁程度按 (1−损毁) 折算。


def _disaster_reduction(state: Any, pid: str, target: str, kind: str) -> float:
    """某类灾害上「工程」这一路贡献的合计减免（0~1）。

    直接读修正系统的贡献明细，不再自己扫 buff：工程写的是词条
    （`buff.disaster[kind]` / `buff.disaster_chance[kind]`），
    按 (1−损毁程度) 折算后作为乘法修正参与唯一公式，这里只把结果折成「减免多少」。
    """
    if state is None:
        return 0.0
    from .modifier_registry import contributions
    keep = 1.0
    for r in contributions(state, target, pid, kind=kind):
        if r.get("from") != "工程":
            continue
        if r.get("op") == "乘法":
            keep *= (1.0 + float(r.get("value") or 0.0))
    return round(1.0 - keep, 4)


def yellow_flood_current_penalty(loc: Dict[str, Any]) -> float:
    """黄汛当前减产比例（初始值按月衰减）——**唯一实现处**。

    黄汛的灾害效果先在这里定出来（初始减产、每月衰减、持续天数），
    再以「乘法修正」的形式进产能（`modifier_registry._from_weather`），
    不另开一条产能通道。economy 与 modifiers 都只能调这一个函数，
    免得同一个衰减公式各写一遍。
    """
    yf = (loc or {}).get("yellow_flood") or {}
    if not yf or int(yf.get("remain_days", 0)) <= 0:
        return 0.0
    month_idx = int(yf.get("month_idx", 0))
    init_p = float(yf.get("initial_penalty", 0.0))
    decay = float(yf.get("decay_per_month", 0.0))
    return round(max(0.0, init_p - month_idx * decay), 4)


def province_flood_reduction(state: Any, pid: str) -> float:
    """本省「大涝导致的产能损失」被工程减免的比例（词条口径，不含超控与科技）。"""
    return _disaster_reduction(state, pid, "disaster_penalty", "flood_major")


def yellow_flood_reduction(state: Any) -> float:
    """全国「黄汛导致的产能损失」被工程减免的比例。"""
    return _disaster_reduction(state, None, "disaster_penalty", "yellow_flood")


def yellow_flood_chance_reduction(state: Any) -> float:
    """全国「黄汛发生概率」被工程减少的比例。"""
    return _disaster_reduction(state, None, "disaster_chance", "yellow_flood")


# ── 人口扣减核心算法 ──

def apply_population_loss(loc: Dict[str, Any], total_loss: int) -> int:
    """按各阶级现存人口等比扣减总死亡人数 精确到个位数，返回实际扣减人数。"""
    if total_loss <= 0 or not loc:
        return 0
    cls_map = loc.get("classes") or {}
    exact_pops = {}
    for c_name, c_data in cls_map.items():
        if "pop_exact" in c_data:
            exact_pops[c_name] = int(c_data["pop_exact"])
        else:
            exact_pops[c_name] = int(round(float(c_data.get("pop", 0.0)) * 10000))
            c_data["pop_exact"] = exact_pops[c_name]

    tot_pop = sum(exact_pops.values())
    if tot_pop <= 0:
        return 0

    actual_total_loss = min(tot_pop, total_loss)
    rem_loss = actual_total_loss

    for c_name, c_data in cls_map.items():
        if tot_pop > 0:
            c_loss = int(round(actual_total_loss * exact_pops[c_name] / tot_pop))
        else:
            c_loss = 0
        c_loss = min(exact_pops[c_name], c_loss)
        exact_pops[c_name] -= c_loss
        rem_loss -= c_loss
        c_data["pop_exact"] = exact_pops[c_name]
        c_data["pop"] = round(exact_pops[c_name] / 10000.0, 4)

    if rem_loss > 0:
        sorted_classes = sorted(cls_map.keys(), key=lambda k: cls_map[k].get("pop_exact", 0), reverse=True)
        for k in sorted_classes:
            cur = cls_map[k].get("pop_exact", 0)
            deduct = min(cur, rem_loss)
            cls_map[k]["pop_exact"] = cur - deduct
            cls_map[k]["pop"] = round(cls_map[k]["pop_exact"] / 10000.0, 4)
            rem_loss -= deduct
            if rem_loss <= 0:
                break

    return actual_total_loss


def record_pending_sacrifice(state: Any, disaster_name: str, province_id: str, province_name: str,
                             year: int, deaths: int) -> None:
    """记录需要次月1日进行礼部祭祀的灾害死亡事件。"""
    if not state or deaths <= 0:
        return
    from .time import cn_era
    era_str = cn_era(year)
    title = f"{era_str}{province_name}{disaster_name}"
    pending = getattr(state, "pending_sacrifices", None)
    if pending is None:
        pending = state.pending_sacrifices = []

    pending.append({
        "disaster_name": disaster_name,
        "province_id": province_id,
        "province_name": province_name,
        "year": year,
        "era_str": era_str,
        "title": title,
        "deaths": deaths,
    })


# ── 省份极端天气与灾害独立抽取 ──

def _roll_province_disasters(state: Any, pid: str, season: int, rng: random.Random) -> Dict[str, Any]:
    """每省每季度独立判定灾害 大旱/大涝/热浪/寒潮。

    考虑海宁海塘等建筑对洪涝的减免。

    注：**西北大灾不再在这里加旱灾概率** 旧机制已删。西北大灾改走
    `engine.mainline_script.tick_northwest_famine` 的分阶段干旱地区修正，
    与常规灾害独立、可叠加，且不影响本函数的任何概率。
    """
    region = get_province_region(pid)
    reg_table = DISASTER_TABLE.get(region, {}).get(season, [])

    triggered_items = []
    for kind, chance, options in reg_table:
        cur_options = list(options)

        if rng.random() < chance:
            r = rng.random()
            cum = 0.0
            chosen_opt = cur_options[0]
            for opt in cur_options:
                cum += opt[1]
                if r <= cum:
                    chosen_opt = opt
                    break
            d_name, _, penalty, d_temp, p_ratio, sev = chosen_opt

            # 灾害减产：基础值按 disaster_penalty 的唯一公式算（工程减灾是其中一项修正）
            from .modifier_registry import value as _mod_value
            _pen_kind = {"涝": "flood_major" if "大" in d_name else "flood_minor",
                         "旱": "drought", "热浪": "heat", "寒潮": "cold"}.get(kind)
            if _pen_kind:
                penalty = round(max(0.0, _mod_value(
                    state, "disaster_penalty", penalty, pid, kind=_pen_kind)["value"]), 4)

            is_major = "大" in d_name
            triggered_items.append({
                "name": d_name,
                "kind": kind,
                "level": "大" if is_major else "小",
                "penalty": penalty,
                "delta_temp": d_temp,
                "precip_ratio": p_ratio,
                "severity": sev,
                "is_major": is_major,
            })

    # 互斥处理：旱涝互斥、寒暑互斥
    drought_or_flood = [item for item in triggered_items if item["kind"] in  ("旱", "涝")]
    if len(drought_or_flood) > 1:
        best_df = max(drought_or_flood, key=lambda x: (x["is_major"], x["penalty"], x["severity"]))
        triggered_items = [item for item in triggered_items if item["kind"] not in  ("旱", "涝" or item is best_df)]

    cold_or_heat = [item for item in triggered_items if item["kind"] in  ("寒潮", "热浪")]
    if len(cold_or_heat) > 1:
        best_ch = max(cold_or_heat, key=lambda x: (x["is_major"], x["penalty"], x["severity"]))
        triggered_items = [item for item in triggered_items if item["kind"] not in  ("寒潮", "热浪" or item is best_ch)]

    if not triggered_items:
        return {
            "items": [],
            "capacity_penalty": 0.0,
            "delta_temp": 0.0,
            "precip_ratio": 1.0,
            "display_name": "",
            "severity": 0,
            "major_disasters": [],
            "daily_death": 0,
            "total_death": 0,
        }

    tot_penalty = sum(item["penalty"] for item in triggered_items)
    tot_delta_temp = sum(item["delta_temp"] for item in triggered_items)
    tot_precip_ratio = 1.0
    for item in triggered_items:
        tot_precip_ratio *= item["precip_ratio"]

    main_item = max(triggered_items, key=lambda x: (x["is_major"], x["penalty"], x["severity"]))
    display_name = "、".join(item["name"] for item in triggered_items)
    major_list = [item["name"] for item in triggered_items if item["is_major"]]

    # ── 连续受灾季度维护与死亡人数计算 ──
    # 查找该省当前人口与连续受灾季度
    pop_exact = 1000000
    consecutive_seasons = 1
    if state and pid in state.provinces:
        loc = state.provinces[pid].get("local") or {}
        from .localdata import population_exact
        pop_exact = population_exact(loc)
        prev_consec = int(loc.get("consecutive_seasons", 0))
        consecutive_seasons = prev_consec + 1
        loc["consecutive_seasons"] = consecutive_seasons
    elif state and hasattr(state, "provinces"):
        loc = (state.provinces.get(pid) or {}).get("local") or {}
        loc["consecutive_seasons"] = 1

    # 每次灾害总死亡人数 = 本省人口 × 0.1 × 产能惩罚 × 灾害种类修正 × 连续修正 × 建筑修正
    # 建筑修正已直接作用于产能惩罚 同乘区，此处建筑修正取 1.0
    kind_coeff = DISASTER_DEATH_COEFFS.get(main_item["name"], 0.01)
    total_death = int(round(pop_exact * 0.1 * tot_penalty * kind_coeff * consecutive_seasons * 1.0))
    # 【v0.3.4】日额保留小数，逐日取整时把余数累积起来，原来 //90 会把不足一天的人永远丢掉
    daily_death = max(0.0, total_death / 90.0)

    return {
        "items": triggered_items,
        "capacity_penalty": round(tot_penalty, 4),
        "delta_temp": round(tot_delta_temp, 1),
        "precip_ratio": round(tot_precip_ratio, 2),
        "display_name": display_name,
        "severity": main_item["severity"],
        "major_disasters": major_list,
        "daily_death": daily_death,
        "total_death": total_death,
        "consecutive_seasons": consecutive_seasons,
    }


# ── 小降水与大降水分配 方差平稳、多日随机 ──

def _roll_small_rain(climate: Dict[str, Any], season: int, small_ratio: float,
                     disaster_plans: Dict[str, Any], rng: random.Random) -> Dict[str, Dict[int, float]]:
    """每省随机 20~35 个小降水日，按权重将小降水配额分配到各日。"""
    rain_days: Dict[str, Dict[int, float]] = {}
    for pid, c in climate.items():
        base_total = c["rain_season"][season]
        if base_total <= 0:
            continue
        p_ratio = disaster_plans.get(pid, {}).get("precip_ratio", 1.0)
        total = base_total * p_ratio
        if total <= 0:
            continue

        days = rng.sample(range(1, 91), rng.randint(20, 35))
        quota = total * small_ratio
        weights = {d: rng.uniform(0.85, 1.15) for d in days}
        sw = sum(weights.values())
        mm = {d: quota * w / sw for d, w in weights.items()}
        lo, hi = total * SMALL_DAY_MIN, total * SMALL_DAY_MAX
        over = 0.0
        for d in days:
            if mm[d] > hi:
                over += mm[d] - hi
                mm[d] = hi
            elif mm[d] < lo:
                over -= lo - mm[d]
                mm[d] = lo
        if over:
            free = [d for d in days if lo < mm[d] < hi]
            if free:
                each = over / len(free)
                for d in free:
                    mm[d] = min(hi, mm[d] + each)
        rain_days[pid] = {d: round(v, 1) for d, v in mm.items()}
    return rain_days


def _roll_big_events(climate: Dict[str, Any], season: int, small_ratio: float,
                     small_rain_days: Dict[str, Dict[int, float]],
                     disaster_plans: Dict[str, Any], rng: random.Random) -> List[Dict[str, Any]]:
    """大降水事件生成：

    - 春季/夏季 季风型：每省抽取除了小降水日期的剩余日期中的 4~6 天 春/ 6~8 天 夏，
      把大降水配额随机分配到这几天，方差平稳 微扰权重 0.85~1.15，总和精确闭合。
    - 秋季/冬季 冷锋型：冷锋自北向南推进 秋 4~6 次 / 冬 6~8 次，
      每次冷锋过境分配大降水，方差平稳，总和精确闭合。
    """
    big_ratio = 1.0 - small_ratio
    events = []

    # 1. 春夏季风降水
    if season in (0, 1):
        min_d, max_d = MONSOON_DAYS[season]
        for pid, c in climate.items():
            base_total = c["rain_season"][season]
            if base_total <= 0:
                continue
            p_ratio = disaster_plans.get(pid, {}).get("precip_ratio", 1.0)
            total = base_total * p_ratio
            if total <= 0:
                continue

            big_quota = total * big_ratio
            small_days = set((small_rain_days.get(pid) or {}).keys())
            avail_days = [d for d in range(1, 91) if d not in small_days]

            num_days = rng.randint(min_d, max_d)
            num_days = min(num_days, len(avail_days))
            if num_days <= 0:
                continue

            chosen_days = sorted(rng.sample(avail_days, num_days))
            weights = [rng.uniform(0.85, 1.15) for _ in range(num_days)]
            sw = sum(weights)
            allocs = [round(big_quota * w / sw, 1) for w in weights]
            diff = round(big_quota - sum(allocs), 1)
            allocs[0] = round(allocs[0] + diff, 1)

            for d, mm_val in zip(chosen_days, allocs):
                events.append({
                    "type": "monsoon",
                    "day": d,
                    "province": pid,
                    "mm": mm_val,
                })
        return events

    # 2. 秋冬冷锋降水
    count = rng.randint(*COLD_FRONT_COUNTS[season])
    for pid, c in climate.items():
        base_total = c["rain_season"][season]
        if base_total <= 0:
            continue
        p_ratio = disaster_plans.get(pid, {}).get("precip_ratio", 1.0)
        total = base_total * p_ratio
        if total <= 0:
            continue

        big_quota = total * big_ratio
        weights = [rng.uniform(0.85, 1.15) for _ in range(count)]
        sw = sum(weights)
        allocs = [round(big_quota * w / sw, 1) for w in weights]
        diff = round(big_quota - sum(allocs), 1)
        allocs[0] = round(allocs[0] + diff, 1)

        band_idx = 0
        for bi, band in enumerate(COLD_FRONT_BANDS):
            if pid in band:
                band_idx = bi
                break

        for fi in range(count):
            day0 = rng.randint(1, 84)
            delay = band_idx * rng.randint(1, 2)
            event_day = min(90, day0 + delay)
            events.append({
                "type": "coldfront",
                "day": event_day,
                "province": pid,
                "mm": allocs[fi],
            })
    return events


def _roll_typhoons(climate: Dict[str, Any], season: int, rng: random.Random) -> List[Dict[str, Any]]:
    """台风生成：仅秋季；沿海允许省份 东番权重×2，1~3 个，每次一个省。

    严禁落点：北直隶、山东、南直隶、广西、辽东。
    """
    if season != 2:
        return []
    n = rng.randint(1, 3)
    pids = [p for p in COASTAL_PROVINCES if p in climate]
    if not pids:
        return []
    weights = [COASTAL_PROVINCES[p] for p in pids]
    out = []
    for _ in range(n):
        pid = rng.choices(pids, weights=weights, k=1)[0]
        total = climate[pid]["rain_season"][2]
        out.append({
            "day": rng.randint(1, 90),
            "province": pid,
            "mm": round(total * rng.uniform(TYPHOON_MIN, TYPHOON_MAX), 1),
        })
    return out


# ── 季度计划生成与维护 ──

def _new_plan(state: Any, key: Tuple[int, int], rng: random.Random, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """生成当前季度天气与灾害计划。"""
    year, season = key
    climate = CLIMATE

    # 1. 各省独立判定灾害
    disaster_plans: Dict[str, Any] = {}
    for pid in climate:
        dp = _roll_province_disasters(state, pid, season, rng)
        disaster_plans[pid] = dp
        # 如果未发生天气灾害，重置连续受灾季度
        if not dp["items"] and state and pid in state.provinces:
            loc = state.provinces[pid].get("local") or {}
            loc["consecutive_seasons"] = 0

        if dp["items"]:
            for item in dp["items"]:
                events.append({
                    "province": pid,
                    "province_id": pid,
                    "disaster": item["name"],
                    "severity": item["severity"],
                    "penalty": item["penalty"],
                })
                CONSOLE.log("weather", "%s发生%s" % (pid, item["name"]),
                            "产能-%d%%，温偏%+.1f℃，降水×%.2f"
                            % (int(item["penalty"] * 100), item["delta_temp"], item["precip_ratio"]))
            if dp.get("total_death", 0) > 0:
                p_obj = state.provinces.get(pid) if state and hasattr(state, "provinces") else {}
                p_name = p_obj.get("name", pid)
                record_pending_sacrifice(state, dp["display_name"], pid, p_name, year, dp["total_death"])

    small_ratio = rng.uniform(0.30, 0.40)
    small_rain_days = _roll_small_rain(climate, season, small_ratio, disaster_plans, rng)
    big_events = _roll_big_events(climate, season, small_ratio, small_rain_days, disaster_plans, rng)
    typhoons = _roll_typhoons(climate, season, rng)

    plan = {
        "key": list(key),
        "season": season,
        "season_name": SEASON_NAMES[season],
        "small_ratio": small_ratio,
        "disaster_plans": disaster_plans,
        "rain_days": small_rain_days,
        "events": big_events,
        "typhoons": typhoons,
        "plague_checked": {},
    }
    return plan


def _ensure_plan(state: Any, date: Dict[str, Any], rng: random.Random, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """确保 state.weather_plan 与当前日期所属季度匹配 跨季则重新调度。"""
    if getattr(state, "weather_plan", None) is None:
        state.weather_plan = {}
    key = season_key(date)
    plan = state.weather_plan.get("plan") or {}
    if plan.get("key") == list(key):
        return plan
    state.weather_plan["plan"] = _new_plan(state, key, rng, events)
    return state.weather_plan["plan"]


# ── 瘟疫判定机制 ──

def _check_plague_for_province(state: Any, pid: str, p: Dict[str, Any], date: Dict[str, Any],
                               dp: Dict[str, Any], rng: random.Random, events: List[Dict[str, Any]]) -> None:
    """大灾难发生后 20 天触发瘟疫判定。"""
    major_list = dp.get("major_disasters") or []
    if not major_list:
        return

    season = season_of(date["month"])
    region = get_province_region(pid)
    p_name = p.get("name", pid)
    loc = p.get("local") or {}

    highest_prob = 0.0
    for m_dis in major_list:
        base_p = PLAGUE_BASE_CHANCE.get(m_dis, {}).get(region, 0.15)
        if base_p > highest_prob:
            highest_prob = base_p

    if season == 3:
        highest_prob *= 0.20

    if rng.random() < highest_prob:
        is_major_plague = (rng.random() >= 0.80)
        rem_in_season = season_remain_days(date)

        if is_major_plague:
            plague_name = "大疫"
            penalty = 0.25
            total_days = rem_in_season + 90
            kind_coeff = DISASTER_DEATH_COEFFS["大疫"]
        else:
            plague_name = "小疫"
            penalty = 0.10
            total_days = rem_in_season
            kind_coeff = DISASTER_DEATH_COEFFS["小疫"]

        from .localdata import population_exact
        from .modifier_registry import value as _mod_value
        pop_exact = population_exact(loc)
        consec = max(1, int(loc.get("consecutive_seasons", 1)))
        # 瘟疫减产与死亡都按修正口径算（词条：瘟疫导致的产能损失 / 灾害死亡人数）
        penalty = round(max(0.0, _mod_value(
            state, "disaster_penalty", penalty, pid, kind="plague")["value"]), 4)
        base_death = int(round(pop_exact * 0.1 * penalty * kind_coeff * consec * 1.0))
        total_death = int(round(max(0.0, _mod_value(
            state, "disaster_death", float(base_death), pid)["value"])))
        daily_death = max(0.0, total_death / float(total_days))   # 【v0.3.4】日额留小数，余数累积

        loc["plague"] = {
            "name": plague_name,
            "penalty": penalty,
            "remain_days": total_days,
            "severity": 85 if is_major_plague else 50,
            "start_date": dict(date),
            "daily_death": daily_death,
            "total_death": total_death,
        }

        events.append({
            "type": "plague",
            "province": p_name,
            "province_id": pid,
            "name": plague_name,
            "penalty": penalty,
            "remain_days": total_days,
            "severity": 85 if is_major_plague else 50,
            "total_death": total_death,
        })
        if total_death > 0:
            record_pending_sacrifice(state, plague_name, pid, p_name, date["year"], total_death)
        CONSOLE.log("plague", "【%s】爆发%s" % (p_name, plague_name),
                    "持续%d日，全阶级产能额外-%d%%，预计死亡%d人"
                    % (total_days, int(penalty * 100), total_death))


# ── 黄汛判定机制 河南/山东/南直隶 ──

def _check_yellow_flood(state: Any, pid: str, p: Dict[str, Any], date: Dict[str, Any],
                        is_big_rain: bool, dp: Dict[str, Any], rng: random.Random,
                        events: List[Dict[str, Any]]) -> None:
    """河南、山东、南直隶在夏秋大涝大降水日触发黄汛判定。"""
    if pid not in ("henan", "shandong", "nanzhili"):
        return
    season = season_of(date["month"])
    if season not in (1, 2):  # 仅夏秋
        return
    if "大涝" not in (dp.get("major_disasters") or []):
        return
    if not is_big_rain:
        return

    loc = p.get("local") or {}
    if loc.get("yellow_flood") or loc.get("pending_yellow_flood"):
        return

    # 黄汛发生概率：基础 70%，按 disaster_chance 的修正算（工程减灾是其中一项修正）
    from .modifier_registry import value as _mod_value
    base_chance = _mod_value(state, "disaster_chance", 0.70, pid,
                             kind="yellow_flood")["value"]
    base_chance = max(0.0, min(1.0, base_chance))

    if rng.random() < base_chance:
        # 满足黄汛触发，标记 pending，次日追加判定大黄汛
        loc["pending_yellow_flood"] = {
            "stage": 1,
            "trigger_date": dict(date),
        }
        CONSOLE.log("yellow_flood", "【%s】黄河汛情告急" % p.get("name", pid),
                    "暴雨引动黄汛，次日将勘定决口规模")


def _resolve_pending_yellow_flood(state: Any, pid: str, p: Dict[str, Any], date: Dict[str, Any],
                                  rng: random.Random, events: List[Dict[str, Any]]) -> None:
    """次日判定是否为大黄汛，并施加全省惩罚与死亡判定。"""
    loc = p.get("local") or {}
    pending = loc.get("pending_yellow_flood")
    if not pending:
        return
    del loc["pending_yellow_flood"]

    # 大黄汛判定：基础 10%，按 disaster_chance 的修正算
    from .modifier_registry import value as _mod_value
    major_chance = max(0.0, min(1.0, _mod_value(
        state, "disaster_chance", 0.10, pid, kind="yellow_flood_major")["value"]))
    is_major = (rng.random() < major_chance)

    from .localdata import population_exact
    pop_exact = population_exact(loc)
    consec = max(1, int(loc.get("consecutive_seasons", 1)))
    p_name = p.get("name", pid)

    if is_major:
        name = "大黄汛"
        base_pen = 0.36
        base_decay = 0.04
        total_days = 270  # 3 个季度  9 个月
        kind_coeff = DISASTER_DEATH_COEFFS["大黄汛"]  # 0.30
    else:
        name = "黄汛"
        base_pen = 0.18
        base_decay = 0.03
        total_days = 180  # 2 个季度  6 个月
        kind_coeff = DISASTER_DEATH_COEFFS["黄汛"]  # 0.20

    # 减产幅度：基础值按 disaster_penalty 的唯一公式算（工程减灾是其中一项修正）
    pen_kind = "yellow_flood_major" if is_major else "yellow_flood"
    init_penalty = round(max(0.0, _mod_value(
        state, "disaster_penalty", base_pen, pid, kind=pen_kind)["value"]), 4)
    # 每月衰减按减产同比缩放，保持「首月最大、逐月递减」的形状
    decay = round(base_decay * (init_penalty / base_pen), 4) if base_pen else 0.0

    # 死亡人数：基础值按 disaster_death 的唯一公式算
    base_death = int(round(pop_exact * 0.1 * init_penalty * kind_coeff * consec * 1.0))
    total_death = int(round(max(0.0, _mod_value(
        state, "disaster_death", float(base_death), pid)["value"])))
    daily_death = max(0.0, total_death / float(total_days))   # 【v0.3.4】日额留小数

    if is_major:
        # 大黄汛损毁黄河工程：损毁程度由 damage_degree 的唯一公式给（基础 100%），
        # 不按工程名认，也不写死成「直接损坏」
        from . import projects as _projects
        damaged = []
        for prov in state.provinces.values():
            ploc = prov.get("local") or {}
            for pj in ploc.get("projects") or []:
                buff = pj.get("buff") or {}
                # 只损毁「自己声明管黄汛」的工程：词条 kind 以 yellow_flood 开头。
                # 海宁海塘管的是大小涝，不该被黄河决口冲毁。
                kinds = list((buff.get("disaster") or {})) + \
                        list((buff.get("disaster_chance") or {}))
                if not any(str(k).startswith("yellow_flood") for k in kinds):
                    continue
                if str(pj.get("status") or "运营中") != "运营中":
                    continue
                dmg = _mod_value(state, "damage_degree", 1.0, pid,
                                 kind="yellow_flood_major")["value"]
                dmg = max(0.0, min(1.0, dmg))
                _projects.set_damage(pj, max(_projects.damage_of(pj), dmg))
                damaged.append({"province": prov.get("name", ""),
                                "name": pj.get("name", ""),
                                "damage": _projects.damage_of(pj)})
        for d in damaged:
            CONSOLE.log("yellow_flood", "【%s】黄河大决" % p_name,
                        "%s工程【%s】%s" % (d["province"], d["name"],
                                          "全毁" if d["damage"] >= 1 else
                                          "损毁 %d%%" % round(d["damage"] * 100)))

    loc["yellow_flood"] = {
        "name": name,
        "initial_penalty": init_penalty,
        "decay_per_month": decay,
        "month_idx": 0,
        "total_days": total_days,
        "remain_days": total_days,
        "daily_death": daily_death,
        "total_death": total_death,
        "start_date": dict(date),
        "is_major": is_major,
    }

    events.append({
        "type": "yellow_flood",
        "province": p_name,
        "province_id": pid,
        "name": name,
        "penalty": init_penalty,
        "remain_days": total_days,
        "total_death": total_death,
        "severity": 90 if is_major else 65,
    })
    if total_death > 0:
        record_pending_sacrifice(state, name, pid, p_name, date["year"], total_death)
    CONSOLE.log("yellow_flood", "【%s】爆发%s" % (p_name, name),
                "首月产能-%d%%，持续%d日，预计死伤%d人"
                % (int(init_penalty * 100), total_days, total_death))


# ── 历史地震脚本判定 ──

def _check_historical_earthquakes(state: Any, date: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    """根据公历日期精准触发历史著名大地震。"""
    if state is None:
        return
    from .economy import get_gregorian_date
    from .localdata import population_exact

    greg = get_gregorian_date(date)
    greg_tuple = (greg.year, greg.month, greg.day)

    flags = getattr(state, "flags", None)
    if flags is None:
        flags = state.flags = set()

    for eq in HISTORICAL_EARTHQUAKES:
        if eq["date"] == greg_tuple:
            if eq["id"] in flags:
                continue
            flags.add(eq["id"])

            pid = eq["province_id"]
            p = state.provinces.get(pid)
            if not p:
                continue
            loc = p.get("local") or {}
            p_name = p.get("name", pid)
            pop_exact = population_exact(loc)

            # 伤亡人口：总人口 × death_rate
            tot_deaths = int(round(pop_exact * eq["death_rate"]))
            apply_population_loss(loc, tot_deaths)

            # 产能减产：持续 30 天；减产比例按修正口径算（词条：地震导致的产能损失）
            from .modifier_registry import value as _mod_value
            _eq_pen = round(max(0.0, _mod_value(
                state, "disaster_penalty", eq["penalty"], pid, kind="earthquake")["value"]), 4)
            loc["earthquake_penalty"] = {
                "name": "%s大地震" % eq["location"],
                "magnitude": eq["magnitude"],
                "penalty": _eq_pen,
                "remain_days": eq["penalty_days"],
                "location": eq["location"],
            }

            events.append({
                "type": "earthquake",
                "province": p_name,
                "province_id": pid,
                "location": eq["location"],
                "magnitude": eq["magnitude"],
                "deaths": tot_deaths,
                "penalty": eq["penalty"],
                "severity": int(eq["magnitude"] * 10),
            })
            if tot_deaths > 0:
                record_pending_sacrifice(state, f"{eq['location']}大地震", pid, p_name, date["year"], tot_deaths)
            CONSOLE.log("earthquake", "【%s】发生%.2f级地震" % (eq["location"], eq["magnitude"]),
                        "死伤%d人，全省下月产能-%d%%" % (tot_deaths, int(eq["penalty"] * 100)))


# ── 每日天气推进与结算 ──

def init_weather(state: Any, rng: random.Random = random) -> None:
    """开局初始化：生成当前季度计划，并设定每省当天天气。"""
    if getattr(state, "weather_plan", None) is None:
        state.weather_plan = {}
    _ensure_plan(state, state.date, rng, [])
    for pid, p in state.provinces.items():
        if climate_of(pid) is None:
            continue
        loc = p.get("local")
        if loc is None:
            loc = p["local"] = {}
        if not isinstance(loc.get("weather"), dict):
            loc["weather"] = {}
        _daily_weather(state, p, state.date, state.weather_plan["plan"], rng, [])


def tick_weather(state: Any, days: int = 1, rng: random.Random = random) -> List[Dict[str, Any]]:
    """推进 days 天：逐日回放，更新每省天气与灾害，并执行地震、瘟疫、黄汛判定。"""
    events: List[Dict[str, Any]] = []
    if days <= 0:
        return events

    cur = _shift(state.date, -(days - 1))
    for _ in range(days):
        y, m, d = map(int, cur.split("-"))
        date = {"year": y, "month": m, "day": d, "hour": 0}
        plan = _ensure_plan(state, date, rng, events)

        # 检查历史地震
        _check_historical_earthquakes(state, date, events)

        for pid, p in state.provinces.items():
            if climate_of(pid) is None:
                continue
            _daily_weather(state, p, date, plan, rng, events)
        cur = _shift(date, 1)
    return events


def _shift(date: Dict[str, Any], days: int) -> str:
    """简化历日期偏移。"""
    from .time import date_shift_key
    return date_shift_key(date, days)


def _daily_weather(state: Any, p: Dict[str, Any], date: Dict[str, Any],
                   plan: Dict[str, Any], rng: random.Random, events: List[Dict[str, Any]]) -> None:
    """更新单省当天天气与灾害/瘟疫/黄汛状态，并执行人口死亡逐日扣减。"""
    pid = p["id"]
    climate = climate_of(pid)
    if not climate:
        return

    loc = p.get("local")
    if loc is None:
        loc = p["local"] = {}
    w = loc.get("weather")
    if not isinstance(w, dict):
        loc["weather"] = w = {}

    month = date["month"]
    din = day_in_season(date)
    dp = (plan.get("disaster_plans") or {}).get(pid, {})

    # 1. 温度计算 基础月温 + 本省极端温偏 + 西北大旱的温偏
    #    西北大灾的分阶段干旱修正与常规灾害独立、直接叠加，但不改任何灾害概率。
    from .mainline_script import drought_climate
    drought_temp, drought_precip = drought_climate(p.get("local") or {})
    temp = climate["temp_month"][month - 1] + dp.get("delta_temp", 0.0) + drought_temp
    w["temp"] = round(temp, 1)

    mm = 0.0
    disaster = None
    remain = 0
    sev = 0
    is_big_rain = False

    # 2. 小降水
    mm = (plan.get("rain_days") or {}).get(pid, {}).get(din, 0.0) or 0.0

    # 3. 大降水事件 春夏季风 / 秋冬冷锋：当天该省
    big = next((e for e in (plan.get("events") or [])
                if e["province"] == pid and e["day"] == din), None)
    if big:
        mm = max(mm, big["mm"])
        disaster = "大雨"
        remain = 1
        sev = min(99, int(big["mm"]))
        is_big_rain = True
        events.append({
            "type": "rain",
            "province": p.get("name", pid),
            "province_id": pid,
            "disaster": "大雨",
            "severity": sev,
        })

    # 4. 台风
    ty = next((t for t in (plan.get("typhoons") or [])
               if t["province"] == pid and t["day"] == din), None)
    if ty:
        mm = max(mm, ty["mm"])
        disaster = "台风"
        remain = 1
        sev = min(99, int(ty["mm"]))
        events.append({
            "type": "typhoon",
            "province": p.get("name", pid),
            "province_id": pid,
            "disaster": "台风",
            "severity": sev,
        })

    # 4.5 西北大旱的降水倍率：把本日降水量按倍率缩放 只影响雨量，不改灾害概率
    if drought_precip != 1.0:
        mm = round(mm * drought_precip, 1)

    # 5. 极端天气与季度灾害标记
    if dp.get("display_name") and disaster is None:
        disaster = dp["display_name"]
        remain = season_remain_days(date)
        sev = dp.get("severity", 0)

    w["disaster"] = disaster
    w["disaster_remain"] = remain
    w["severity"] = sev
    w["rain_mm"] = round(mm, 1) if mm else 0
    w["capacity_penalty"] = dp.get("capacity_penalty", 0.0)

    if mm > 0:
        w["condition"] = "雪" if temp <= 0 else "雨"
    else:
        w["condition"] = rng.choices(CONDITIONS[:3], weights=[0.5, 0.3, 0.2], k=1)[0]

    # 6. 黄汛处理：判定与次日决口
    _resolve_pending_yellow_flood(state, pid, p, date, rng, events)
    _check_yellow_flood(state, pid, p, date, is_big_rain, dp, rng, events)

    # 7. 瘟疫系统推进与第 20 天判定
    plague_checked = plan.setdefault("plague_checked", {})
    if din == 20 and not plague_checked.get(pid):
        plague_checked[pid] = True
        _check_plague_for_province(state, pid, p, date, dp, rng, events)

    # 8. 每日生命周期与人口死亡扣减
    # 8.1 季度灾害每日死亡
    daily_disaster_death = float(dp.get("daily_death", 0) or 0.0)
    if daily_disaster_death > 0:
        acc = float(dp.get("death_carry", 0.0)) + daily_disaster_death
        n_death = int(acc)
        dp["death_carry"] = round(acc - n_death, 6)
        if n_death > 0:
            apply_population_loss(loc, n_death)

    # 8.2 瘟疫每日扣减与死亡
    plague = loc.get("plague")
    if plague:
        acc = float(plague.get("death_carry", 0.0)) + float(plague.get("daily_death", 0) or 0.0)
        p_death = int(acc)
        plague["death_carry"] = round(acc - p_death, 6)
        if p_death > 0:
            apply_population_loss(loc, p_death)
        plague["remain_days"] = int(plague.get("remain_days", 0)) - 1
        if plague["remain_days"] <= 0:
            del loc["plague"]

    # 8.3 黄汛每日扣减与死亡
    yf = loc.get("yellow_flood")
    if yf:
        acc = float(yf.get("death_carry", 0.0)) + float(yf.get("daily_death", 0) or 0.0)
        yf_death = int(acc)
        yf["death_carry"] = round(acc - yf_death, 6)
        if yf_death > 0:
            apply_population_loss(loc, yf_death)
        total_d = int(yf.get("total_days", 180))
        rem_d = int(yf.get("remain_days", 180)) - 1
        yf["remain_days"] = rem_d
        elapsed_d = total_d - rem_d
        yf["month_idx"] = elapsed_d // 30
        if rem_d <= 0:
            del loc["yellow_flood"]

    # 8.4 地震减产倒计时
    eq = loc.get("earthquake_penalty")
    if eq:
        eq["remain_days"] = int(eq.get("remain_days", 0)) - 1
        if eq["remain_days"] <= 0:
            del loc["earthquake_penalty"]
