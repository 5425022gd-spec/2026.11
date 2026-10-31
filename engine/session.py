"""游戏会话：一条命令进、一组文本出的解释器。

终端 main.py与网页 server.py共用同一份命令逻辑，
保证两套界面行为完全一致。会话持有世界状态与最近条陈缓存。
"""

import json
import os
import random
import time
from typing import Dict, Any, List, Optional

from .state import WorldState
from .variations import record_variation
from .time import (advance_month, advance_day, advance_hour, advance_minute, date_str, month_name,
                   date_key, date_shift_key, days_between, time_str_24h)
from .llm import LLMClient, parse_jsonl
from .diagnose import run_diagnose
from .reports import build_report
from .memorials import (generate_daily, generate_filler, generate_side,
                        read_prompt, extract_history, travel_days, pick_memorial_writer, _stamp)
from .weather import init_weather, tick_weather
from .logger import CONSOLE
from . import localdata
from .officials import (list_active, remove_official, schedule_successor,
                        check_appointments, tick_tenures, month_key_str)
from .council import Council
from .depts_rites import calc_prestige
from .factions import evaluate_memorial_decision, get_cabinet_majority_faction, get_silijian_faction
from .operations import execute_operation, OPERATIONS_DOC

WORLD_DIR = "world"
SAVE_FILE = "save.json"


def load_world():
    def jp(*parts):
        return os.path.join(WORLD_DIR, *parts)

    with open(jp("provinces.json"), encoding="utf-8") as f:
        return json.load(f)


def extract_history(state) -> str:
    """提取最近一个月的世界剧情记录。"""
    journal = getattr(state, "journal", [])
    if not journal:
        return " 天下承平，往期无特别灾乱异动"
    recent_1 = journal[-1]
    m_str = recent_1.get("month", "")
    t_str = recent_1.get("text", "")
    return f"【{m_str}天下大势】\n{t_str}"


def extract_history(state, months=None) -> str:
    """提取世界历史。

    months=None 取自开局以来的全部；给 1 / 6 / 12 则取最近 N 个月。
    月度台账 journal 一条一个月，按「月」截取，不做日级细分。
    """
    journal = getattr(state, "journal", [])
    if not journal:
        return " 天下承平，往期无特别灾乱异动"
    rows = journal if months is None else journal[-int(months):]
    lines = []
    for j in rows:
        m_str = j.get("month", "")
        t_str = j.get("text", "")
        lines.append (f"【{m_str}天下大势】\n{t_str}")
    return "\n\n".join(lines)


def extract_history_1(state) -> str:
    """过去一个月的世界历史。"""
    return extract_history(state, 1)


def extract_history_6(state) -> str:
    """过去六个月的世界历史。"""
    return extract_history(state, 6)


def extract_history_12(state) -> str:
    """过去十二个月的世界历史。"""
    return extract_history(state, 12)


def extract_history_all(state) -> str:
    """自开局以来归档的全部世界历史。"""
    return extract_history(state, None)


class GameSession:
    def __init__(self):
        self.world = load_world()
        self.llm = LLMClient()
        self.layout = self.world["map_layout"]
        self.ids = [p["id"] for p in self.world["provinces"]]
        self.last_proposals = None
        self.quit = False
        self.state = WorldState(self.world)
        self.boot_lines = self._boot()
        # 开局世界总结 data/opening_world.txt：作为默认 {{history}} 与"世界总结"归档
        self.opening_history = self._load_opening_history()
        # 章奏：未处理列表 + 已归档历史；开局仅两模板各 2 份填充
        self.memorials = []
        self.memorial_history = []
        # 快照历史：供章奏空位替换 {{x月x日国库数据}}
        self.snapshots = []
        self.record_snapshot()
        # 召集系统 群策群力
        self.council = Council(self)
        # 对话归档 召对/廷议/世界总结：随单个存档存取
        self.dialogues = []
        # 战报归档：开局就是空表 原来属性要到第一次交付战报才出现，接口取它时属性不存在
        self.battle_reports = []
        self.unread_battle_reports = False
        self.pending_battle_reports = []
        # 事件树：主线剧本 AI 工作流的对话记录 见 engine/script_workflow.py。
        # 每条：{id, mainline_id, title, created, note, script, log}
        # 章奏回复的 AI 已删除，事件树不再记章奏升级，只记剧本工作流。
        self.script_sessions = []
        # 事件 ID 分配器：{层: 序号}，n=不重要 m=次要 l=主要 如 n-1、m-3、l-1
        self.event_seq = {"n": 0, "m": 0, "l": 0}
        # 变化池：见 engine/variations.record_variation，池子与编号器都在 state 上
        # 凌晨批处理状态：{last: 最近执行日, next_side: 下次支线生成日}
        # 每天凌晨 0 点：抽不重要事件 + 支线生成 间隔 1-5 天+ 每月一号月度总结
        self.dawn_state = {"last": date_key(self.state.date),
                           "next_side": self._first_side_date()}
        # 地方天气初始化 每省气候基准 + 初始天气状态
        init_weather(self.state)
        self._seed_opening_history()
        self.refresh_memorials(counts={"mainline": 0, "side": 0, "filler": 4})

    def next_event_id(self, layer):
        """分配唯一事件 ID：{层}-{序号} 如 n-1 / m-2 / l-1。"""
        self.event_seq[layer] = self.event_seq.get(layer, 0) + 1
        return "%s-%d" % (layer, self.event_seq[layer])

    # ---- 开局 ----
    def _load_opening_history(self):
        """读取开局世界总结文本 data/opening_world.txt。

        文件本身用【…】把整段包起来，展示时不再需要外层括号，读入即剥掉。
        """
        path = os.path.join("data", "opening_world.txt")
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
        except OSError:
            return ""
        if text.startswith("【"):
            text = text[1:]
        if text.endswith("】"):
            text = text[:-1]
        return text.strip()

    def _seed_opening_history(self):
        """开局默认 {{history}}：把世界总结注入台账 month=开局，不计入月度邸报，
        并归档为"世界总结"对话记录 对话查阅 > 世界总结。"""
        if not self.opening_history:
            return
        if not any(j.get("month") == "开局" for j in self.state.journal):
            self.state.journal.insert(0, {"month": "开局", "text": self.opening_history})
        if not any(d.get("category") == "world" for d in self.dialogues):
            self.save_dialogue(None, "世界总结 开局 天启七年",
                               self.opening_history, [], category="world")

    def _boot(self):
        return ["== 天启七年 八月 开局 =="]

    def _first_side_date(self):
        """首次支线生成日：开局首日不出支线。

        第一次凌晨批处理发生在开局日的次日 简化历 08-26，公历 1627-10-04，
        故把 next_side 初值再往后放一天，使该日只出不重要事件与主线事件；
        支线自 08-27 起按原有 1~5 天间隔正常生成。
        """
        return date_shift_key(self.world["start_date"], 2)

    # ---- 界面元素 ----
    def header(self):
        return [self._topbar(), self._render_map(), self._legend()]

    def _topbar(self):
        return "【%s】国库 %.1f万两" % (month_name(self.state.date), self.state.treasury)

    def _render_map(self):
        rows = []
        for row in self.layout:
            cells = []
            for pid in row:
                if not pid:
                    cells.append("   ")
                    continue
                p = self.state.provinces[pid]
                cells.append(p["name"] + " ")
            rows.append(" ".join(cells))
        return "\n".join(rows)

    def _legend(self):
        return "  ".join("%d.%s" % (i + 1, self.state.provinces[pid]["name"])
                         for i, pid in enumerate(self.ids))

    # ---- 命令解释 ----
    def run(self, cmd: str):
        cmd = (cmd or "").strip()
        if not cmd:
            return []
        parts = cmd.split()
        c = parts[0].lower()
        if c not in ("h", "help", "?"):
            CONSOLE.log("cmd", "玩家指令", cmd)

        if c in ("q", "quit", "exit"):
            self.quit = True
            return ["圣驾回銮。"]
        if c in ("h", "help", "?"):
            return self._help()
        if c == "diag":
            return run_diagnose(self.llm)
        # 召集期间锁定：不得使用其他 AI 功能/主动操作 仅可查阅数据
        if self.council.active and c not in ("mem", "view", "officials",
                                             "q", "h", "?", "diag"):
            return ["召集进行中：仅可查阅数据 mem/view/officials。"
                    "其他操作已锁定，请先结束对话。"]
        if c == "mem":
            return self._mem_list()
        if c == "view":
            return self._mem_view(parts)
        if c == "read":
            return self._mem_read(parts)
        if c == "reply":
            return self._mem_reply(parts)
        if c == "officials":
            return self._officials_list()
        if c in  ("罢免", "remove_official"):
            return self._remove_official(parts)
        if c in  ("sacrifice", "祭祀" and len(parts) > 1):
            from .depts_rites import start_sacrifice
            ok, msg = start_sacrifice(self.state, parts[1])
            return [msg]
        if c == "j":
            return self._advance(parts)
        if c == "w":
            return self._polish()
        if c == "s":
            return self._save()
        if c == "l":
            return self._load()
        if c == "new":
            return self._new_game()
        if c == "op" and len(parts) > 1:
            op_text = cmd[2:].strip()
            res = execute_operation(self.state, op_text)
            return [res]
        if c.isdigit():
            idx = int(c) - 1
            if 0 <= idx < len(self.ids):
                return self._province_text(self.ids[idx])
            return ["查无此省。"]
        if c in self.ids:
            return self._province_text(c)
        
        # 尝试作为 20 类正负操作指令解析执行
        op_res = execute_operation(self.state, cmd)
        if op_res and not op_res.startswith("执行通用指令：") and op_res != "空指令":
            return [op_res]
        return ["不明旨意。输入 help 查看命令。"]

    def _help(self):
        return [
            "命令：",
            "  数字 / 省id    查看省份详情，如 1 或 shaanxi",
            "  w              润色本月邸报 文案层示例",
            "  j [Nm] [Nd] [Nh]  推进时间：j / j 3 / j 2m 5d 3h 月/日/时",
            "  mem            查看当日章奏列表",
            "  view <id>      阅读章奏正文",
            "  read <id>      已阅 归档",
            "  reply <id> <回折>   撰写回折",
            "  diag           校验 AI 连接 HTTP 连通 / 聊天 / 工具请求",
            "  new            重开新局",
            "  s / l          存盘 / 读盘",
            "  q              退出",
        ]

    def _province_text(self, pid):
        """省份详情：按玩家清单的词条顺序逐项列出——
        核心情况 / 腐败 / 地方人口 / 地方阶级 / 基建程度 /
        地方工程 / 可承载人口 / 天气情况。
        镇军与军队 机动部队已移至军队名册；地形接口已删除。
        派生值 人口/税收见 localdata，不另加词条与括号注释。"""
        p = self.state.provinces[pid]
        kind = "京师" if p["type"] == "capital" else ("省" if localdata.under_ming_control(p) else "敌国")
        lines = ["【%s】%s" % (p["name"], kind)]
        loc = p.get("local") or {}
        from .modifiers import get_province_local_modifiers
        live = get_province_local_modifiers(self.state, pid) if loc else None
        if loc:
            # 1) 核心情况 国家认同的统称
            lines.append("核心情况: %s" % (loc.get("core", "?")))
            # 2) 腐败
            lines.append("腐败: %d" % loc.get("corruption", 0))
            # 3) 地方人口 = 各阶级人口总和，每月 +0.1%
            lines.append("地方人口: %.1f 万" % localdata.population(loc))
            # 4) 地方阶级 每阶级：人口/态度/物质库存/产能/需求/扶持
            cls = loc.get("classes") or {}
            lines.append ("地方阶级:")
            for name in localdata.CLASSES:
                c = cls.get(name)
                if not c:
                    continue
                pop_val = float(c.get("pop") or 0)
                cap = localdata.class_capacity(name, pop_val, loc, live)
                dem = localdata.class_demand(name, pop_val, float(loc.get("modifier_demand_pct") or 0.0))
                lines.append("  %s: 人口 %.1f万 | 态度 %d | 物质库存 %.1f万单位 | "
                             "产能 %.1f万单位/月 | 需求 %.1f万单位/月 | 扶持 %d两/月"
                             % (name, pop_val, c.get("attitude", 0),
                                c.get("stock", 0.0), cap, dem, c.get("support", 0)))
            lines.append("物质产出: %.1f 万单位/月 | 物质需求: %.1f 万单位/月 | 供需指数: %.2f"
                         % (localdata.material_production(loc, live),
                            localdata.material_demand(loc),
                            localdata.supply_demand_ratio(loc, live)))
            # 5) 基建程度
            lines.append("基建程度: %d" % loc.get("infra", 0))
            # 6) 地方工程 列出地方的大型工程，包括要塞、矿山等
            projs = loc.get("projects") or []
            if projs:
                lines.append ("地方工程:")
                for x in projs:
                    lines.append("  %s %s %s %s"
                                 % (x.get("name", ""), x.get("kind", ""),
                                    x.get("status", ""), x.get("note", "")))
            # 7) 可承载人口 当前科技及经济形式下的自然承载力
            lines.append("可承载人口: %.1f 万" % localdata.carrying(loc, self.state))
            # 8) 天气情况与灾害/瘟疫/黄汛/地震 独立算法，见 weather.py
            w = loc.get("weather") or {}
            plague = loc.get("plague") or {}
            yf = loc.get("yellow_flood") or {}
            eq = loc.get("earthquake_penalty") or {}
            if w:
                dis = "，%s 剩余%d日" % (w["disaster"], w["disaster_remain"]) if w.get("disaster") else ""
                plg = "，【%s】剩余%d日 产能-%d%%" % (plague["name"], plague["remain_days"], int(plague["penalty"] * 100)) if plague.get("remain_days", 0) > 0 else ""
                yf_txt = "，【%s】剩余%d日 首月-%d%%" % (yf["name"], yf["remain_days"], int(yf["initial_penalty"] * 100)) if yf.get("remain_days", 0) > 0 else ""
                eq_txt = "，【%s】剩余%d日 产能-%d%%" % (eq["name"], eq["remain_days"], int(eq["penalty"] * 100)) if eq.get("remain_days", 0) > 0 else ""
                lines.append("天气情况: %s %d℃%s%s%s%s" % (w.get("condition", "?"), w.get("temp", 0), dis, plg, yf_txt, eq_txt))

            # 9) 地区修正与全域 Buff 汇编 涵盖军队欠饷、六部、灾害与机制修正
            from .modifiers import get_province_all_modifiers
            mods = get_province_all_modifiers(self.state, pid)
            mil = mods.get("military", {})
            dept = mods.get("departments", {})
            wea = mods.get("weather_disaster", {})
            proj_mod = mods.get("projects_and_mines", {})
            sd = mods.get("supply_demand", {})

            mod_lines = ["地区修正:"]
            lx = mil.get("liaoxiang", {})
            arr = mil.get("army_arrears", {})
            if lx.get("active") or arr.get("active"):
                lx_txt = f"辽饷月分摊 {lx.get('allocated_silver_monthly', 0.0):.2f}万两({lx.get('penalty_desc', '无')})" if lx.get("active") else "辽饷无分摊"
                arr_txt = f"驻军欠饷 {arr.get('total_arrears_wan', 0.0):.2f}万两({arr.get ('penalty_desc', '无')})" if arr.get("active") else "驻军军饷充足"
                mod_lines.append (f"  军饷防务: {lx_txt} | {arr_txt}")

            corr_info = dept.get("corruption", {})
            carry_info = dept.get("carrying", {})
            rel_info = dept.get("religion", {})
            tax_info = dept.get("taxation", {})
            mod_lines.append(f"  六部政纪: 吏治腐败 {corr_info.get('value', 0.0):.1f}基准50 {corr_info.get('pct_sum_display', '+0%')}) | 承载力 {carry_info.get('pct_sum_display', '+0%')} | {rel_info.get ('summary_desc', '宗教常恒')} | 户部月税 {tax_info.get('monthly_tax_total', 0.0):.2f}万两")

            if wea.get("total_disaster_penalty_pct", 0) > 0:
                d_parts = []
                if wea.get("disaster", {}).get("active"): d_parts.append(wea["disaster"]["desc"])
                if wea.get("plague", {}).get("active"): d_parts.append(wea["plague"]["desc"])
                if wea.get("yellow_flood", {}).get("active"): d_parts.append(wea["yellow_flood"]["desc"])
                if wea.get("earthquake", {}).get("active"): d_parts.append(wea["earthquake"]["desc"])
                mod_lines.append(f"  灾异受损: {'；'.join(d_parts)} 合计减产 -{wea.get('total_disaster_penalty_pct', 0)*100:.1f}%)")

            mit_list = [m["effect"] for m in proj_mod.get("disaster_mitigations", [])]
            if mit_list:
                mod_lines.append(f"  防灾水利: {'；'.join(mit_list)}")
            if proj_mod.get("total_annual_silver", 0) > 0 or proj_mod.get("total_annual_gold", 0) > 0:
                mod_lines.append(f"  矿场采收: 课税白银 {proj_mod.get('total_annual_silver', 0):.1f}万两/年 采收黄金 {proj_mod.get('total_annual_gold', 0):.0f}两/年")

            mod_lines.append(f"  供求民心: 供求比 {sd.get('ratio', 1.0):.2f} ({sd.get ('status_name', '平衡')}) {sd.get('drift_text', '')}")
            lines.extend(mod_lines)
        nbs = [self.state.provinces[n]["name"] for n in p["neighbors"]]
        lines.append("邻接: %s" % "、".join(nbs))
        return lines

    def _polish(self):
        entries = [j["text"] for j in self.state.journal
                   if j["month"] == self.state.month_key()]
        if not entries:
            return ["本月尚无台账可润色。"]
        if self.llm.mock:
            return ["【邸报润色 mock，配置 LLM_API_KEY 后为 AI 润色】",
                    "" + "；".join(entries)]
        data = self.llm.chat_json(
            "你是明末邸报主笔，把干巴巴的政令记录润色成文言邸报文风，只输出 JSON。",
            "本月台账：\n" + "\n".join(entries),
            {"type": "object", "properties": {"polished": {"type": "string"}}},
        )
        return [data.get("polished", "")]

    def _advance(self, parts=None):
        """推进时间。格式：
        j          -> 1 个月 兼容旧用法
        j 3        -> 3 个月
        j 2m 5d 3h -> 2 个月 5 日 3 小时 组合推进
        """
        months = days = hours = 0
        if parts and len(parts) > 1:
            for tok in parts[1:]:
                try:
                    if tok.endswith("m"):
                        months = max(0, min(int(tok[:-1]), 60))
                    elif tok.endswith("d"):
                        days = max(0, min(int(tok[:-1]), 60))
                    elif tok.endswith("h"):
                        hours = max(0, min(int(tok[:-1]), 60))
                    else:
                        months = max(0, min(int(tok), 60))  # 纯数字按月
                except ValueError:
                    pass
        else:
            months = 1

        lines = []
        before_key = date_key(self.state.date)
        # 先推进小时，再日，再月 日进位会顺带进位
        advance_hour(self.state, hours)
        advance_day(self.state, days)
        for _ in range(months):
            notes = advance_month(self.state)
            lines += build_report(self.state, notes).split("\n")
            lines.append("")
        if not lines:
            lines.append (" 时间推进：无月度结算")
        # 地方天气逐日滚动 按推进经过的天数
        elapsed = days_between(before_key, date_key(self.state.date))
        # 太仆寺常盈库年抽与产能刷新 公历每年 1 月 1 日零点，按已过天数回填判断
        from .depts_taipusi import tick_changying_annual
        for cy in tick_changying_annual(self.state, elapsed):
            lines.append(cy)
        weather_events = tick_weather(self.state, elapsed)
        for we in weather_events:
            if we.get("type") == "earthquake":
                lines.append("【地动灾异】%s发生 %.2f 级大地震 死伤 %d 人，全省下月产能 -%d%%"
                             % (we["location"], we["magnitude"], we["deaths"], int(we["penalty"] * 100)))
                record_variation(self.state, "disaster", "【%s】发生%.2f级地震 死伤%d人，产能-%d%%" % (we["province"], we["magnitude"], we["deaths"], int(we["penalty"] * 100)), province=we["province"])
            elif we.get("type") == "yellow_flood":
                lines.append("【黄河决口】%s爆发%s 首月产能 -%d%%，持续 %d 日，预计死伤 %d 人"
                             % (we["province"], we["name"], int(we["penalty"] * 100), we["remain_days"], we.get("total_death", 0)))
                record_variation(self.state, "disaster", "【%s】爆发%s 首月产能-%d%%" % (we["province"], we["name"], int(we["penalty"] * 100)), province=we["province"])
            elif we.get("type") == "plague":
                lines.append("【疠疫流行】%s爆发%s 强度 %d，持续 %d 日，全阶级产能 -%d%%"
                             % (we["province"], we["name"], we.get("severity", 50), we["remain_days"], int(we["penalty"] * 100)))
                record_variation(self.state, "plague", "【%s】爆发%s 产能-%d%%" % (we["province"], we["name"], int(we["penalty"] * 100)), province=we["province"])
            else:
                lines.append("【%s】%s 强度 %d" % (we["province"], we["disaster"], we["severity"]))
                record_variation(self.state, "weather", "【%s】发生%s 强度 %d" % (we["province"], we["disaster"], we["severity"]), province=we["province"])

        # 礼部灾异祭祀每日推进与超时结算
        from .depts_rites import tick_sacrifices_daily
        for _ in range(elapsed if elapsed > 0 else 0):
            sac_events = tick_sacrifices_daily(self.state)
            for se in sac_events:
                if se["type"] == "sacrifice_completed":
                    lines.append("【礼仪祭祀】%s %s祭典告成，%s影响力相对值 +1%% 现为 %.1f"
                                 % (se["title"], se["religion"], se["target_religion"], se["new_influence"]))
                    record_variation(self.state, "rites", "【祭祀告成】%s %s礼成，%s影响力提升"
                                       % (se["title"], se["religion"], se["target_religion"]))
                elif se["type"] == "sacrifice_expired":
                    lines.append("【礼仪祭祀】%s %s逾期未祭，皇室威望损失 -%.3f 点！"
                                 % (se["title"], se["religion"], se["penalty"]))
                    record_variation(self.state, "rites", "【祭祀逾期】%s %s超时未祭，威望受损"
                                       % (se["title"], se["religion"]))

        # 【v0.3.6】阶级叛乱：态度归零的阶级每天 1% 概率起事 见 engine/rebellion.py
        from .rebellion import tick_rebellion_daily
        for rb in tick_rebellion_daily(self.state):
            lines.append(rb)
            record_variation(self.state, "military", rb)

        # 军队行军每日推进与抵达
        from .armies import tick_armies_daily
        army_notes = tick_armies_daily(self.state, elapsed)
        for an in army_notes:
            lines.append(an)
            record_variation(self.state, "military", an)

        # 势力 AI：本季度决议里到日的活动逐日发令 后金等；见 engine/faction_ai.py
        from .faction_ai import tick_faction_ai_daily
        for fn in tick_faction_ai_daily(self.state, elapsed):
            lines.append(fn)
            record_variation(self.state, "diplomacy", fn)

        # 驿站调度：角色在途逐省推进与军队令箭送达
        # 变化标签由 relay 自己在事件发生处登记 唯一登记口 record_variation，
        # 这里只把驿报文字并进推进日志，不再照文字二次登记一遍。
        from .relay import tick_movements_and_relays_daily
        lines += tick_movements_and_relays_daily(self.state, elapsed)
        # 官员就任检查 罢免后继任者到期上任
        appointed = check_appointments(month_key_str(self.state))
        if appointed:
            CONSOLE.log("action", "新官就任 %d 名" % appointed,
                        " 前任被罢免后的继任者到任")
            record_variation(self.state, "official", "新官到任 %d 员" % appointed)
        # 官员任期届满更替：任止已到 -> 卸任，随机生成继任者立即接任
        changes = tick_tenures(month_key_str(self.state))
        for ch in changes:
            lines.append("【吏部铨选】%s %s%s任期届满卸任，%s 接任。"
                         % (ch["old"], ch["province"], ch["title"], ch["new"]))
            CONSOLE.log("action", "官员更替 %s -> %s" % (ch["old"], ch["new"]),
                        "%s %s任止 %s，随机铨选继任" % (ch["title"], ch["province"],
                                                       ch["old_end"]))
            record_variation(self.state, "official", "%s %s%s任期届满卸任，由 %s 接任" % (ch["old"], ch["province"], ch["title"], ch["new"]), province=ch["province"])
        # 吏部铨选补官：缺员槽按月补授。补官发生在时间推进里，不在读界面时
        from .positions import fill_vacancies
        for f in fill_vacancies(month_key_str(self.state)):
            lines.append("【吏部铨选】%s %s缺员，补授 %s。"
                         % (f["category"], f["title"], f["person"]))
            CONSOLE.log("action", "铨选补官 %s" % f["person"],
                        "%s %s 缺员补授" % (f["category"], f["title"]))
            record_variation(
                self.state, "official",
                "【铨选】%s%s缺员，补授 %s" % (f["category"], f["title"], f["person"]),
                province="" if f["kind"] == "京官" else f["category"])
        # 记录数据快照 供章奏空位替换
        self.record_snapshot()
        # 近侍问话：每天 20:00 一次（廷议进行中则不打扰）
        lines += self._attendant_night_check()
        # 凌晨批处理：跨日后执行 每天最多一次
        dawn_lines = self._dawn_events()
        lines += dawn_lines
        lines.insert(0, "【%s】" % date_str(self.state.date))
        CONSOLE.log("cmd", "时间推进",
                    "%d月 %d日 %d时 -> %s 新章奏 %d 份"
                    % (months, days, hours, date_str(self.state.date),
                       len(self.memorials)))
        return lines

    # ---- 近侍问寝：每天 20:00 一次 ----
    def _attendant_night_check(self):
        """到了晚上八点、廷议没在进行、今天还没问过，就让近侍问一句要不要就寝。"""
        lines = []
        from . import attendants as _att
        d = self.state.date
        if int(d.get("hour", 0)) < 20:
            return lines
        today = "%04d-%02d-%02d" % (d["year"], d["month"], d["day"])
        if (self.state.attendant_prompt or {}).get("date") == today:
            return lines
        if getattr(self, "council", None) and getattr(self.council, "active", False):
            return lines
        slot = _att.primary_slot(self.state)
        who = (_att.slots(self.state)[slot].get("name") or "")
        if not who:
            return lines
        text = _att.phrase(self.state, "问寝", slot)
        self.state.attendant_prompt = {
            "kind": "问寝",
            "slot": slot,
            "name": who,
            "text": text,
            "date": today,
            "choices": [{"key": "rest", "label": "就寝"},
                        {"key": "later", "label": "再批一会"}],
        }
        lines.append("【近侍】%s：%s" % (who, text))
        CONSOLE.log("attendant", "近侍问寝", "%s：%s" % (who, text))
        return lines

    # ---- 凌晨 0 点批处理 每天一次----
    def _dawn_events(self):
        """每天凌晨 0 点执行 推进跨日后触发，一天最多一次：

        0. 结算上一批未处理的章奏 根据司礼监掌印太监与内阁党派判定；
        1. 从不重要事件模板库随机抽取 10-20 个不重要事件 n 层；
        2. 支线生成：到下次支线日则随机生成 1-5 个次要事件 m 层，从变化池抽取事件点；
        3. 每月一号凌晨：月度总结 Prompt/zongjie.txt，AI 撰写；
        4. 事件树：到期追踪事件推演续报 以2结尾。
        """
        today = date_key(self.state.date)
        if self.dawn_state.get("last") == today:
            return []
        self.dawn_state["last"] = today
        lines = []
        side_count = 0

        # 0) 未处理章奏结算
        for m in self.memorials:
            if m.get("status") == "new":
                cab_f = get_cabinet_majority_faction(self.state)
                sili_f = get_silijian_faction(self.state)
                rew = ""
                from .operations import apply_reward_text
                prov_hint = m.get("Location") or m.get("province") or "shaanxi"
                if cab_f == sili_f:
                    if m.get("decision_type") == "processed":
                        rew = m.get("reward_processed", "")
                        CONSOLE.log("mem", "未处理结算", "司礼监与内阁同党，依议触发处理奖励：%s -> %s" % (m.get("title", ""), rew))
                    else:
                        rew = m.get("reward_unprocessed", "")
                        CONSOLE.log("mem", "未处理结算", "司礼监与内阁同党，留中触发消极影响：%s -> %s" % (m.get("title", ""), rew))
                    if rew:
                        apply_reward_text(self.state, rew, default_province=prov_hint)
                else:
                    CONSOLE.log("mem", "未处理结算", "司礼监与内阁意见不合，产生权力掣肘惩罚：%s" % m.get("title", ""))
                m["status"] = "archived"

        # 每日凌晨批处理时：彻底清除所有已阅/已批/归档章奏，目录中只保留未处理的新章奏
        self.memorials = [m for m in self.memorials if m.get("status") == "new"]

        # 1) 不重要事件 n 层：模板库随机抽取 10-20 份 模板少则重复抽取
        n = random.randint(10, 20)
        filler = generate_filler(n, state=self.state, id_fn=self.next_event_id)
        self.memorials = filler + self.memorials
        lines.append ("通政司进呈本章 %d 份。" % n)
        # 2) 次要事件 m 层：到支线日则生成 1-5 份，间隔 1-5 天
        next_side = self.dawn_state.get("next_side")
        if not next_side or today >= next_side:
            side_count = random.randint(1, 5)
            side = generate_side(self.state, self.llm, count=side_count,
                                 id_fn=self.next_event_id)
            self.memorials = side + self.memorials
            self.dawn_state["next_side"] = date_shift_key(
                self.state.date, random.randint(1, 5))
            lines.append("支线奏报 %d 份 下次支线 %s。"
                         % (side_count, self.dawn_state["next_side"]))
        # 3.5) 主线要紧事物事件触发与推进 剧本事件曝光、延时影响
        self._tick_mainline_events(today)

        # 3.55) 任务判定 数值达标即发科技点与主线结案、西北大灾阶段刷新
        from .tasks import evaluate as _evaluate_tasks
        for rec in _evaluate_tasks(self.state):
            lines.append("【任务达成】%s：奖励科技点 %g。" % (rec["name"], rec["reward_points"]))
        lines += self._tick_mainlines()

        # 3.6) 每日各省军队攻防对抗与战事裁判推进
        from .battle import check_and_trigger_daily_battles
        daily_battles = check_and_trigger_daily_battles(self.state)
        if daily_battles:
            if not hasattr(self, "pending_battle_reports") or self.pending_battle_reports is None:
                self.pending_battle_reports = []
            # 存入待交付队列，于次日凌晨 1 点交付战报
            for b in daily_battles:
                self.pending_battle_reports.append(b)
                for ln in b.get("loss_notes", []):
                    lines.append(ln)

        # 检查是否到了交付战报的时间 次日凌晨 1 点送达
        self._deliver_pending_battle_reports()

        # 3) 每月一号凌晨：月度总结与工程建造扣费
        monthly = False
        if self.state.date["day"] == 1:
            monthly = True
            from .operations import tick_construction_monthly, apply_flow_modifiers_monthly
            c_notes = tick_construction_monthly(self.state)
            for cn in c_notes:
                lines.append(cn)
            # 白银/黄金修正词条：凭空生成，白银进国库、黄金进私库，按月折算
            for fn in apply_flow_modifiers_monthly(self.state):
                lines.append(fn)
            summary = self._monthly_summary()
            lines.append ("【月度总结】%s" % summary)
        # 5) 主线要紧事物判定
        self._tick_mainlines()
        CONSOLE.log("cmd", "凌晨批处理",
                    "%s：不重要 %d 份%s%s"
                    % (today, n,
                       "，支线 %d 份" % side_count if side_count else "",
                       "，月度总结" if monthly else ""))
        return lines

    def _deliver_pending_battle_reports(self):
        """检查并投递次日凌晨 1 点到达的军情战报。"""
        if not hasattr(self, "pending_battle_reports") or not self.pending_battle_reports:
            return
        if not hasattr(self, "battle_reports") or self.battle_reports is None:
            self.battle_reports = []

        # 将已积攒的战斗结果转入正式战报归档，并点亮红点
        for b in self.pending_battle_reports:
            self.battle_reports.insert(0, b)
        self.pending_battle_reports = []
        self.unread_battle_reports = True
        CONSOLE.log("war", f"战报于凌晨送达通达枢密", f"新增战报 {len(self.battle_reports)} 份，已归档。")

    def _tick_mainline_events(self, today: str):
        """推进并触发要紧事物剧本事件，记录到 occurred_events，并处理延时影响。

        【v0.5.0】不再分阶段：事件集就是整份剧本的 `m["events"]`，
        触发基准日是 `script_start_date` 首次推进那天。
        触发写法兼容「在剧本开始后N天」与旧写法「在阶段开始后N天」。
        """
        from .mainline_script import load_mainlines_data, save_mainlines_data
        from .operations import execute_operation_command
        from .time import days_between
        import re

        data = load_mainlines_data()
        changed = False

        for m in data.get("mainlines", []):
            if m.get("status") == "completed":
                continue
            events = m.get("events") or []

            # 0) 延时影响 「事件后XX天触发：指令」：到期即执行，只执行一次
            pending = m.setdefault("pending_impacts", [])
            still_pending = []
            for item in pending:
                if days_between(item.get("due_date", today), today) >= 0:
                    execute_operation_command(self.state, item.get("cmd", ""))
                    CONSOLE.log("mainline", "主线延时影响执行",
                                "《%s》%s → %s" % (m.get("title"), item.get("event_id"), item.get("cmd")))
                    changed = True
                else:
                    still_pending.append(item)
            if still_pending != pending:
                m["pending_impacts"] = still_pending
                pending = still_pending

            if not events:
                continue

            occurred = m.setdefault("occurred_events", [])
            occurred_ids = {e.get("event_id") for e in occurred}
            occurred_dates = {e.get("event_id"): e.get("occurred_date") for e in occurred}

            # 剧本起始基准日：首次推进时记下
            script_start = m.get("script_start_date")
            if not script_start:
                script_start = today
                m["script_start_date"] = today
                changed = True

            for ev in events:
                eid = ev.get("event_id")
                if not eid or eid in occurred_ids:
                    continue

                trigger = str(ev.get("trigger") or "").strip()
                should_trigger = False

                # 1. 在剧本开始后 XX 天触发 兼容旧写法「阶段开始后」
                m_phase_delay = re.search(r"在(?:剧本|阶段)开始后\s*(\d+)\s*天", trigger)
                if m_phase_delay:
                    req_days = int(m_phase_delay.group(1))
                    if days_between(script_start, today) >= req_days:
                        should_trigger = True
                # 2. 在 XX 事件结束后 XX 天触发
                elif "事件结束后" in trigger:
                    m_dep_delay = re.search(r"在([a-zA-Z0-9_\-]+)事件结束后\s*(?:(\d+)\s*天?", trigger)
                    if m_dep_delay:
                        dep_id = m_dep_delay.group(1)
                        req_days = int(m_dep_delay.group(2) or 0)
                        if dep_id in occurred_ids:
                            dep_date = occurred_dates.get(dep_id, script_start)
                            if days_between(dep_date, today) >= req_days:
                                should_trigger = True
                # 3. 即时触发 0天
                elif not trigger or "即时" in trigger or trigger in  ("剧本开始", "阶段开始"):
                    should_trigger = True

                if not should_trigger:
                    continue

                ev_record = dict(ev)
                ev_record["occurred_date"] = today
                occurred.append(ev_record)
                occurred_ids.add(eid)
                changed = True

                # 投递到通政司章奏箱 目前唯一开放的展示渠道
                if ev.get("display_type") == "memorial":
                    mem_item = {
                        "id": eid,
                        "source": "mainline",
                        "mainline_id": m.get("id"),
                        "title": ev.get("title") or "【主线奏报】%s" % m.get("title"),
                        "category_label": ev.get("category_label") or "题本",
                        "content_label": ev.get("content_label") or "要紧事务",
                        "Location": ev.get("location") or "北京城",
                        "from": (ev.get("location") or "通政司") + " 官署",
                        "content": ev.get("full_text") or ev.get("content") or " 无公文正文",
                        "draft": ev.get("draft") or "候圣裁批红。",
                        "reward": ev.get("reward") or "",
                        "status": "new",
                        "event_time": today,
                        "delivery_time": today,
                    }
                    self.memorials.insert(0, mem_item)
                    CONSOLE.log("mainline", "主线事件曝光",
                                "要紧事物《%s》事件【%s】已呈递通政司。" % (m.get("title"), eid))

                # 影响指令：即时通道 + 延时通道
                impact_str = ev.get("impact") or ""
                if "即时触发：" in impact_str:
                    cmd = impact_str.split("即时触发：", 1)[1].strip()
                    if cmd:
                        execute_operation_command(self.state, cmd)
                m_delay = re.search(r"事件后\s*(\d+)\s*天触发：(.+)", impact_str)
                if m_delay:
                    m.setdefault("pending_impacts", []).append({
                        "event_id": eid,
                        "due_date": date_shift_key(self.state.date, int(m_delay.group(1))),
                        "cmd": m_delay.group(2).strip(),
                    })

        if changed:
            save_mainlines_data(data)

    def _tick_mainlines(self):
        """要紧事物：干旱修正、结束条件结算，以及剧本事件调度。"""
        from .mainline_script import tick_northwest_famine, evaluate_mainlines, load_mainlines_data
        from . import script_events as _sev
        lines = list(tick_northwest_famine(self.state))
        for rec in evaluate_mainlines(self.state):
            lines.append("【要紧事物结案】《%s》：奖励科技点 %g。"
                         % (rec["title"], rec["reward_points"]))
        lines.extend(self._tick_script_events(load_mainlines_data))
        return lines

    def _tick_script_events(self, load_fn):
        """剧本事件：四维判定，命中即触发。系统事件当场执行，其余排队给界面。

        触发记录写在 mainline_state[mid]["fired"]，同一事件只触发一次。
        """
        from . import script_events as se
        out = []
        for m in load_fn().get("mainlines", []):
            events = m.get("events") or []
            if not events:
                continue
            mid = m.get("id")
            rec = self.state.mainline_state.setdefault(mid, {})
            fired = rec.setdefault("fired", {})
            started = None
            raw = rec.get("started_at")
            if raw:
                started = se.parse_stamp(raw)
            hits = se.tick(self.state, events, started, fired)
            for hit in hits:
                ev = hit["event"]
                eid = str(ev.get("event_id") or "")
                if se.is_system(ev):
                    out.extend(self._run_system_event(ev))
                else:
                    out.append("【剧本事件】%s（%s）" % (
                        ev.get("title") or se.describe(ev), "；".join(hit["reasons"])))
                    self.state.pending_script_events.append({
                        "mainline": mid, "event": ev, "at": se.tag_key(self.state)})
                out.append(se.publish_tag(self.state, "%s:%s" % (se.TAG_EVENT_DONE, eid), eid))
        return out

    def _run_system_event(self, ev):
        """系统事件：重新撰写剧本 / 广播标签。"""
        from . import script_events as se
        t = str(ev.get("type") or "")
        if t == "broadcast_tag":
            tag = str(ev.get("tag") or ev.get("event_id") or "")
            return [se.publish_tag(self.state, tag, ev.get("event_id") or "")]
        if t == "rewrite_script":
            mid = ev.get("mainline") or ""
            if mid:
                rec = self.state.mainline_state.setdefault(mid, {})
                rec["rewrite_requested"] = True
                rec["rewrite_reason"] = ev.get("reason") or ""
            return ["【剧本事件】重新撰写剧本：%s（将改用 meeting_pizou_replan_2.txt）"
                    % (ev.get("reason") or ev.get("event_id") or "")]
        return []

    def _monthly_summary(self):
        """每月一号凌晨的月度总结与大盘更新：调用 Prompt/zongjie.txt。"""
        prompt = read_prompt("zongjie.txt")
        today = date_key(self.state.date)

        # 组装上月记录包
        c_sums = []
        for d in self.dialogues[-10:]:
            c_sums.append(f"【{d.get('category')}】《{d.get('memorial_title')}》总结：{d.get('summary', '')[:80]}")
        c_sums_text = "\n".join(c_sums) or "暂无重要会话"

        tree_records = []
        for ss in self.script_sessions[-5:]:
            tree_records.append("【剧本工作流】《%s》%s" % (ss.get("title"), ss.get("created")))
        tree_records_text = "\n".join(tree_records) or "暂无剧本工作流记录"

        m_vars = []
        for v in self.state.variation_pool[-10:]:
            m_vars.append(f"[{v.get('id')}] {v.get('text')}")
        m_vars_text = "\n".join(m_vars) or "暂无明显异动"

        cur_overview = (
            f"国库：{getattr(self.state, 'treasury', 240.0):.1f}万两 | 地方银库：{getattr(self.state, 'local_reserve', 440.0):.1f}万两 | "
            f"皇室威望：{getattr(self.state, 'base_prestige', 60.0):.1f} | 中央腐败：{getattr(self.state, 'central_corruption', 50.0):.1f}"
        )

        report_text = "上月天下大势承平，四海承风，百司各守职任。"
        if prompt and not getattr(self.llm, "mock", True):
            from .submit_tools import FINISH_MONTHLY, monthly_tool
            from .ai_workflow import run_tool_workflow
            prompt_filled = (prompt.replace("{{council_summaries}}", c_sums_text)
                             .replace("{{event_tree_records}}", tree_records_text)
                             .replace("{{monthly_variations}}", m_vars_text)
                             .replace("{{history_12}}", extract_history_12(self.state))
                             .replace("{{current_world_overview}}", cur_overview))
            messages = [{"role": "system", "content": ""},
                        {"role": "user", "content": prompt_filled}]
            result = run_tool_workflow(self.llm, messages, [monthly_tool()],
                                       state=self.state, session=self,
                                       finish_name=FINISH_MONTHLY)
            parsed = result.get("value") or {}
            if not parsed:
                # 没交卷就是没交卷：如实记下原因，不拿半截文本当总结
                CONSOLE.log("zongjie", "月度总结未交卷",
                            str(result.get("error") or "模型没有调用交卷工具"))
            else:
                report_text = parsed.get("monthly_report") or report_text
                # 1. 更新官员卡片
                for off in parsed.get("officials_updates", []):
                    oname = off.get("name")
                    if oname:
                        from .cards import update_card
                        update_card(oname, **{k: v for k, v in off.items() if k != "name" and v})
                # 2. 更新贸易项
                for tu in parsed.get("trade_updates", []):
                    from . import depts_rites
                    t_list = depts_rites.TRADE_EXPORTS if tu.get("type") == "export" else depts_rites.TRADE_IMPORTS
                    for item in t_list:
                        if item.get("id") == tu.get("id") or item.get("name") == tu.get("name"):
                            if "flat_mod" in tu: item["flat_mod"] = float(tu["flat_mod"])
                            if "pct_mod" in tu: item["pct_mod"] = float(tu["pct_mod"])

        # 归档到世界总结
        self.save_dialogue(None, f"世界总结 {today}", report_text, [], category="world")
        return report_text

    def record_snapshot(self):
        """记录当前关键数据快照，供空位替换查历史 保留最近 120 条。

        省份字段全部来自地方数据 local派生 见 engine/localdata.py：
        地方人口 / 镇军人数 万人/ 基建程度 / 税收总额 / 扶持总额。
        """
        st = self.state
        provs = {}
        total_pop = 0.0
        total_garrison = 0.0
        from .modifiers import build_local_modifier_index, province_monthly_tax
        from .armies import garrison_counts
        live_index = build_local_modifier_index(st)
        gar_counts = garrison_counts()          # 镇军人数只从名册取 省份数据里没有副本
        for pid, p in st.provinces.items():
            if not localdata.under_ming_control(p):     # 当前属明朝控制的省份 看控制权
                continue
            loc = p.get("local") or {}
            live = live_index.get(pid)
            pop = localdata.population(loc)
            total_pop += pop
            gar_n = gar_counts.get(pid, 0)
            total_garrison += gar_n
            provs[p["name"]] = {
                "population": pop,                     # 地方人口 万
                "garrison_count": gar_n / 10000.0,     # 镇军 万人
                "infra": loc.get("infra", 0),          # 基建程度
                "material_production": localdata.material_production(loc, live),
                "material_demand": localdata.material_demand(loc),
                "supply_demand_ratio": localdata.supply_demand_ratio(loc, live),
                "class_material_stock": localdata.class_material_stock(loc),
                "tax": province_monthly_tax(st, pid, loc, live_index),
                "support": localdata.support_total(loc),  # 扶持总额 两/月
            }
        self.snapshots.append({
            "year": st.date["year"], "month": st.date["month"], "day": st.date["day"],
            "label": date_str(st.date),
            "treasury": st.treasury,
            "m1": getattr(st, "m1", 320.0),
            "local_reserve": getattr(st, "local_reserve", 440.0),
            "m2": st.m2,
            "population": round(total_pop),            # 全国人口 万
            "army": round(total_garrison / 10000.0, 1),  # 陆军人数 = 全国镇军合计 机动部队留空
            "navy": 0.0,                               # 海军暂缺
            "provinces": provs,
        })
        if len(self.snapshots) > 120:
            self.snapshots = self.snapshots[-120:]
        return self.snapshots[-1]

    # ---- 对话归档 随存档，见 _save/_load----
    def save_dialogue(self, memorial_id, title, summary, pids, category="council",
                      entries=None, pizou="", messages=None, next_due=""):
        """归档一条对话记录 召对/廷议/世界总结。返回 id。

        entries：提示词规定的 JSONL 结构化条目 [{"character","topic","summary","timestamp"}]
        pizou：会话生成的批奏文本 若有。
        messages：召对完整对话原文 [{id,role,name,text}]，供归档界面回放会议内容。
        next_due：该事件下一次章奏出现的触发日期 批奏后由事件树推算，写入归档。
        """
        self.dialogues.append({
            "id": len(self.dialogues) + 1,
            "memorial_id": memorial_id,
            "memorial_title": title,
            "summary": summary,
            "entries": list(entries or []),
            "pizou": pizou or "",
            "participants": list(pids or []),
            "messages": list(messages or []),
            "next_due": next_due or "",
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
        })
        return self.dialogues[-1]["id"]

    def list_dialogues(self, category=None):
        if category == "council":
            return []                      # 召对会话归档已删除，接口不再给这一类
        rows = [d for d in self.dialogues
                if (not category or d.get("category") == category)]
        return list(reversed(rows[-50:]))

    # ---- 中旨 ----
    def issue_zhongzhi(self, command_text: str) -> Dict[str, Any]:
        """发起中旨：
        四类玩家发起事件。经过【司礼监ai】转化后生成命令行指令施加到系统。
        即时威望 -3，执行概率加权 k=1.5。
        有  威望/100^k 的概率调用 zhongzhi_2.txt 返回机构拒绝执行的章奏。
        """
        command_text = (command_text or "").strip()
        if not command_text:
            return {"ok": False, "msg": "中旨内容不能为空"}

        # 消耗时间 10 分钟
        advance_minute(self.state, 10)

        # 1. 威望即时惩罚 -3
        self.state.prestige_penalty = getattr(self.state, "prestige_penalty", 0.0) + 3.0
        p_info = calc_prestige(self.state)
        p_val = p_info["value"]

        # 2. 拒绝概率： 威望 / 100 ^ 1.5 支持测试开关 override
        test_mode = self.state.reject_test_mode
        if test_mode == "force_reject":
            is_rejected = True
        elif test_mode == "force_pass":
            is_rejected = False
        else:
            k = 1.5
            prob = (max(0.0, min(1.0, p_val / 100.0))) ** k
            is_rejected = (random.random() < prob)

        # 3. 若触发机构拒绝执行中旨
        if is_rejected:
            prompt = read_prompt("zhongzhi_2.txt").replace("{{player_command}}", command_text)
            title, full_text, loc = "", "", "京师户部"
            if not getattr(self.llm, "mock", True) and prompt:
                from .submit_tools import FINISH_REFUSAL, refusal_tool
                from .ai_workflow import run_tool_workflow
                messages = [{"role": "system", "content": ""},
                            {"role": "user", "content": prompt}]
                # 中旨抗旨是一次调用并交卷：不给多轮往返的机会
                result = run_tool_workflow(self.llm, messages, [refusal_tool()],
                                           state=self.state, session=self,
                                           finish_name=FINISH_REFUSAL, max_calls=1)
                value = result.get("value") or {}
                if value:
                    title = value.get("title") or ""
                    full_text = value.get("full_text") or ""
                    loc = value.get("Location") or "京师户部"
                else:
                    CONSOLE.log("zhongzhi", "抗疏未交卷",
                                str(result.get("error") or "模型没有调用交卷工具"))

            if not full_text:
                title = "题本 户部等衙门奉旨难遵乞还中旨疏"
                full_text = (
                    "臣等谨题，伏奉中旨令臣等遵行【%s】事。伏查朝廷旧制，中旨不经凤阁纶扉封驳，"
                    "文武百司无所折衷，难以遵行。臣等伏乞收回成命，仍付廷议施行。\n谨题请旨。" % command_text
                )

            writer = pick_memorial_writer(loc, full_text, title, "规谏疏", "题本")
            m = {
                "id": self.next_event_id("n"),
                "title": title or "题本 户部等衙门奉旨难遵乞还中旨疏",
                "from": writer,
                "content": full_text,
                "format": "题本",
                "category": "规谏疏",
                "source": "player_origin",
                "urgent": True,
                "status": "new",
                "date": date_str(self.state.date),
                "Location": loc,
                "draft_processed": "原旨留中，下部院再议。",
                "draft_unprocessed": "成命收回，体统为重。",
                "reward_processed": "中旨受阻：政令未达",
                "reward_unprocessed": "朝廷规制保全",
            }
            _stamp(m, self.state)
            self.memorials.insert(0, m)
            CONSOLE.log("action", "中旨受阻", "各衙门抗旨拒行：%s" % command_text)
            self.save_dialogue(
                m["id"],
                f"中旨受阻 {command_text[:20]}",
                "各衙门以缺乏内阁与通政司合法程序为由抗旨封还，已呈递章奏。",
                ["皇帝", writer],
                category="zhongzhi",
                entries=[{"character": writer, "topic": "中旨封驳", "summary": "抗旨拒行中旨", "timestamp": date_str(self.state.date)}],
                pizou="原旨留中，下部院再议。",
                messages=[
                    {"id": 1, "role": "player", "name": "皇帝", "text": f"【口谕传旨】{command_text}"},
                    {"id": 2, "role": "ai", "name": writer, "text": full_text}
                ]
            )
            return {
                "ok": False,
                "rejected": True,
                "msg": "中旨受阻！各衙门以缺乏内阁与通政司合法程序为由抗旨封还，已呈递章奏 威望 -3.0。"
            }

        # 4. 若未拒绝 成功执行：由司礼监 AI 走工具工作流，把口谕转成系统命令行
        #    指令目录、合法取值与试跑回执都在工具里 engine/zhongzhi_tools.py，
        #    提示词只放提示词与口谕原文
        prompt_silijian = read_prompt("silijian_ai.txt")
        command_line = ""
        if prompt_silijian:
            prompt_silijian = prompt_silijian.replace("{{player_command}}", command_text)
            from .llm import get_llm_for_system
            from .zhongzhi_tools import run_zhongzhi_workflow
            silijian_llm = get_llm_for_system("silijian")
            if not getattr(silijian_llm, "mock", True):
                res = run_zhongzhi_workflow(self.state, silijian_llm, prompt_silijian,
                                           session=self)
                command_line = res["command_line"]
                if not command_line:
                    CONSOLE.log("action", "司礼监未交回命令行，按口谕原文执行", res.get("error") or "")

        if not command_line:
            command_line = command_text

        lines = []
        if command_line:
            # 优先调用 operations 引擎执行超控
            from .operations import execute_operation_command
            ok, msg = execute_operation_command(self.state, command_line)
            if ok:
                lines.append(msg)
            else:
                lines = self.run(command_line)
        record_variation(self.state, "zhongzhi", "皇帝下达中旨：%s" % command_text)
        CONSOLE.log("action", "中旨执行", "司礼监传宣中旨：%s -> 执行：%s" % (command_text, command_line))
        # 归档到中旨会话
        self.save_dialogue(
            f"zhongzhi_{len(self.dialogues)+1}",
            f"中旨 {command_text[:20]}",
            "；".join(lines) if lines else "中旨已传宣奉行。",
            ["皇帝", "司礼监掌印太监 魏忠贤"],
            category="zhongzhi",
            entries=[{"character": "皇帝", "topic": "中旨传宣", "summary": command_text, "timestamp": date_str(self.state.date)}],
            pizou=command_line,
            messages=[
                {"id": 1, "role": "player", "name": "皇帝", "text": f"【口谕传旨】{command_text}"},
                {"id": 2, "role": "ai", "name": "司礼监掌印太监 魏忠贤", "text": f"奴婢遵旨。已传宣中旨奉行：\n{command_line}"}
            ]
        )
        return {
            "ok": True,
            "msg": "中旨已由司礼监传宣奉行 威望 -3.0。\n" + ("\n".join(lines) if lines else "中旨已奉行。")
        }

    # ---- 章奏 ----
    def refresh_memorials(self, counts=None):
        """推进后生成新一批当日章奏 n 不重要 / m 次要 / l 主要 三层。"""
        batch = generate_daily(self.state, self.llm, counts=counts, snaps=self.snapshots,
                               id_fn=self.next_event_id)
        now = date_str(self.state.date)
        srcs = {}
        for m in batch:
            srcs[m["source"]] = srcs.get(m["source"], 0) + 1
        CONSOLE.log("mem", "生成章奏 %d 份" % len(batch),
                    "n不重要=%s m次要=%s l主要=%s" % (srcs.get("filler", 0),
                                                     srcs.get("side", 0),
                                                     srcs.get("mainline", 0)))
        for m in batch:
            m["date"] = now
        self.memorials = batch + self.memorials
        return batch

    def list_memorials(self):
        """章奏目录：未处理 new在前、已处理 已阅/已批/批奏置底且不消失。"""
        pending, done = [], []
        for m in self.memorials:
            if m.get("status") and m["status"] != "new":
                done.append(m)
            else:
                pending.append(m)
        return pending + done

    def memorial_act(self, mid, action, text=""):
        """已阅 / 回折 / 批注。返回  ok, 消息。每批阅 1 封章奏消耗时间 10 分钟。

        - 已阅 (read): 按照内阁党派选定的票拟与奖励执行。
        - 批注 (annotate): 有  威望/100^0.1 概率触发六科封驳；若未封驳，进入事件树追踪 事件2。
        """
        for m in self.memorials:
            if m["id"] == mid:
                # 批阅章奏消耗时间 10 分钟
                advance_minute(self.state, 10)

                if action == "read":
                    m["status"] = "read"
                    # 若为主线生成的章奏，不进入分支判定与评定，直接标记已阅完成
                    if m.get("source") == "mainline":
                        CONSOLE.log("mem", "已阅主线章奏", f"《{m.get('title')}》已阅。")
                        return True, "已阅主线章奏：%s" % m["title"]

                    dec = evaluate_memorial_decision(m, self.state)
                    m.update(dec)
                    reward = m.get("selected_reward") or m.get("reward_processed", "")

                    # 真正执行奖励/惩罚解析闭环 超控与奖励是一个逻辑
                    from .operations import apply_reward_text
                    prov_hint = m.get("Location") or m.get("province") or "shaanxi"
                    reward_notes = apply_reward_text(self.state, reward, default_province=prov_hint)

                    reward_desc = f"{reward}\n底层执行明细：{'；'.join(reward_notes)}" if reward_notes else reward
                    CONSOLE.log("mem", "已阅章奏", "%s\n执行内阁票拟：%s\n触发奖励：%s" % (m["title"], m.get("selected_draft", ""), reward_desc))
                    return True, "已阅：%s 按内阁票拟执行" % m["title"]

                if action == "reject":
                    m["status"] = "rejected"
                    # 若章奏已有既定选定方向，基于其反转；否则调用 evaluate_memorial_decision
                    cur_type = m.get("decision_type")
                    if not cur_type:
                        dec = evaluate_memorial_decision(m, self.state)
                        cur_type = dec.get("decision_type")

                    # 反转决策类型 积极 <-> 消极/留中
                    opp_type = "unprocessed" if cur_type == "processed" else "processed"
                    opp_draft = m.get("draft_unprocessed") if opp_type == "unprocessed" else m.get("draft_processed", "")
                    opp_reward = m.get("reward_unprocessed") if opp_type == "unprocessed" else m.get("reward_processed", "")

                    m["decision_type"] = opp_type
                    m["selected_draft"] = opp_draft
                    m["selected_reward"] = opp_reward

                    # 1. 执行相反效果的奖励/惩罚解析闭环
                    from .operations import apply_reward_text, reduce_prestige
                    prov_hint = m.get("Location") or m.get("province") or "shaanxi"
                    reward_notes = apply_reward_text(self.state, opp_reward, default_province=prov_hint)

                    # 2. 额外触发皇室威望 -0.1
                    p_note = reduce_prestige(self.state, 0.1)
                    reward_notes.append(p_note)

                    reward_desc = f"{opp_reward}\n底层执行明细：{'；'.join(reward_notes)}" if reward_notes else opp_reward
                    CONSOLE.log("mem", "驳回章奏", "%s\n驳回并改行相反票拟：%s\n触发效果：%s" % (m["title"], opp_draft, reward_desc))
                    return True, "已驳回：%s 执行相反票拟，威望 -0.1" % m["title"]

                if action == "reply":
                    m["status"] = "replied"
                    m["reply"] = text
                    CONSOLE.log("mem", "玩家回折", "%s\n回折内容：%s" % (m["title"], text))
                    return True, "回折已呈：%s" % m["title"]

                if action == "annotate":
                    # 【v0.5.0】章奏回复的 AI 已全部删除：不再有六科封驳 AI、不再有续报推演。
                    # 玩家打字批阅只做纯记录 批注 + 批奏文本 + 归档，不进事件树。
                    # AI 事件改由自由廷议与中旨负责。
                    m.setdefault("annotations", []).append(text)
                    m["status"] = "annotated"
                    m["pizou"] = text
                    today = date_key(self.state.date)
                    days = travel_days(m.get("Location", "")) * 2 + random.randint(0, 10)
                    due = date_shift_key(self.state.date, days) if m.get("Location") else ""
                    self.save_dialogue(
                        m["id"],
                        m.get("title", ""),
                        "御笔批奏：%s" % text,
                        ["皇帝"],
                        category="annotate",      # 御笔批注归档（召对会话已删除）
                        entries=[{"character": "皇帝", "topic": m.get("title", ""),
                                  "summary": text, "timestamp": date_str(self.state.date)}],
                        pizou=text,
                        messages=[{"id": 1, "role": "player", "name": "皇帝",
                                   "text": "【御笔批奏】%s" % text}],
                        next_due=due
                    )
                    CONSOLE.log("mem", "打字批阅", "%s\n批奏：%s" % (m["title"], text))
                    return True, "已批奏：%s" % m["title"]
                return False, "未知操作"
        return False, "查无此章奏"

    # ---- 事件树：主线剧本 AI 工作流的对话记录 章奏回复的 AI 已删除----
    def add_script_session(self, mainline_id: str, title: str, log, script=None, note: str = "") -> dict:
        """把一次剧本工作流的完整对话记进事件树 唯一写入口。"""
        rec = {
            "id": "ss-%d" % (len(self.script_sessions) + 1),
            "mainline_id": mainline_id or "",
            "title": title or "",
            "created": date_str(self.state.date),
            "date": date_key(self.state.date),
            "note": note or "",
            "script": script or None,
            "log": list(log or []),
        }
        self.script_sessions.append(rec)
        CONSOLE.log("court", "事件树记录", "《%s》对话 %d 条" % (rec["title"], len(rec["log"])))
        return rec

    def list_script_sessions(self):
        """事件树读口：最近的剧本工作流记录 倒序。"""
        return list(reversed(self.script_sessions))

    def _mem_list(self):
        lines = ["【当日章奏 %d 份】" % len(self.memorials)]
        if not self.memorials:
            lines.append (" 今日章奏已毕，可推进时间再得新章奏")
        for m in self.memorials:
            tag = "急" if m.get("urgent") else ""
            lines.append("[%s] %s %s %s %s" % (
                m["id"], tag, m["format"], m["category"],
                m["title"]))
        return lines

    def _mem_view(self, parts):
        if len(parts) < 2:
            return ["用法：view <id>"]
        for m in self.memorials:
            if m["id"] == parts[1]:
                return ["【%s】%s %s %s %s" % (
                    m["format"], m["title"], m["category"], m["source"], m["date"]),
                    "上奏者：%s" % m["from"], "",
                    m["content"]]
        return ["查无此章奏"]

    def _mem_read(self, parts):
        if len(parts) < 2:
            return ["用法：read <id>"]
        ok, msg = self.memorial_act(parts[1], "read")
        return [msg] if ok else ["诏令不行：%s" % msg]

    def _mem_reply(self, parts):
        if len(parts) < 3:
            return ["用法：reply <id> <回折内容>"]
        ok, msg = self.memorial_act(parts[1], "reply", " ".join(parts[2:]))
        return [msg] if ok else ["诏令不行：%s" % msg]

    # ---- 官员 ----
    def _officials_list(self):
        act = list_active()
        lines = ["【在职官员 %d 名】" % len(act)]
        for o in act[:40]:
            lines.append("%s %s %s" % (o["name"], o["title"], o["province"] or "京师"))
        return lines

    def _remove_official(self, parts):
        """罢免官员：下旨罢免 -> 记在位末 -> 随机 1-6 月后继任者上任。"""
        if len(parts) < 2:
            return ["用法：罢免 <姓名>"]
        name = parts[1]
        end = month_key_str(self.state)
        d = remove_official(name, end_date=end)
        if not d:
            return ["查无在职官员：%s" % name]
        succ = schedule_successor(d, end)
        CONSOLE.log("action", "罢免官员 %s %s" % (d["name"], d["title"]),
                    "在位至 %s；继任者 %s 预定 %s 就任"
                    % (end, succ["name"], succ["arrive_date"]))
        return ["已罢免 %s %s，继任者 %s 将择期上任。"
                % (d["name"], d["title"], succ["name"])]

    # ---- 次要事件测试调用 控制台----
    def run_side_test(self, instruction=""):
        """控制台专用：单独跑一次次要事件生成。
        结果只进集中控制台，不进入游戏。"""
        from .memorials import generate_side
        batch = generate_side(self.state, self.llm, count=2,
                              extra_instruction=instruction,
                              id_fn=self.next_event_id)
        for m in batch:
            CONSOLE.log("side", "测试生成：「%s」" % m["title"],
                        "ID=%s 分类：%s/%s\n事发地：%s\n意见参数：%s\n公式：%s\n正文：\n%s"
                        % (m["id"], m["format"], m["category"], m.get("Location", ""),
                           m["opinion_shift"], m["formula"], m["content"]))
        return {"ok": True,
                "msg": "已生成 %d 条次要事件，详情见集中控制台。" % len(batch)}

    def _save(self):
        from .armies import load_armies_data
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump({"state": self.state.to_dict(),
                       "armies_data": load_armies_data(),
                       "dialogues": self.dialogues,
                       "script_sessions": self.script_sessions,
                       "event_seq": self.event_seq,
                       "dawn_state": self.dawn_state,
                       "memorials": self.memorials,
                       "battle_reports": getattr(self, "battle_reports", []),
                       "unread_battle_reports": getattr(self, "unread_battle_reports", False)},
                      f, ensure_ascii=False, indent=2)
        return ["已存档。"]

    def _load(self):
        if not os.path.exists(SAVE_FILE):
            return ["无存档可读。"]
        with open(SAVE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "state" in data:
            self.state = WorldState.from_dict(data["state"])
            if "armies_data" in data and data["armies_data"]:
                from .armies import save_armies_data
                save_armies_data(data["armies_data"])
            self.dialogues = [d for d in (data.get("dialogues") or [])
                              if d.get("category") != "council"]      # 召对会话归档已删除（批注走 annotate）
            self.script_sessions = data.get("script_sessions") or []
            self.event_seq = data.get("event_seq") or {"n": 0, "m": 0, "l": 0}
            self.dawn_state = data.get("dawn_state") or {
                "last": date_key(self.state.date), "next_side": None}
            self.memorials = data.get("memorials") or []
            self.battle_reports = data.get("battle_reports") or []
            self.unread_battle_reports = bool(data.get("unread_battle_reports", False))
        else:
            # 旧格式：整个文件就是 state 无对话归档
            self.state = WorldState.from_dict(data)
            self.dialogues = []
            self.script_sessions = []
            self.event_seq = {"n": 0, "m": 0, "l": 0}
            self.dawn_state = {"last": date_key(self.state.date), "next_side": None}
            self.memorials = []
        self.last_proposals = None
        return ["已读档。"]

    def _new_game(self):
        self.state = WorldState(self.world)
        self.dialogues = []
        self.script_sessions = []
        self.event_seq = {"n": 0, "m": 0, "l": 0}
        self.dawn_state = {"last": date_key(self.state.date),
                           "next_side": self._first_side_date()}
        self.memorials = []
        self.last_proposals = None
        self.boot_lines = self._boot()
        self._seed_opening_history()
        self.refresh_memorials(counts={"mainline": 0, "side": 0, "filler": 4})
        return self.boot_lines
