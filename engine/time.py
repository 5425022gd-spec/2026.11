"""时间推进：时辰/日/月推进 + 延迟效果结算 + 月度自然漂移。

纪律：时间是状态变量，归引擎所有。AI 未来接入时从账本里读时间，
永不发明时间。date 结构：{era, year, month(1-12), day(1-30), hour(0-23)}，
每月简化按 30 日计，时辰为 12 时辰制 两小时一时辰。
"""

from typing import Any, Dict, List

from .effects import apply_one
from .economy import tick_economy_monthly

MONTH_NAMES = ["", "正月", "二月", "三月", "四月", "五月", "六月",
               "七月", "八月", "九月", "十月", "冬月", "腊月"]
MONTH_DAYS = 30  # 简化历法：每月 30 日
SHICHEN = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]


def era_of(year):
    """公元纪年 -> 中国古纪年 天启七年=1627，崇祯元年=1628。

    时间推进以公元纪年 year为基准计算，古纪年只作平行显示。
    与 cn_era(同义 中文数字纪年，保留两个名字以兼容旧调用。)
    """
    return cn_era(year)


def month_name(date: Dict[str, Any]) -> str:
    return "%s %s" % (era_of(date["year"]), MONTH_NAMES[date["month"]])


def shichen(hour: int) -> str:
    """hour(0-23) -> 时辰名。子时=23:00-1:00 跨日，简化取 (hour+1)//2。"""
    return SHICHEN[(hour + 1) // 2 % 12]


# ── 中文纪年显示 古纪年 + 公历双显示──

CN_NUM = ["", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
          "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九",
          "二十", "二十一", "二十二", "二十三", "二十四", "二十五", "二十六",
          "二十七", "二十八", "二十九", "三十"]


def cn_num(n: int) -> str:
    """1-30 中文数字 日期用。"""
    if 1 <= n <= 30:
        return CN_NUM[n]
    return str(n)


def cn_year_num(n: int) -> str:
    """年号纪年数字：1 为「元」，其余中文。"""
    if n == 1:
        return "元"
    if n <= 60:
        return cn_num(n)
    return str(n)


def cn_era(year: int) -> str:
    """公元纪年 -> 中文古纪年年号 天启七年 / 崇祯元年 / 万历四十七年…。"""
    if year >= 1628:
        return "崇祯%s年" % cn_year_num(year - 1627)
    if year >= 1621:
        return "天启%s年" % cn_year_num(year - 1620)
    if year == 1620:
        return "泰昌元年"
    if year >= 1573:
        return "万历%s年" % cn_year_num(year - 1572)
    if year >= 1567:
        return "隆庆%s年" % cn_year_num(year - 1566)
    return "嘉靖%s年" % cn_year_num(year - 1521)


def date_cn(date: Dict[str, Any]) -> str:
    """古纪年完整日期：天启七年八月二十五日。"""
    return "%s%s%s日" % (cn_era(date["year"]), MONTH_NAMES[date["month"]],
                         cn_num(date["day"]))


# 开局基准：游戏简化历 1627-08-25 天启七年八月二十五日
# = 公历 1627-10-03 世界观：崇祯于是日登基
_GREG_BASE_GAME_DAYS = 1627 * 360 + 7 * 30 + 24   # 简化历天数 8月25日
_GREG_BASE = None  # datetime.date(1627,10,3)，惰性导入


def greg_date(date: Dict[str, Any]):
    """简化历 → 公历日期对象 按开局基准偏移；简化历每月 30 日。

    公历换算只此一处：`date_greg()` 与主线结束条件 如「时间大于 1644年1月1日」都调它。
    """
    import datetime
    global _GREG_BASE
    if _GREG_BASE is None:
        _GREG_BASE = datetime.date(1627, 10, 3)
    delta = (date["year"] * 360 + (date["month"] - 1) * 30 + (date["day"] - 1)
             - _GREG_BASE_GAME_DAYS)
    return _GREG_BASE + datetime.timedelta(days=delta)


def date_greg(date: Dict[str, Any]) -> str:
    """公历显示：按开局基准偏移换算 简化历每月 30 日。"""
    g = greg_date(date)
    return "公历%d年%d月%d日" % (g.year, g.month, g.day)


def date_str(date: Dict[str, Any]) -> str:
    return "%s %s %d日 %s时" % (
        era_of(date["year"]), MONTH_NAMES[date["month"]], date["day"],
        shichen(date["hour"]))


def date_key(date: Dict[str, Any]) -> str:
    """YYYY-MM-DD 格式时间戳 提示词/批阅用。"""
    return "%04d-%02d-%02d" % (date["year"], date["month"], date["day"])


def date_shift_key(date: Dict[str, Any], days: int) -> str:
    """date 偏移 days 天 正=未来，负=过去，返回 YYYY-MM-DD。

    简化历法：每年 360 日，每月 30 日；不早于 0000-01-01。
    """
    y, m, d = date["year"], date["month"], date["day"]
    total = max(0, y * 360 + (m - 1) * 30 + (d - 1) + days)
    ny, rem = divmod(total, 360)
    nm, nd = divmod(rem, 30)
    return "%04d-%02d-%02d" % (ny, nm + 1, nd + 1)


def days_between(key1: str, key2: str) -> int:
    """简化历日期差 YYYY-MM-DD，返回 key2 - key1 的天数。"""
    def total(key):
        y, m, d = map(int, key.split("-"))
        return y * 360 + (m - 1) * 30 + (d - 1)
    return total(key2) - total(key1)


def random_date_before(date: Dict[str, Any], max_days: int = 60):
    """随机生成该日期之前 0~max_days 天的日期 YYYY-MM-DD。

    用于填充章奏的「事件发生时间」 送达时间取生成当日。
    简化历法：每月 30 日。
    """
    import random
    days = random.randint(0, max_days)
    return date_shift_key(date, -days)


def advance_minute(state, n: int = 10) -> List[str]:
    """推进 n 分钟；跨小时进位 进位顺带跨日跨月。"""
    n = max(0, int(n))
    cur_m = int(state.date.get("minute", 0))
    total = cur_m + n
    state.date["minute"] = total % 60
    hours = total // 60
    if hours > 0:
        advance_hour(state, hours)
    return []


def time_str_24h(date: Dict[str, Any]) -> str:
    """24小时制时间字符串：HH:MM。"""
    h = int(date.get("hour", 0))
    m = int(date.get("minute", 0))
    return "%02d:%02d" % (h, m)


def advance_hour(state, n: int = 1) -> List[str]:
    """推进 n 小时；跨日进位。不结算月度逻辑。"""
    n = max(0, n)
    total = state.date.get("hour", 0) + n
    state.date["hour"] = total % 24
    days = total // 24
    if days:
        advance_day(state, days)
    return []


def advance_day(state, n: int = 1) -> List[str]:
    """推进 n 日；跨月进位 每月 30 日。"""
    n = max(0, n)
    total = state.date["day"] - 1 + n
    state.date["day"] = total % MONTH_DAYS + 1
    months = total // MONTH_DAYS
    for _ in range(months):
        _bump_month(state)
    return []


def _bump_month(state) -> None:
    """仅历法进月 不结算月度逻辑，供日推进跨月时使用。"""
    state.date["month"] += 1
    if state.date["month"] > 12:
        state.date["month"] = 1
        state.date["year"] += 1
        state.date["era"] = era_of(state.date["year"])  # 古纪年随公元年自动调整


def advance_month(state) -> List[str]:
    """推进一个月，返回本月亮点 结算/漂移的人话清单。"""
    notes: List[str] = []

    # 朝廷月费 让银子成为约束
    state.treasury = max(0.0, state.treasury - 2.0)
    notes.append ("朝廷月费 2.0万两")

    # 历法推进
    _bump_month(state)
    state.date["day"] = 1
    state.date["hour"] = 0

    # 延迟效果结算 pending queue
    due = [e for e in state.pending
           if e["year"] == state.date["year"] and e["month"] == state.date["month"]]
    state.pending = [e for e in state.pending if e not in due]
    for e in due:
        for ef in e["effects"]:
            notes.append(apply_one(state, ef))

    # 冷却递减
    for k in list(state.cooldowns):
        state.cooldowns[k] -= 1
        if state.cooldowns[k] <= 0:
            del state.cooldowns[k]

    # 月度新经济系统自然结算 物质循环 + 货币循环
    eco_notes = tick_economy_monthly(state)
    notes += eco_notes
    return notes
