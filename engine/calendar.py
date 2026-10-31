"""钦天监日历系统与大明礼仪常祀换算器。

功能：
1. 游戏简化历 每月 30 日，一年 360 日与真实公历双向精确换算。
2. 六十甲子天干地支纪日计算 基准：公历 1627-10-03 = 天启七年八月二十五日 = 戊午日。
3. 大明常祀 国祭日期计算：
   - 社稷坛 春祭/秋祭：每年农历二月、八月的上戊日 首个天干为“戊”的日子。
   - 山川坛 春祭/秋祭：每年农历二月、八月的 1 日 初一；若初一为上戊日则顺延至初二。
   - 雩祀：仲夏五月朔日。
   - 先农坛藉田礼：每年仲春二月上亥日 首个地支为“亥”的日子。
4. 日历标记接口 支持公历月历展示、常祀标记及后续事件扩展接口。
"""

import datetime
from typing import Any, Dict, List, Optional, Tuple

GAN = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸"]
ZHI = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]

# 开局基准：游戏简化历 1627-08-25 = 公历 1627-10-03  戊午日
BASE_GREG = datetime.date(1627, 10, 3)
BASE_GAME_DAYS = 1627 * 360 + 7 * 30 + 24
BASE_GANZHI_IDX = 54  # 戊午在六十甲子中为 54  0-indexed: 戊=4, 午=6


def game_to_gregorian(game_date: Dict[str, Any]) -> datetime.date:
    """游戏简化历 -> 对应公历日期。"""
    y = int(game_date["year"])
    m = int(game_date["month"])
    d = int(game_date.get("day", 1))
    cur_days = y * 360 + (m - 1) * 30 + (d - 1)
    delta = cur_days - BASE_GAME_DAYS
    return BASE_GREG + datetime.timedelta(days=delta)


def gregorian_to_game(greg_date: datetime.date) -> Dict[str, int]:
    """公历日期 -> 游戏简化历日期。"""
    delta = (greg_date - BASE_GREG).days
    cur_days = BASE_GAME_DAYS + delta
    year = cur_days // 360
    rem = cur_days % 360
    month = rem // 30 + 1
    day = rem % 30 + 1
    return {"year": year, "month": month, "day": day}


def get_day_ganzhi(greg_date: datetime.date) -> Tuple[str, int, int]:
    """获取指定公历日期的六十甲子干支名称、天干序号(0-9)与地支序号(0-11)。"""
    delta = (greg_date - BASE_GREG).days
    idx = (BASE_GANZHI_IDX + delta) % 60
    g_idx = idx % 10
    z_idx = idx % 12
    return GAN[g_idx] + ZHI[z_idx], g_idx, z_idx


def get_game_day_ganzhi(game_date: Dict[str, Any]) -> Tuple[str, int, int]:
    """获取游戏日期的干支。"""
    greg = game_to_gregorian(game_date)
    return get_day_ganzhi(greg)


# ── 大明四大常祀吉日计算 ──

def calc_annual_sacrifices(year: int) -> List[Dict[str, Any]]:
    """计算指定年份 游戏年大明常祀吉日列表。

    名目：
    1. 社稷坛 春：每年二月上戊日
    2. 山川坛 春：每年二月1日 若为上戊日则二月2日
    3. 先农坛藉田礼：每年仲春二月上亥日
    4. 雩祀：仲夏五月朔日
    5. 社稷坛 秋：每年八月上戊日
    6. 山川坛 秋：每年八月1日 若为上戊日则八月2日
    """
    sacrifices = []

    # 1. 仲春 二月常祀
    # 1.1 社稷坛 春上戊日
    sheji_spring_day = 1
    sheji_spring_greg = None
    sheji_spring_gz = ""
    for d in range(1, 31):
        g = game_to_gregorian({"year": year, "month": 2, "day": d})
        gz, gi, zi = get_day_ganzhi(g)
        if gi == 4:  # 戊
            sheji_spring_day = d
            sheji_spring_greg = g
            sheji_spring_gz = gz
            break

    # 1.2 山川坛 春
    shanchuan_spring_day = 2 if sheji_spring_day == 1 else 1
    shanchuan_spring_greg = game_to_gregorian({"year": year, "month": 2, "day": shanchuan_spring_day})
    shanchuan_spring_gz = get_day_ganzhi(shanchuan_spring_greg)[0]

    # 1.3 先农坛 二月上亥日
    xiannong_day = 1
    xiannong_greg = None
    xiannong_gz = ""
    for d in range(1, 31):
        g = game_to_gregorian({"year": year, "month": 2, "day": d})
        gz, gi, zi = get_day_ganzhi(g)
        if zi == 11:  # 亥
            xiannong_day = d
            xiannong_greg = g
            xiannong_gz = gz
            break

    # 2. 仲夏 五月常祀
    # 2.1 雩祀 五月1日
    yusi_greg = game_to_gregorian({"year": year, "month": 5, "day": 1})
    yusi_gz = get_day_ganzhi(yusi_greg)[0]

    # 3. 仲秋 八月常祀
    # 3.1 社稷坛 秋上戊日
    sheji_autumn_day = 1
    sheji_autumn_greg = None
    sheji_autumn_gz = ""
    for d in range(1, 31):
        g = game_to_gregorian({"year": year, "month": 8, "day": d})
        gz, gi, zi = get_day_ganzhi(g)
        if gi == 4:  # 戊
            sheji_autumn_day = d
            sheji_autumn_greg = g
            sheji_autumn_gz = gz
            break

    # 3.2 山川坛 秋
    shanchuan_autumn_day = 2 if sheji_autumn_day == 1 else 1
    shanchuan_autumn_greg = game_to_gregorian({"year": year, "month": 8, "day": shanchuan_autumn_day})
    shanchuan_autumn_gz = get_day_ganzhi(shanchuan_autumn_greg)[0]

    sacrifices.append({
        "id": f"sheji_spring_{year}",
        "name": "社稷坛春祭",
        "altar": "社稷坛",
        "season": "春",
        "rule": "仲春二月上戊日",
        "game_date": {"year": year, "month": 2, "day": sheji_spring_day},
        "game_date_str": f"二月初{sheji_spring_day}" if sheji_spring_day < 10 else f"二月{sheji_spring_day}日",
        "greg_date": sheji_spring_greg.strftime("%Y-%m-%d") if sheji_spring_greg else "",
        "ganzhi": sheji_spring_gz,
        "icon": "🌾",
        "desc": "太社太稷之祭，祈谷祈年，国之大事。",
    })

    sacrifices.append({
        "id": f"shanchuan_spring_{year}",
        "name": "山川坛春祭",
        "altar": "山川坛",
        "season": "春",
        "rule": "仲春二月初一，逢戊改初二",
        "game_date": {"year": year, "month": 2, "day": shanchuan_spring_day},
        "game_date_str": f"二月初{shanchuan_spring_day}",
        "greg_date": shanchuan_spring_greg.strftime("%Y-%m-%d"),
        "ganzhi": shanchuan_spring_gz,
        "icon": "⛰️",
        "desc": "祭五岳、四渎、名山大川及京师旗纛神。",
    })

    sacrifices.append({
        "id": f"xiannong_{year}",
        "name": "先农坛藉田礼",
        "altar": "先农坛",
        "season": "春",
        "rule": "仲春二月上亥日",
        "game_date": {"year": year, "month": 2, "day": xiannong_day},
        "game_date_str": f"二月初{xiannong_day}" if xiannong_day < 10 else f"二月{xiannong_day}日",
        "greg_date": xiannong_greg.strftime("%Y-%m-%d") if xiannong_greg else "",
        "ganzhi": xiannong_gz,
        "icon": "🚜",
        "desc": "皇帝躬耕藉田，劝农务本，享先农之神。",
    })

    sacrifices.append({
        "id": f"yusi_{year}",
        "name": "雩祀",
        "altar": "雩坛",
        "season": "夏",
        "rule": "仲夏五月初一",
        "game_date": {"year": year, "month": 5, "day": 1},
        "game_date_str": "五月初一",
        "greg_date": yusi_greg.strftime("%Y-%m-%d"),
        "ganzhi": yusi_gz,
        "icon": "🌧️",
        "desc": "仲夏大雩，百神应祈，甘霖调顺。",
    })

    sacrifices.append({
        "id": f"sheji_autumn_{year}",
        "name": "社稷坛秋祭",
        "altar": "社稷坛",
        "season": "秋",
        "rule": "仲秋八月上戊日",
        "game_date": {"year": year, "month": 8, "day": sheji_autumn_day},
        "game_date_str": f"八月初{sheji_autumn_day}" if sheji_autumn_day < 10 else f"八月{sheji_autumn_day}日",
        "greg_date": sheji_autumn_greg.strftime("%Y-%m-%d") if sheji_autumn_greg else "",
        "ganzhi": sheji_autumn_gz,
        "icon": "🌾",
        "desc": "报赛秋成，酬谢社稷之灵。",
    })

    sacrifices.append({
        "id": f"shanchuan_autumn_{year}",
        "name": "山川坛秋祭",
        "altar": "山川坛",
        "season": "秋",
        "rule": "仲秋八月初一，逢戊改初二",
        "game_date": {"year": year, "month": 8, "day": shanchuan_autumn_day},
        "game_date_str": f"八月初{shanchuan_autumn_day}",
        "greg_date": shanchuan_autumn_greg.strftime("%Y-%m-%d"),
        "ganzhi": shanchuan_autumn_gz,
        "icon": "⛰️",
        "desc": "秋报山川，安镇四方。",
    })

    return sacrifices


# ── 日历月度视图与事件标记接口 供前端弹窗与扩展 ──

def get_calendar_markers_map(greg_year: int, greg_month: int, state: Any = None) -> Dict[str, List[Dict[str, Any]]]:
    """获取指定公历年月的所有日期标记表 key 为 YYYY-MM-DD。

    预置包含：
    1. 常祀吉日标记
    2. 留接口：扩展历史事件、地震、节令等标记。
    """
    markers_map: Dict[str, List[Dict[str, Any]]] = {}

    # 获取关联的可能年份 跨年处理
    game_years = {greg_year - 1, greg_year, greg_year + 1}
    for gy in game_years:
        for sac in calc_annual_sacrifices(gy):
            g_str = sac.get("greg_date")
            if g_str and g_str.startswith(f"{greg_year:04d}-{greg_month:02d}"):
                markers_map.setdefault(g_str, []).append({
                    "type": "regular_sacrifice",
                    "id": sac["id"],
                    "title": sac["name"],
                    "name": sac["name"],
                    "icon": sac["icon"],
                    "desc": f"{sac['rule']}，{sac['desc']}",
                    "altar": sac["altar"],
                })

    # 留接口：历史事件与地震标记
    from .weather import HISTORICAL_EARTHQUAKES
    for eq in HISTORICAL_EARTHQUAKES:
        eq_y, eq_m, eq_d = eq["date"]
        if eq_y == greg_year and eq_m == greg_month:
            g_str = f"{eq_y:04d}-{eq_m:02d}-{eq_d:02d}"
            markers_map.setdefault(g_str, []).append({
                "type": "earthquake",
                "title": f"{eq['location']}地震",
                "name": f"{eq['location']}{eq['magnitude']}级大地震",
                "icon": "⚡",
                "desc": f"历史重大震灾，震级 {eq['magnitude']}，损毁房舍，伤亡黎庶。",
            })

    # 科举考试开考日 乡试/会试/殿试，间隔相对上次考试，见 depts_rites.exam_rows
    if state is not None:
        try:
            from .depts_rites import exam_rows
            for ex in exam_rows(state):
                d = ex["next_greg"]
                if d["year"] == greg_year and d["month"] == greg_month:
                    g_str = "%04d-%02d-%02d" % (d["year"], d["month"], d["day"])
                    markers_map.setdefault(g_str, []).append({
                        "type": "exam",
                        "id": ex["id"],
                        "title": ex["name"],
                        "name": ex["name"],
                        "icon": "🖋",
                        "desc": f"开设科目：{ex['subjects']}。{ex['note']}",
                    })
        except Exception:
            pass

    return markers_map


def get_calendar_month_data(greg_year: int, greg_month: int, state: Any = None) -> Dict[str, Any]:
    """生成指定公历月份的完整日历矩阵 供前端日历组件直接渲染。"""
    from .time import date_cn

    # 该月首日与下月首日
    first_day = datetime.date(greg_year, greg_month, 1)
    if greg_month == 12:
        next_month = datetime.date(greg_year + 1, 1, 1)
    else:
        next_month = datetime.date(greg_year, greg_month + 1, 1)

    total_days = (next_month - first_day).days
    start_weekday = first_day.weekday()  # 0=周一, 6=周日

    markers_map = get_calendar_markers_map(greg_year, greg_month, state)

    days_list = []
    for day_num in range(1, total_days + 1):
        cur_date = datetime.date(greg_year, greg_month, day_num)
        date_str_key = cur_date.strftime("%Y-%m-%d")
        game_d = gregorian_to_game(cur_date)
        gz, _, _ = get_day_ganzhi(cur_date)
        lunar_str = date_cn(game_d)

        day_markers = markers_map.get(date_str_key, [])

        days_list.append({
            "greg_date": date_str_key,
            "day": day_num,
            "weekday": cur_date.weekday(),
            "game_date": game_d,
            "game_date_str": lunar_str,
            "lunar_month_day": f"{game_d['month']}月{game_d['day']}日",
            "ganzhi": gz,
            "markers": day_markers,
            "is_today": False,
        })

    # 标记游戏当前日期对应的公历日
    if state and hasattr(state, "date"):
        cur_greg = game_to_gregorian(state.date)
        cur_key = cur_greg.strftime("%Y-%m-%d")
        for d_item in days_list:
            if d_item["greg_date"] == cur_key:
                d_item["is_today"] = True

    return {
        "year": greg_year,
        "month": greg_month,
        "start_weekday": start_weekday,
        "total_days": total_days,
        "days": days_list,
    }
