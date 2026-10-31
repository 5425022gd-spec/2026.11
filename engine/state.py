"""世界状态：整个世界的唯一真相，可整体序列化为 JSON。

设计纪律：引擎的每一次推进都是 `next_state = tick(state, action)` 的纯函数形态，
状态之外不允许藏任何运行时事实。存档 = to_dict() 一个 JSON 文件；
调试 = 直接改 JSON；AI 上下文 = 从此渲染摘要。
"""

from __future__ import annotations

from typing import Any, Dict


def _last_variation_seq(entries) -> int:
    """从已登记的变化标签里取最大的 var-NNN 编号 老存档缺 variation_seq 时复原编号器。"""
    seq = 0
    for v in entries:
        sid = str(v.get("id", ""))
        if sid.startswith("var-") and sid[4:].isdigit():
            seq = max(seq, int(sid[4:]))
    return seq


def seed_department_modifiers(state) -> int:
    """把 world/department_modifiers.json 里的开局修正登记进统一修正。

    修正只有一个物种：这里写的是「显示位置 + 修正项目 + 修正范围 + 修正效果 + 介绍」，
    效果栏的文字**当场用词条表校验**，写法不在表里就直接报错（不许悄悄放过）。
    """
    import io
    import json as _json
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    path = _os.path.join(root, "world", "department_modifiers.json")
    if not _os.path.exists(path):
        return 0
    data = _json.loads(io.open(path, encoding="utf-8").read())
    from .operations import parse_department_modifier_content
    added = 0
    for m in data.get("department_modifiers", []):
        name = str(m.get("name") or "")
        if not name or name in state.department_modifiers:
            continue
        if m.get("effect_text"):
            effects, errors = parse_department_modifier_content(state, m["effect_text"])
            if errors:
                raise ValueError("开局修正【%s】的效果写错了：%s" % (name, "；".join(errors)))
        elif m.get("effect_desc"):
            # 展示型效果：只写一句质量描述，不挂任何词条（不参与数值计算）
            effects = [{"desc": str(m["effect_desc"])}]
        else:
            raise ValueError("开局修正【%s】既没有词条效果，也没有展示型效果" % name)
        state.department_modifiers[name] = {
            "name": name,
            "location": m.get("location", ""), "dept": m.get("location", ""),
            "scope": m.get("scope", "全国"), "intro": m.get("intro", ""),
            "content_str": m.get("effect_text", ""),
            "effects": effects,
            "summary": "，".join(e.get("desc", "") for e in effects),
        }
        added += 1
    return added


class WorldState:
    def __init__(self, world_data: Dict[str, Any]):
        start = world_data["start_date"]
        self.date: Dict[str, Any] = {
            "era": start["era"], "year": start["year"], "month": start["month"],
            "day": start.get("day", 1), "hour": start.get("hour", 0),
            "minute": start.get("minute", 0),
        }
        self.treasury: float = float(world_data.get("start_treasury", 240.0))  # 国库存银 万两
        self.privy_treasury: float = float(world_data.get("privy_treasury", 400.0))  # 私库存银 万两，开局400

        # ── 宏观与货币系统状态 ──
        self.m1: float = float(world_data.get("m1", 10000.0))                     # 市场流通白银 万两，开局10000
        self.local_reserve: float = float(world_data.get("local_reserve", 20000.0)) # 全国地方银库窖藏 万两，开局20000
        # 【v0.5.3】原 currency_type 货币制度/币制只是主界面一个显示格，不参与任何计算，已删除
        self.financial_rate_base: float = float(world_data.get("financial_rate", 0.15))  # 金融系数 f 的基础值
        self.financial_rate: float = self.financial_rate_base   # 实际生效值 = 基础值 + 科技效果 + 部门修正
        # 【v0.3.7】原 commodity_scale 商品流通规模 G不参与任何计算，只是界面 G 格的兜底值，已删；G 就是全国需求
        self.confidence_delta: float = float(world_data.get("confidence_delta", 0.20)) # 信心偏离率 Δλ
        # 物价指数固定基准 r₀ = 流通白银 / 全国物质需求开局值；物价指数 P_t = r_t / r₀，开局 P₀ = 1。
        # 0 表示尚未记录，首次计算物价指数时以当时的 r 作为开局基准补上 见 economy.calc_price_index。
        self.price_index_base: float = float(world_data.get("price_index_base", 0.0))
        self.confidence_acc: float = float(world_data.get("confidence_acc", 0.0))     # 信心偏离加速度 a
        self.national_market_stock: float = float(world_data.get("national_market_stock", 0.0)) # 全国市场未售物资
        self.religion_influences: Dict[str, float] = dict(world_data.get ("religion_influences", {"儒家": 60.0, "佛教": 20.0, "道教": 10.0, "民间宗教": 10.0}))

        # 教育与人才储备 万人
        self.local_educated_pop: float = float(world_data.get("local_educated_pop", 185.0))  # 地方受教育人口
        self.higher_educated_pop: float = float(world_data.get("higher_educated_pop", 15.0)) # 受高等教育人口
        # 【v0.3.5】礼部科举：受教育人口修正与受高等教育人口修正各一套 开局写在 world/education.json
        from .depts_rites import opening_local_edu_modifiers, opening_higher_edu_modifiers
        self.local_edu_modifiers: List[Dict[str, Any]] = opening_local_edu_modifiers()
        self.higher_edu_modifiers: List[Dict[str, Any]] = opening_higher_edu_modifiers()
        # 【v0.3.4】兵部月费：辽饷加派与驿站维持 原来只靠 getattr 默认值，state 里根本没定义，
        #            超控指令改的是 depts_war.LIAOXIANG_CONFIG，结算却永远走 41.67，属真 bug
        from .depts_war import LIAOXIANG_CONFIG, RELAY_CONFIG
        self.liaoxiang_monthly: float = float(world_data.get(
            "liaoxiang_monthly", LIAOXIANG_CONFIG.get("monthly_target", 41.67)))
        self.relay_monthly_cost: float = float(world_data.get(
            "relay_monthly_cost", RELAY_CONFIG.get("monthly_cost", 2.50)))
        self.exam_last: Dict[str, int] = {}          # 各场考试上次开考的游戏年
        # 人口自然变动修正 留接口：科技/超控往这里刷新，比例在 economy.population_rates 合并
        self.pop_rate_modifiers: List[Dict[str, Any]] = []
        # 【v0.3.7】部门修正 超控「创建部门修正」写入：改成开局就存在，
        # 不再由指令懒创建——否则外部要读这个字段时它可能根本不存在 接口对齐
        self.department_modifiers: Dict[str, Any] = dict(world_data.get("department_modifiers", {}))
        # 教育修正也是修正：开局就把两套教育修正登记进统一修正（显示位置＝礼部）
        from .depts_rites import seed_education_modifiers
        seed_education_modifiers(self)
        # 开局修正（world/department_modifiers.json）：词条写法在这里当场校验，写错就报错
        seed_department_modifiers(self)

        # 黄金储备体系 仅收录，不参与市场商品结算；流通黄金每月磨损 0.01%
        self.gold_treasury: float = float(world_data.get("gold_treasury", 100.0))       # 国库黄金 万两，大部分
        self.gold_local_reserve: float = float(world_data.get("gold_local_reserve", 25.0)) # 地方窖藏黄金 万两，少部分
        self.gold_m1: float = float(world_data.get("gold_m1", 1.5))                     # 流通黄金 万两，极少

        self.provinces: Dict[str, Dict[str, Any]] = {}
        for p in world_data["provinces"]:
            loc = p.get("local") or {}
            # 补齐阶级库存与市场状态
            cls = loc.get("classes") or {}
            for c_name, c_data in cls.items():
                c_data.setdefault("stock", 5.0)
                c_data.setdefault("famine_months", 0)
                c_data.setdefault("high_att_months", 0)
            loc.setdefault("local_market_stock", 0.0)

            self.provinces[p["id"]] = {
                "id": p["id"],
                "name": p["name"],
                "type": p.get("type", "province"),
                "owner": p.get("owner", "ming"),
                "neighbors": list(p.get("neighbors", [])),
                "local": loc,
            }
        # 宗教需求修正依赖完整省份表，必须在省份装载后初始化。
        from .depts_rites import calc_religion_summary
        from .localdata import under_ming_control
        rel_dem = round(float(calc_religion_summary(self).get("total_demand_pct") or 0.0), 4)
        for province in self.provinces.values():
            local = province.get("local") or {}
            if local.get("classes"):
                local["modifier_demand_pct"] = rel_dem if under_ming_control(province) else 0.0
        self.flags = set()                     # 已触发事件标记 保留接口
        self.pending: list = []                # 延迟效果 {year, month, effects[]}
        self.journal: list = []                # 台账/邸报条目 {month, text}
        self.cooldowns: Dict[str, int] = {}    # 动作id -> 剩余冷却月
        self.weather_plan: Any = None          # 季度天气计划 engine/weather.py 生成，随存档
        self.faction_ai: Dict[str, Any] = dict(world_data.get("faction_ai") or {})  # 势力 AI 季度决议 engine/faction_ai.py，随存档
        self.base_prestige: float = float(world_data.get("base_prestige", 60.0))        # 皇室威望开局基础值 (60.0)
        self.prestige_penalty: float = float(world_data.get("prestige_penalty", 0.0))  # 威望扣减累计
        self.prestige_bonus: float = float(world_data.get("prestige_bonus", 0.0))      # 威望奖励累计
        self.central_corruption: float = float(world_data.get("central_corruption", 50.0))  # 中央腐败程度 0~100
        self.national_ledger: list = list(world_data.get("national_ledger", []))       # 国库实际账单交易记录
        self.privy_ledger: list = list(world_data.get("privy_ledger", []))             # 私库实际账单交易记录
        self.pending_sacrifices: list = list(world_data.get("pending_sacrifices", []))  # 待次月1日刷新的灾害死亡事件清单
        self.sacrifices: list = list(world_data.get("sacrifices", []))                  # 礼部灾异祭祀清单与状态
        self.movement_modifiers: Dict[str, float] = dict(world_data.get("movement_modifiers", {}))  # 省际移动修正 默认1.0

        # 太仆寺常盈库 马价银：自成一本账，不做任何收支结算；但计入 M2 统计口径
        self.changying_treasury: float = float(world_data.get("changying_treasury", 70.0))  # 常盈库存银 万两，开局70
        self.changying_ledger: list = list(world_data.get("changying_ledger", []))          # 常盈库自身流水 年抽/马价支出
        self.changying_last_year: int = int(world_data.get("changying_last_year", 1627))    # 最近一次年抽所在的公历年份
        # 本公历年自地方窖藏抽解入常盈库的数额 万两：开局已逢公历1627年正月朔抽解，
        # 故开局即为本年度抽解额 50 万两，各省产能修正由它实时派生 见 depts_taipusi。
        self.changying_year_extract_wan: float = float(world_data.get("changying_year_extract_wan", 50.0))
        # 太仆寺马匹库存：马名齐全在这里补齐，读取处不得再 setdefault 补
        from .depts_taipusi import horse_names
        self.taipusi_horses: Dict[str, int] = {n: 0 for n in horse_names()}
        self.taipusi_horses.update(world_data.get("taipusi_horses") or {})

        # 【v0.4.1】以下字段原先各模块「谁用到谁懒建」，结果同一个字段有时存在有时不存在。
        # 与 department_modifiers 同理，改成开局一律声明，别处只许直接取用 不许 hasattr / getattr 兜底。
        from .depts_works import INITIAL_WORKS_INVENTORY
        self.works_inventory: Dict[str, int] = dict(INITIAL_WORKS_INVENTORY)  # 工部军械军需仓储库存
        # 变化池：子系统交上来的"变化"标签，下行广播给其它系统；池子只保留最近 POOL_LIMIT 条。
        # 登记口只有一个 engine/variations.record_variation，编号器与池子同放在 state，
        # 不再有 session.variation_pool / state.variation_pool 这种一个池子两个名字。
        self.variation_pool: list = []
        self.variation_seq: int = 0
        self.historical_variations: list = []          # 变化全量归档 设置界面按年月查阅
        self.pending_person_movements: list = []       # 在途官员调任 候旨→途中→到任
        self.pending_army_orders: list = []            # 在途军队调度
        # 近侍三格：每格 {name, phrases{应答/问寝/祭祀}}，口径见 engine/attendants.py
        self.attendants: list = []
        # 剧本事件标签：{标签: {"at": 时间键, "from": 事件id}}，口径见 engine/script_events.py
        self.script_tags: dict = {}
        # 已触发但还没被界面消费的剧本事件：[{mainline, event, at}]
        self.pending_script_events: list = []
        # 待答的近侍问话（问寝 / 祭祀）：{kind, slot, text, choices, date}
        self.attendant_prompt: dict = {}
        self.reject_test_mode: str = "auto"            # 六科封驳的测试模式 auto/always/never

        # 皇帝当前位置：紫禁城殿宇名，取值与 png/room.txt 的地图标注一致；开局在乾清宫
        self.emperor_location: str = str(world_data.get ("emperor_location", "乾清宫"))

        # 科技 见 engine/tech.py：科技点由任务/成就/主线奖励给 engine/tasks.grant_tech_points 是唯一发点口
        self.tech_points: float = float(world_data.get("tech_points", 10.0))
        self.tech_unlocked: list = list(world_data.get("tech_unlocked", []))
        # 任务 engine/tasks.py与成就 engine/achievements.py：一次性完成记录，只此一份
        self.tasks_done: list = list(world_data.get("tasks_done", []))
        self.achievements_done: list = list(world_data.get("achievements_done", []))
        # 主线结束状态与奖励记录 engine/mainline_script.py：{主线id: {ended, when, points}}
        self.mainline_state: Dict[str, Any] = dict(world_data.get("mainline_state", {}))

        for p in world_data.get("provinces", []):
            loc = self.provinces.get(p.get("id"), {}).get("local", {})
            cls_map = loc.get("classes") or {}
            for c_name, c_data in cls_map.items():
                if "pop_exact" not in c_data:
                    c_data["pop_exact"] = int(round(float(c_data.get("pop", 0)) * 10000))

        # 开局已解锁科技若有数值效果 存档续玩时也会走这里，金融替代率等按效果重算一次
        from .tech_effects import recompute as _recompute_tech_effects
        _recompute_tech_effects(self)

    def province(self, pid: str) -> Dict[str, Any]:
        return self.provinces[pid]

    def month_key(self) -> str:
        return "%d-%d" % (self.date["year"], self.date["month"])

    @property
    def m2(self) -> float:
        """广义货币总量 M2 = M1 + 国库 + 私库 + 地方银库 + 太仆寺常盈库。"""
        return round(self.m1 + self.treasury + self.privy_treasury
                     + self.local_reserve + self.changying_treasury, 2)

    @property
    def gold_total(self) -> float:
        """黄金储备总存量 = 国库黄金 + 地方窖藏黄金 + 流通黄金。"""
        return round(self.gold_treasury + self.gold_local_reserve + self.gold_m1, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "treasury": self.treasury,
            "privy_treasury": self.privy_treasury,
            "m1": self.m1,
            "local_reserve": self.local_reserve,
            "gold_treasury": self.gold_treasury,
            "gold_local_reserve": self.gold_local_reserve,
            "gold_m1": self.gold_m1,
            "financial_rate_base": self.financial_rate_base,
            "financial_rate": self.financial_rate,
            "confidence_delta": self.confidence_delta,
            "price_index_base": self.price_index_base,
            "confidence_acc": self.confidence_acc,
            "national_market_stock": self.national_market_stock,
            "local_educated_pop": self.local_educated_pop,
            "higher_educated_pop": self.higher_educated_pop,
            "local_edu_modifiers": self.local_edu_modifiers,
            "higher_edu_modifiers": self.higher_edu_modifiers,
            "exam_last": self.exam_last,
            "pop_rate_modifiers": self.pop_rate_modifiers,
            "liaoxiang_monthly": self.liaoxiang_monthly,
            "relay_monthly_cost": self.relay_monthly_cost,
            "provinces": self.provinces,
            "flags": sorted(self.flags),
            "pending": self.pending,
            "journal": self.journal,
            "cooldowns": self.cooldowns,
            "weather_plan": self.weather_plan,
            "faction_ai": self.faction_ai,
            "base_prestige": self.base_prestige,
            "prestige_penalty": self.prestige_penalty,
            "prestige_bonus": self.prestige_bonus,
            "central_corruption": self.central_corruption,
            "national_ledger": self.national_ledger,
            "privy_ledger": self.privy_ledger,
            "religion_influences": self.religion_influences,
            "pending_sacrifices": self.pending_sacrifices,
            "sacrifices": self.sacrifices,
            "movement_modifiers": self.movement_modifiers,
            "works_inventory": self.works_inventory,
            "changying_treasury": self.changying_treasury,
            "changying_ledger": self.changying_ledger,
            "changying_last_year": self.changying_last_year,
            "changying_year_extract_wan": self.changying_year_extract_wan,
            "taipusi_horses": self.taipusi_horses,
            "emperor_location": self.emperor_location,
            "tech_points": self.tech_points,
            "tech_unlocked": self.tech_unlocked,
            "tasks_done": self.tasks_done,
            "achievements_done": self.achievements_done,
            "mainline_state": self.mainline_state,
            "department_modifiers": self.department_modifiers,
            "reject_test_mode": self.reject_test_mode,
            "variation_pool": self.variation_pool,
            "variation_seq": self.variation_seq,
            "historical_variations": self.historical_variations,
            "pending_person_movements": self.pending_person_movements,
            "pending_army_orders": self.pending_army_orders,
            "attendants": self.attendants,
            "script_tags": self.script_tags,
            "pending_script_events": self.pending_script_events,
            "attendant_prompt": self.attendant_prompt,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorldState":
        ws = cls.__new__(cls)
        ws.date = dict(data["date"])
        ws.date.setdefault("day", 1)
        ws.date.setdefault("hour", 0)
        ws.date.setdefault("minute", 0)
        ws.treasury = float(data.get("treasury", 240.0))
        ws.privy_treasury = float(data.get("privy_treasury", 400.0))
        ws.m1 = float(data.get("m1", 10000.0))
        ws.local_reserve = float(data.get("local_reserve", 20000.0))
        ws.gold_treasury = float(data.get("gold_treasury", 100.0))
        ws.gold_local_reserve = float(data.get("gold_local_reserve", 25.0))
        ws.gold_m1 = float(data.get("gold_m1", 1.5))
        # 老存档没有 financial_rate_base：取存档里的 financial_rate 当基础值 属迁移，不是兜底
        ws.financial_rate_base = float(data.get("financial_rate_base",
                                                data.get("financial_rate", 0.15)))
        ws.financial_rate = ws.financial_rate_base
        ws.confidence_delta = float(data.get("confidence_delta", 0.20))
        ws.price_index_base = float(data.get("price_index_base", 0.0))
        ws.confidence_acc = float(data.get("confidence_acc", 0.0))
        ws.national_market_stock = float(data.get("national_market_stock", 0.0))
        ws.local_educated_pop = float(data.get("local_educated_pop", 185.0))
        ws.higher_educated_pop = float(data.get("higher_educated_pop", 15.0))
        # 【v0.3.5】两套分开存；v0.3.4 的合并字段 edu_modifiers按类型迁回各自那一套
        ws.local_edu_modifiers = list(data.get("local_edu_modifiers") or [])
        ws.higher_edu_modifiers = list(data.get("higher_edu_modifiers") or [])
        for m in (data.get("edu_modifiers") or []):
            nm = str(m.get("name", ""))
            if nm in  ("北京国子监", "南京国子监", "捐纳泛滥"):
                ws.higher_edu_modifiers.append(m)
            else:
                ws.local_edu_modifiers.append(m)
        if "local_edu_modifiers" not in data and "higher_edu_modifiers" not in data:
            # 老存档没有这两个字段：按开局值补上 属迁移，不是兜底
            from .depts_rites import opening_local_edu_modifiers, opening_higher_edu_modifiers
            ws.local_edu_modifiers = opening_local_edu_modifiers()
            ws.higher_edu_modifiers = opening_higher_edu_modifiers()
        ws.exam_last = {str(k): int(v) for k, v in (data.get("exam_last") or {}).items()}
        ws.pop_rate_modifiers = list(data.get("pop_rate_modifiers") or [])
        from .depts_war import LIAOXIANG_CONFIG as _LX, RELAY_CONFIG as _RL
        ws.liaoxiang_monthly = float(data.get("liaoxiang_monthly", _LX.get("monthly_target", 41.67)))
        ws.relay_monthly_cost = float(data.get("relay_monthly_cost", _RL.get("monthly_cost", 2.50)))

        ws.provinces = data["provinces"]
        for p in ws.provinces.values():
            loc = p.setdefault("local", {})
            # 旧存档迁移：核心情况「未控」已统一改称「非核」
            if loc.get("core") == "未控":
                loc["core"] = "非核"
            cls_map = loc.get("classes") or {}
            for c in cls_map.values():
                c.setdefault("stock", 5.0)
                c.setdefault("famine_months", 0)
                c.setdefault("high_att_months", 0)
                if "pop_exact" not in c:
                    c["pop_exact"] = int(round(float(c.get("pop", 0)) * 10000))
            loc.setdefault("local_market_stock", 0.0)

        ws.flags = set(data.get("flags", []))
        ws.pending = data.get("pending", [])
        ws.journal = data.get("journal", [])
        ws.cooldowns = data.get("cooldowns", {})
        ws.weather_plan = data.get("weather_plan")
        ws.faction_ai = dict(data.get("faction_ai") or {})
        ws.base_prestige = float(data.get("base_prestige", 60.0))
        ws.prestige_penalty = float(data.get("prestige_penalty", 0.0))
        ws.prestige_bonus = float(data.get("prestige_bonus", 0.0))
        ws.central_corruption = float(data.get("central_corruption", 50.0))
        ws.national_ledger = list(data.get("national_ledger", []))
        ws.privy_ledger = list(data.get("privy_ledger", []))
        ws.religion_influences = dict(data.get ("religion_influences", {"儒家": 60.0, "佛教": 20.0, "道教": 10.0, "民间宗教": 10.0}))
        ws.pending_sacrifices = list(data.get("pending_sacrifices", []))
        ws.sacrifices = list(data.get("sacrifices", []))
        ws.movement_modifiers = dict(data.get("movement_modifiers", {}))
        # 老存档没有 works_inventory：按开局值补上 属迁移，不是兜底
        if data.get("works_inventory") is not None:
            ws.works_inventory = dict(data["works_inventory"])
        else:
            from .depts_works import INITIAL_WORKS_INVENTORY
            ws.works_inventory = dict(INITIAL_WORKS_INVENTORY)
        ws.changying_treasury = float(data.get("changying_treasury", 70.0))
        ws.changying_ledger = list(data.get("changying_ledger", []))
        ws.changying_last_year = int(data.get("changying_last_year", 1627))
        ws.changying_year_extract_wan = float(data.get("changying_year_extract_wan", 50.0))
        from .depts_taipusi import horse_names as _horse_names
        ws.taipusi_horses = {n: 0 for n in _horse_names()}      # 老存档缺马名：属迁移，不是兜底
        ws.taipusi_horses.update(data.get("taipusi_horses") or {})
        ws.emperor_location = str(data.get("emperor_location") or "乾清宫")
        ws.tech_points = float(data.get("tech_points", 10.0))
        ws.tech_unlocked = list(data.get("tech_unlocked", []))
        ws.tasks_done = list(data.get("tasks_done", []))
        ws.achievements_done = list(data.get("achievements_done", []))
        ws.mainline_state = dict(data.get("mainline_state", {}))
        ws.department_modifiers = dict(data.get("department_modifiers", {}))
        from .depts_rites import seed_education_modifiers
        seed_education_modifiers(ws)      # 老存档里的两套教育修正一次性搬进统一修正
        seed_department_modifiers(ws)     # 开局修正补齐（老存档里没有的按开局补上）
        ws.reject_test_mode = str(data.get("reject_test_mode", "auto"))
        ws.variation_pool = list(data.get("variation_pool", []))
        ws.historical_variations = list(data.get("historical_variations", []))
        # 编号器是池子的一部分：老存档没写 variation_seq 时按已登记的 id 复原，不重置编号
        ws.variation_seq = int(data.get("variation_seq")
                               or _last_variation_seq(ws.variation_pool + ws.historical_variations))
        ws.pending_person_movements = list(data.get("pending_person_movements", []))
        ws.pending_army_orders = list(data.get("pending_army_orders", []))
        ws.attendants = list(data.get("attendants", []))
        ws.script_tags = dict(data.get("script_tags") or {})
        ws.pending_script_events = list(data.get("pending_script_events") or [])
        ws.attendant_prompt = dict(data.get("attendant_prompt", {}))
        from .attendants import ensure_state as _ensure_attendants
        _ensure_attendants(ws)            # 老存档补三格与默认用语
        # 读档后按「基础值 + 科技效果 + 部门修正」重算一次，防止存档里的旧派生值与效果分叉
        from .tech_effects import recompute as _recompute_tech_effects
        _recompute_tech_effects(ws)
        return ws
