# -*- coding: utf-8 -*-
"""大明王朝 1627 变化池 Variation Pool与变量动态静态映射中心。

核心设计：
1. 变化类型有限枚举：气象灾害、黄汛决口、地震、疠疫、军队欠饷、边防民乱、工程完工/损毁、官员大计/贪腐、法度废弛、外交贸易、衙署变动等。
2. 静态映射操作变量：每种变化产生时，硬编码绑定该类型所允许使用的 1~3 条局部超控接口 Scoped Operations。
3. 提示词分阶段精炼注入：
   - zhixian.txt：仅发送带有 variation_id 的简明变化清单 极低 Token；
   - zhixian_2.txt：仅注入与该 variation_id 绑定的 Scoped Operations 规范文本；
   - 引擎安全执行：严格限定 AI 只能操作被绑定的变量范围，防越权与幻觉。
"""

from typing import Any, Dict, List, Optional, Tuple
import re

# ═══════════════════════════════════════════════════════════
# 一、 变化类型与硬编码操作变量映射注册表
# ═══════════════════════════════════════════════════════════

VARIATION_SCHEMA_MAP: Dict[str, Dict[str, Any]] = {
    "court_directive": {
        "name": "中旨",
        "allowed_commands": [],
        "schema_text": "【该变化仅作记录，不绑定地方超控指令】",
    },
    # 变化池为空时给 AI 的三条地方风闻：不绑定任何超控范围，只作题材
    "local_rumor": {
        "name": "地方风闻",
        "allowed_commands": [],
        "schema_text": "【该条为地方风闻，不绑定任何系统超控指令；可作章奏题材，不得据此改动数值】",
    },
    # 1. 极端自然灾害 旱灾/水涝/高温/严寒/台风
    "disaster_weather": {
        "name": "自然灾害与气象异常",
        "allowed_commands": ["创建修正", "删除修正", "新建灾害建筑", "删除税目", "增加税目", "拨付国库到地方", "增加地方各阶级人口", "减少地方各阶级人口"],
        "schema_text": """
【允许的系统超控指令】 只能针对本省/相关灾务执行：
1. 创建修正 <省份> <修正名称> 本省 全国所有阶级产能+5% <介绍>  例: 创建修正 {province} 旱荒抚恤 本省 全国所有阶级产能+5% 蠲赈并行
2. 删除修正 <修正名称>  例: 删除修正 旱荒抚恤
3. 新建灾害建筑 <省份> <名称> <修正> <介绍> <造价万两> <工期月数>  例: 新建灾害建筑 {province} 抗旱机井群 全国所有阶级产能+3% 备旱 10 6
4. 删除税目 <税名>  例: 删除税目 夏税
5. 拨付国库到地方 <省份> <万两>  拨库银赈济灾黎
6. 减少地方各阶级人口 <省份> <阶级> <人数>  灾异死伤人口扣减
""".strip()
    },

    # 2. 黄河决溢与汛情
    "disaster_yellow_flood": {
        "name": "黄河汛情与大水决口",
        "allowed_commands": ["创建修正", "删除修正", "新建灾害建筑", "删除工程", "拨付国库到地方", "减少地方各阶级人口"],
        "schema_text": """
【允许的系统超控指令】 只能针对黄河流域治水防汛执行：
1. 新建灾害建筑 <省份> <名称> <修正> <介绍> <造价万两> <工期月数>  例: 新建灾害建筑 {province} 顺黄石堤 全国所有阶级产能+4% 御黄汛 24 12
2. 删除工程 <省份> <工程名称>  销号彻底损毁的大堤
3. 创建修正 <省份> <修正名称> 本省 全国所有阶级产能+4% <介绍>
4. 拨付国库到地方 <省份> <万两>  拨发堵口防汛银
""".strip()
    },

    # 3. 历史地震
    "disaster_earthquake": {
        "name": "地动灾异",
        "allowed_commands": ["创建修正", "删除修正", "删除工程", "拨付国库到地方", "减少地方各阶级人口"],
        "schema_text": """
【允许的系统超控指令】 只能针对地震震区善后执行：
1. 创建修正 <省份> <修正名称> 本省 全国所有阶级产能+2% <介绍>  例: 创建修正 {province} 震后结庐安民 本省 全国所有阶级产能+2% 搭棚栖止
2. 拨付国库到地方 <省份> <万两>  拨发国库存银抚恤压伤军民
3. 减少地方各阶级人口 <省份> <阶级> <人数>
""".strip()
    },

    # 4. 疠疫流行
    "disaster_plague": {
        "name": "疫病流行",
        "allowed_commands": ["创建修正", "删除修正", "地方基建程度", "减少地方各阶级人口", "拨付国库到地方"],
        "schema_text": """
【允许的系统超控指令】 只能针对疫区辟疫善后执行：
1. 创建修正 <省份> <修正名称> 本省 全国所有阶级产能+3% <介绍>  例: 创建修正 {province} 设局施药 本省 全国所有阶级产能+3% 广施汤药
2. 地方基建程度 <省份> <正负数>  例: 地方基建程度 {province} 3
3. 减少地方各阶级人口 <省份> <阶级> <人数>
""".strip()
    },

    # 5. 军队粮饷与欠饷
    "military_arrears": {
        "name": "军队粮饷与军费开支",
        "allowed_commands": ["修改粮饷加派", "拨付国库到地方", "增加地方各阶级态度", "降低地方各阶级态度"],
        "schema_text": """
【允许的系统超控指令】 只能针对兵饷筹措与犒赏执行：
1. 修改粮饷加派 <月征数额万两>  例: 修改粮饷加派 45.0
2. 拨付国库到地方 <省份> <万两>  调拔国库银补发驻军欠饷
3. 增加地方各阶级态度 <省份> <阶级> <点数>
""".strip()
    },

    # 6. 敌情起义与边防战事
    "military_defense_war": {
        "name": "边防战守与剿捕反乱",
        "allowed_commands": ["删除军队", "增加武职", "删除武职", "更换武官", "创建修正", "删除修正", "皇室威望"],
        "schema_text": """
【允许的系统超控指令】 只能针对战事与将校任免执行：
1. 删除军队 <省份> <军队名称>  例: 删除军队 {province} 王二部起义军
2. 增加武职 <省份或区域> <武职名称>  例: 增加武职 {province} 宁塞营守备
3. 删除武职 <省份或区域> <武职名称>
4. 更换武官 <省份或区域> <武职名称> <原名> <新名>
5. 皇室威望 <正负数>  例: 皇室威望 -5
""".strip()
    },

    # 7. 地方工程与矿场营建
    "works_project": {
        "name": "地方工矿营建与开采",
        "allowed_commands": ["新建工程建筑", "删除工程", "地方基建程度"],
        "schema_text": """
【允许的系统超控指令】 只能针对地方工矿与关防设施执行：
1. 新建工程建筑 <省份> <名称> <修正> <介绍> <造价万两> <工期月数>  例: 新建工程建筑 {province} 新修堡隘 全国所有阶级产能+2% 扼守要冲 10 6
2. 删除工程 <省份> <工程名称>
3. 地方基建程度 <省份> <正负数>  例: 地方基建程度 {province} 2
""".strip()
    },

    # 8. 吏治贪腐、大计考察与言官弹劾
    "official_personnel": {
        "name": "吏治考核与官员任免",
        "allowed_commands": ["更换文官", "增加文职", "删除文职", "创建修正", "删除修正", "地方腐败程度", "中央腐败程度"],
        "schema_text": """
【允许的系统超控指令】 只能针对人事任免与吏治纪律执行：
1. 更换文官 <省份> <官职> <原官名> <新官名>  例: 更换文官 {province} 知府 原知府名 新选官名
2. 增加文职 <省份> <官职>  由逻辑引擎接入随机官员
3. 删除文职 <省份> <官职>
4. 创建修正 吏部 <修正名> 全国 中央腐败程度-5% <介绍>  例: 创建修正 吏部 严核考成 全国 中央腐败程度-5% 大计京察
5. 删除修正 <修正名>  废除一条现行行政纪律
6. 地方腐败程度 <省份> <正负数> / 中央腐败程度 <正负数>
""".strip()
    },

    # 9. 司法刑狱与大案
    "justice_law": {
        "name": "国家法典与条例审谳",
        "allowed_commands": ["创建修正", "修改修正", "删除修正", "中央腐败程度", "皇室威望"],
        "schema_text": """
【允许的系统超控指令】 只能针对刑部法律与司法体统执行：
1. 修改修正 <修正项目> <修正效果> <介绍>  例: 修改修正 《问刑条例》 中央腐败程度+3% 加重惩贪
2. 删除修正 <修正项目>  正式废止一条现行律法
3. 中央腐败程度 <正负数>  例: 中央腐败程度 -3
4. 皇室威望 <正负数>  例: 皇室威望 3
""".strip()
    },

    # 10. 外交关系与对外贸易
    "diplomacy_trade": {
        "name": "藩属朝贡与对外贸易",
        "allowed_commands": ["修改外交关系", "创建修正", "皇室威望"],
        "schema_text": """
【允许的系统超控指令】 只能针对外交与文教执行：
1. 修改外交关系 <国名> <关系:藩属/和平/战争> <贸易状态:贸易/禁贸>  例: 修改外交关系 朝鲜 藩属 贸易
2. 创建修正 礼部 <修正名> 全国 受教育人口年增+5% <介绍>  例: 创建修正 礼部 各府设义学 全国 受教育人口年增+5% 广设义学
3. 创建修正 礼部 <修正名> 全国 受高等教育人口年增+3% <介绍>
""".strip()
    },

    # 11. 衙署设立、裁撤与现任管理层
    "dept_reorg": {
        "name": "中枢及外朝衙署建制与现任管理层",
        "allowed_commands": ["增加部门", "删除部门", "更换中央官员", "增加部门职位", "删除部门职位", "皇室威望"],
        "schema_text": """
【允许的系统超控指令】 只能针对二十四衙门、六部与外廷诸司管理层执行：
1. 增加部门 <外廷/内廷> <部门名称>  例: 增加部门 外廷 军器总督司
2. 删除部门 <部门名称>  例: 删除部门 惜薪司
3. 更换中央官员 <部门名称> <官职或原官> <新官>  例: 更换中央官员 司礼监 司礼监掌印太监 王承恩
4. 增加部门职位 <部门名称> <官职>  例: 增加部门职位 兵仗局 军械监理太监
5. 删除部门职位 <部门名称> <官职>  例: 删除部门职位 兵仗局 军械监理太监
""".strip()
    }
}


def normalize_variation_type(vtype: str, text: str = "") -> str:
    """把一条变化归到标准类别。

    判定顺序（玩家口径）：**先看正文的具体事实，再看调用方给的粗类型**。
    调用方（天气、驿站、铨选、祭祀、势力 AI）只知道自己属于哪个子系统，传的是
    "disaster" / "military" / "official" 这种粗名；若让粗名先命中，祭祀会被记成
    自然灾害、调兵会被记成欠饷。所以文本里能认出来的具体事件优先。
    """
    v = (vtype or "").lower()
    t = text or ""

    # 0) 已经是标准类型名：直接认
    if v in VARIATION_SCHEMA_MAP:
        return v

    # 1) 文本里的具体事件优先
    if "黄汛" in t or "决口" in t:
        return "disaster_yellow_flood"
    if "地震" in t:
        return "disaster_earthquake"
    if "瘟疫" in t or "大疫" in t or "小疫" in t:
        return "disaster_plague"
    if any(k in t for k in ("起义", "起事", "叛军", "后金", "入寇", "哗变", "营啸",
                            "调兵", "令箭", "军令", "开拔", "进驻", "战备", "交战")):
        return "military_defense_war"
    if any(k in t for k in ("欠饷", "军饷", "补发")):
        return "military_arrears"
    if any(k in t for k in ("工程", "竣工", "兴工", "矿", "水利", "修筑")):
        return "works_project"
    if any(k in t for k in ("大明律", "条例", "三法司", "案卷", "审谳")):
        return "justice_law"
    if any(k in t for k in ("祭祀", "礼成", "贸易", "藩属", "朝贡", "遣使", "教育")):
        return "diplomacy_trade"
    if any(k in t for k in ("衙署", "增设", "裁撤", "并省")):
        return "dept_reorg"
    if any(k in t for k in ("旱", "涝", "寒潮", "热浪", "台风", "降雪", "暴雨")):
        return "disaster_weather"
    if any(k in t for k in ("巡抚", "知府", "道员", "御史", "到任", "卸任", "弹劾", "贪墨", "新官")):
        return "official_personnel"

    # 2) 文本认不出来，再看调用方的粗类型
    if "yellow_flood" in v:
        return "disaster_yellow_flood"
    if "earthquake" in v:
        return "disaster_earthquake"
    if "plague" in v:
        return "disaster_plague"
    if "disaster" in v or "weather" in v:
        return "disaster_weather"
    if "military" in v or "army" in v:
        return "military_arrears"
    if "works" in v or "project" in v:
        return "works_project"
    if "official" in v:
        return "official_personnel"
    if "law" in v or "case" in v:
        return "justice_law"
    if "rites" in v or "trade" in v or "diplomacy" in v:
        return "diplomacy_trade"
    if "dept" in v:
        return "dept_reorg"
    if v in ("zhongzhi", "court_directive"):
        return "court_directive"

    raise ValueError("未知变化类型：%s；无法从文本归一化：%s" % (vtype or "<空>", text or "<空>"))


POOL_LIMIT = 60     # 变化池只保留最近这么多条；全量归档见 state.historical_variations


def record_variation(state: Any, vtype: str, text: str, province: str = "") -> Dict[str, Any]:
    """登记一条"变化"标签：分配编号、入池 保留最近 POOL_LIMIT 条、入全量归档。

    全局唯一的登记口。子系统 天气、驿站、铨选、祭祀……只能从这里上交标签，
    再经 format_variations_for_prompt(下行广播给其它系统；编号器与池子都在 state 上，)
    不存在"一个池子挂两个名字"或"两套编号"。
    """
    from .time import date_key
    state.variation_seq += 1
    cur_date = state.date
    entry = {
        "id": "var-%03d" % state.variation_seq,
        "type": normalize_variation_type(vtype, text),
        "text": text,
        "province": province or "",
        "date": date_key(cur_date),
        "year": int(cur_date.get("year", 1627)),
        "month": int(cur_date.get("month", 8)),
        "day": int(cur_date.get("day", 1)),
    }
    state.variation_pool.append(entry)
    if len(state.variation_pool) > POOL_LIMIT:
        del state.variation_pool[:-POOL_LIMIT]     # 原地裁剪，不重新绑定，免得又出一个名字
    state.historical_variations.append(entry)
    return entry


def get_scoped_operations_schema(vtype: str, province: str = "") -> str:
    """获取指定变化类型绑定的 Scoped Operations 说明文本。"""
    if vtype not in VARIATION_SCHEMA_MAP:
        raise ValueError ("未知变化类型：%s" % vtype)
    raw_text = VARIATION_SCHEMA_MAP[vtype]["schema_text"]
    return raw_text.replace ("{province}", province or "地方")


def recent_variations(state: Any, days: int = 30) -> List[Dict[str, Any]]:
    """变化池里发生在最近 days 天以内的变化点 唯一一处滑动窗口口径。

    章奏生成要"从近期变化清单里挑一条"，界面与提示词都只看这个窗口；
    日期缺省或解析不出来的条目按"算近期"处理 宁可给模型看，也不悄悄丢掉。
    """
    from .time import days_between
    from .session import date_key

    cur = date_key(state.date) if state is not None and getattr(state, "date", None) else "1627-08-25"
    out: List[Dict[str, Any]] = []
    for v in (getattr(state, "variation_pool", None) or []):
        v_date = v.get("date")
        if not v_date:
            out.append(v)
            continue
        try:
            diff = days_between(v_date, cur)
        except Exception:
            out.append(v)
            continue
        if 0 <= diff <= days:
            out.append(v)
    return out


def format_variations_for_prompt(variations: List[Dict[str, Any]]) -> str:
    """将变化池中的有效变化标签事件格式化为提示词清单。"""
    if not variations:
        return "（当前无变化）"
    lines = []
    for v in variations:
        vid = v.get("id") or "var-000"
        txt = v.get("text") or "地方民政异动"
        lines.append(f"[{vid}] {txt}")
    return "\n".join(lines)
