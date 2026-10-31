"""天启七年 网页外壳 零依赖：仅 Python 标准库。

运行：python server.py   然后浏览器打开 http://127.0.0.1:8010
端口可用环境变量 PORT 覆盖 如 PORT=8011 python server.py。

API：
  GET  /                      页面 web/index.html
  GET  /api/state             会话快照
  GET  /png/fine_arts.png     显示用大图 12000x13000
  GET  /api/pixel?x=&y=       地块判定：取 determination.png 该像素色查表
  POST /api/command           {"cmd": "..."} → {lines, state}
"""

import json
import os
from urllib.parse import unquote
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.stdout.reconfigure(encoding="utf-8")

from engine.session import GameSession
from engine.time import month_name, date_str, date_cn, date_greg
from engine.mapdetect import MapDetector
from engine.logger import CONSOLE
from engine import localdata, economy, tech
from engine import __version__
from engine import depts_libu, depts_rites, tech_effects
from engine import metric_rows

SESSION = GameSession()
DETECTOR = MapDetector()  # 后台解码 determination.png，未就绪时取色返回 busy
LOCK = threading.Lock()
PORT = int(os.environ.get("PORT", "8010"))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
PNG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "png")

# 注：原 load_mainlines() / MAINLINES 是主线的第二份载入路径 读盘失败静默退空表，
# 且 MAINLINES 全局变量无人使用。主线读口只有 engine.mainline_script.load_mainlines_data 一处，
# 已删除。


def snapshot():
    st = SESSION.state
    provs = []
    # 部门地方修正：实时汇总一次，各省物质产出/供需指数取同一份结果 不落盘
    from engine.modifiers import (build_local_modifier_index, get_central_modifier_rows,
                                  province_monthly_tax)
    from engine.armies import garrison_counts
    live_index = build_local_modifier_index(st)
    gar_counts = garrison_counts()          # 镇军人数只从名册取
    for p in st.provinces.values():
        loc = p.get("local") or {}
        live = live_index.get(p["id"])
        provs.append({
            "id": p["id"], "name": p["name"], "type": p["type"], "owner": p["owner"],
            "neighbors": list(p["neighbors"]),
            "core": loc.get("core", ""),
            "controller": localdata.controller_of(p),
            # 地方数据派生值 见 engine/localdata.py：人口/基建/物质产出/物质需求/供需指数/库存/税收/扶持/腐败/承载
            # 镇军人数取自军队名册 world/armies.json，省份数据里不存副本
            "population": localdata.population(loc),
            "population_exact": localdata.population_exact(loc),
            "garrison_count": gar_counts.get(p["id"], 0) / 10000.0,
            "infra": loc.get("infra", 0),
            "corruption": localdata.corruption(loc, st),
            "carrying": localdata.carrying(loc, st),
            "material_production": localdata.material_production(loc, live),
            "material_demand": localdata.material_demand(loc),
            "supply_demand_ratio": localdata.supply_demand_ratio(loc, live),
            "class_material_stock": localdata.class_material_stock(loc),
            "tax": province_monthly_tax(st, p["id"], loc, live_index),
            "support": localdata.support_total(loc),
        })
    m1 = getattr(st, "m1", 320.0)
    res = getattr(st, "local_reserve", 440.0)
    frate = getattr(st, "financial_rate", 0.15)
    cdelta = getattr(st, "confidence_delta", 0.20)
    cacc = getattr(st, "confidence_acc", 0.0)
    lev_m1 = round(m1 / max(1e-5, 1.0 - frate), 1)
    lev_treasury = round(st.treasury / max(1e-5, 1.0 - frate), 1)
    return {
        "llm_mode": "mock" if SESSION.llm.mock else "live",
        "header": SESSION.header(),
        "boot": SESSION.boot_lines,
        "quit": SESSION.quit,
        "date": st.date,
        "script_tags": dict(getattr(st, "script_tags", {}) or {}),
        "pending_script_events": list(getattr(st, "pending_script_events", []) or []),
        "date_str": date_str(st.date),
        "date_cn": date_cn(st.date),
        "date_greg": date_greg(st.date),
        "month_label": month_name(st.date),
        "treasury": st.treasury,
        "privy_treasury": getattr(st, "privy_treasury", 120.0),
        "m1": m1,
                # 【v0.3.5】物价指数：军队欠饷等银→物质折算要用它，原来前端拿不到、恒按 1.0 算
                "price_index": round(float(economy.calc_price_index(SESSION.state)), 4),
        "local_reserve": res,
        "m2": st.m2,
        # 【v0.5.9】M₂ 的构成项之一：账本栏要列出它，屏幕上的 M₂ 才能与下列各项对上（默认 0 不虚增银）
        "changying_treasury": getattr(st, "changying_treasury", 0.0),
        "leveraged_m1": lev_m1,
        "leveraged_treasury": lev_treasury,
        "gold_treasury": getattr(st, "gold_treasury", 100.0),
        "gold_local_reserve": getattr(st, "gold_local_reserve", 25.0),
        "gold_m1": getattr(st, "gold_m1", 1.5),
        "gold_total": getattr(st, "gold_total", 126.5),
        "financial_rate": frate,
        # 替代率的算账明细：界面上该项的【ⓘ】三段 修正/计算方式/机制 就从它出，数值与上面同源
        "financial_calc": tech_effects.financial_rate_calc(st),
        "confidence_delta": cdelta,
        "confidence_acc": cacc,
        "national_demand": economy.calc_national_demand(st),
        "liquidity_health": economy.calc_liquidity_health(m1, frate, economy.calc_national_demand(st)),
        "leverage": economy.calc_leverage(frate),
        "national_market_stock": getattr(st, "national_market_stock", 0.0),
        "central_corruption": depts_libu.calc_central_corruption(st),
        "prestige": depts_rites.calc_prestige(st),
        "council_active": SESSION.council.active,
        "sacrifices": st.sacrifices,
        "pending_sacrifices": st.pending_sacrifices,
        "provinces": provs,
        "journal": st.journal[-30:],
        "reject_test_mode": st.reject_test_mode,
        "pending_army_orders": st.pending_army_orders,
        "pending_person_movements": st.pending_person_movements,        "emperor_location": st.emperor_location,
        "sacrifice_prompt": depts_rites.due_sacrifice(st),
        # 中央范围的修正条目 项目/范围/效果：供主界面上方各数据项的【ⓘ】动态显示
        "central_modifiers": get_central_modifier_rows(st),
        # 科技点：主界面左上角科技入口显示 科技树数据见 /api/tech
        "tech_points": tech.points(st),
        # 顶栏各数值的三段（修正／计算方式／机制）：唯一来源在 engine/metric_rows.py，
        # 界面只管渲染，不再自己写说明文字
        **metric_rows.all_rows(st, provs),
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # 静默访问日志

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # 一律不缓存：界面改了以后浏览器还拿着旧页面，看着就像「改了没生效」
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b'{"error":"web/index.html missing"}')
        elif path.startswith("/png/"):
            # png 目录下的静态资源（背景图、立绘 png/hum/*）：只允许相对路径，挡目录穿越
            rel = unquote(path[len("/png/"):]).replace("\\", "/")
            full = os.path.normpath(os.path.join(PNG_DIR, rel))
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".jfif": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp",
                     ".txt": "text/plain; charset=utf-8", ".psd": "application/octet-stream"}.get(
                         os.path.splitext(full)[1].lower())
            if (not full.startswith(os.path.abspath(PNG_DIR) + os.sep) or ctype is None
                    or not os.path.isfile(full)):
                self._send(404, b'{"error":"png not found"}')
            else:
                try:
                    with open(full, "rb") as f:
                        body = f.read()
                    self._send(200, body, ctype)
                except OSError:
                    self._send(404, b'{"error":"png missing"}')
        elif path == "/api/state":
            with LOCK:
                st = snapshot()
                st["version"] = __version__   # 页面上显示，用来一眼看出浏览器是不是旧页面
                self._send(200, json.dumps(st, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/memorials":
            with LOCK:
                self._send(200, json.dumps(
                    {"memorials": SESSION.list_memorials()}, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/pixel":
            q = parse_qs(parsed.query)
            try:
                x = int(q.get("x", ["-1"])[0])
                y = int(q.get("y", ["-1"])[0])
            except ValueError:
                self._send(400, b'{"error":"bad x/y"}')
                return
            resp = json.dumps(DETECTOR.pick(x, y), ensure_ascii=False).encode("utf-8")
            self._send(200, resp)
        elif path == "/api/characters":
            from engine.characters import list_characters, list_callable
            with LOCK:
                resp = json.dumps({"characters": list_characters(),
                                   "callable": [c["id"] for c in list_callable()]},
                                  ensure_ascii=False).encode("utf-8")
            self._send(200, resp)
        elif path == "/api/positions":
            # 官职表唯一读口：按部门/类型/分类取，行内六列齐全
            from engine import positions as _pos
            q = parse_qs(parsed.query)
            dept = (q.get("dept") or [""])[0]
            kind = (q.get("kind") or [""])[0]
            category = (q.get("category") or [""])[0]
            if dept:
                rows = _pos.rows(kind="京官", category=dept)
            elif kind:
                rows = _pos.rows(kind=kind, category=category or None)
            else:
                rows = _pos.rows()
            self._send(200, json.dumps({"positions": rows, "total": len(rows)},
                                       ensure_ascii=False).encode("utf-8"))
        elif path == "/api/script-events":
            with LOCK:
                self._send(200, json.dumps(
                    {"events": list(SESSION.state.pending_script_events or [])},
                    ensure_ascii=False).encode("utf-8"))
        elif path == "/api/token-stats":
            from engine.llm import token_stats
            self._send(200, json.dumps(token_stats(), ensure_ascii=False).encode("utf-8"))
        elif path == "/api/console":
            # 控制台归档：与 logs/console.log 同源的环形缓冲
            from engine.logger import CONSOLE
            q = parse_qs(parsed.query)
            try:
                n = int(q.get("limit", ["400"])[0])
            except ValueError:
                n = 400
            self._send(200, json.dumps({"entries": CONSOLE.recent(max(1, min(n, 5000)))},
                                       ensure_ascii=False).encode("utf-8"))
        elif path == "/api/dialogues":
            q = parse_qs(parsed.query)
            cat = q.get("category", [None])[0]
            with LOCK:
                self._send(200, json.dumps(
                    {"dialogues": SESSION.list_dialogues(category=cat)},
                    ensure_ascii=False).encode("utf-8"))
        elif path == "/api/council/data_preview":
            # 调阅内容的预览：与实际发给 AI 的内容走同一个生产函数 council.data_preview
            q = parse_qs(parsed.query)
            sw = (q.get("sw", [""])[0] or "").strip()
            from engine.council import data_preview, switch_title
            with LOCK:
                try:
                    text = data_preview(SESSION.state, SESSION, sw)
                    title = switch_title(SESSION.state, sw)
                except KeyError as e:
                    self._send(404, json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
                    return
            self._send(200, json.dumps({"title": title, "text": text},
                                       ensure_ascii=False).encode("utf-8"))
        elif path == "/api/event-tree":
            with LOCK:
                self._send(200, json.dumps(
                    {"sessions": SESSION.list_script_sessions()},
                    ensure_ascii=False).encode("utf-8"))
        elif path == "/api/armies":
            from engine.armies import MAINT_PER_MAN, PAY_PER_MAN, get_all_armies
            with LOCK:
                data = {"units": get_all_armies(SESSION.state),
                        "pay_per_man": PAY_PER_MAN,
                        "maint_per_man": MAINT_PER_MAN}
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/hubu/overview":
            from engine.depts_hubu import get_hubu_overview
            with LOCK:
                data = get_hubu_overview(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/factions/overview":
            from engine.factions import calc_overall_faction_control, calc_cabinet_faction_distribution
            with LOCK:
                overall = calc_overall_faction_control(SESSION.state)
                cab = calc_cabinet_faction_distribution(SESSION.state)
                data = {
                    "ratios": overall.get("ratios", {}),
                    "scores": overall.get("scores", {}),
                    "counts": overall.get("counts", {}),
                    "cabinet": cab
                }
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/libu/overview":
            from engine.depts_libu import get_libu_overview
            with LOCK:
                data = get_libu_overview(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/rites/overview":
            from engine.depts_rites import get_rites_overview
            with LOCK:
                data = get_rites_overview(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/calendar":
            from engine.calendar import get_calendar_month_data, game_to_gregorian
            q = parse_qs(parsed.query)
            with LOCK:
                cur_greg = game_to_gregorian(SESSION.state.date)
                y = int(q.get("year", [cur_greg.year])[0])
                m = int(q.get("month", [cur_greg.month])[0])
                data = get_calendar_month_data(y, m, SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/xingbu/overview":
            from engine.depts_xingbu import get_xingbu_overview
            with LOCK:
                data = get_xingbu_overview(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/tech":
            from engine.tech import tech_view
            with LOCK:
                data = tech_view(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/works/overview":
            from engine.depts_works import get_works_overview
            with LOCK:
                data = get_works_overview(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/war/overview":
            from engine.depts_war import get_war_overview
            with LOCK:
                data = get_war_overview(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/taipusi/overview":
            from engine.depts_taipusi import get_taipusi_overview
            with LOCK:
                data = get_taipusi_overview(SESSION.state)
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/battles":
            with LOCK:
                data = {
                    "reports": getattr(SESSION, "battle_reports", []),
                    "unread": getattr(SESSION, "unread_battle_reports", False),
                }
            self._send(200, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/settings/variations_history":
            with LOCK:
                all_vars = SESSION.state.historical_variations
                # 按照年  一级 -> 月  二级 分组
                grouped = {}
                for v in all_vars:
                    y = str(v.get("year") or 1627)
                    m = str(v.get("month") or 8)
                    grouped.setdefault(y, {}).setdefault(m, []).append(v)
            self._send(200, json.dumps({"history": grouped}, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/mainlines":
            from engine.mainline_script import mainline_rows
            with LOCK:
                self._send(200, json.dumps({"mainlines": mainline_rows(SESSION.state)},
                                           ensure_ascii=False).encode("utf-8"))
        elif path == "/api/tasks":
            from engine.tasks import task_rows
            with LOCK:
                self._send(200, json.dumps({"tasks": task_rows(SESSION.state)},
                                           ensure_ascii=False).encode("utf-8"))
        elif path == "/api/achievements":
            from engine.achievements import achievement_rows
            with LOCK:
                self._send(200, json.dumps({"achievements": achievement_rows(SESSION.state)},
                                           ensure_ascii=False).encode("utf-8"))
        elif path == "/api/settings/operations":
            from engine.operations import get_all_operations_schema, get_ai_operations_schema
            self._send(200, json.dumps({"operations": get_all_operations_schema(),
                                        "ai_operations": get_ai_operations_schema()},
                                       ensure_ascii=False).encode("utf-8"))
        elif path == "/api/settings/terms":
            # 修正词条表与超控指令表 唯一真相源在 engine 侧 这里只做投递
            # commands 只给全域指令、ai_commands 只给国家 AI 专用指令，与前端的第 3/4 节一一对应
            from engine.modifier_terms import terms_payload, locations_payload
            from engine.operations import OVERRIDE_COMMANDS
            self._send(200, json.dumps({"terms": terms_payload(),
                                          "locations": locations_payload(),
                                        "commands": [r for r in OVERRIDE_COMMANDS if not r.get("ai")],
                                        "ai_commands": [r for r in OVERRIDE_COMMANDS if r.get("ai")]},
                                       ensure_ascii=False).encode("utf-8"))
        elif path == "/api/wenhua":
            from engine import wenhua
            with LOCK:
                view = wenhua.wenhua_view(SESSION.state)
            self._send(200, json.dumps(view, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/province":
            q = parse_qs(parsed.query)
            pid = q.get("id", [""])[0]
            with LOCK:
                # 【v0.3.7】载荷构造统一在 engine/localdata.build_province_view 服务端与测试共用一份
                view = localdata.build_province_view(SESSION.state, pid)
            if view is None:
                self._send(404, b'{"error":"unknown province"}')
            else:
                self._send(200, json.dumps({"province": view},
                                           ensure_ascii=False).encode("utf-8"))
        elif path == "/api/army":
            q = parse_qs(parsed.query)
            aid = q.get("id", [q.get("army_id", [""])[0]])[0]
            from engine.armies import get_army_by_id
            with LOCK:
                army = get_army_by_id(aid, SESSION.state)
            if army is None:
                self._send(404, b'{"error":"unknown army"}')
            else:
                from engine.armies import MAINT_PER_MAN, PAY_PER_MAN
                body = {"army": army,
                        "pay_per_man": PAY_PER_MAN,
                        "maint_per_man": MAINT_PER_MAN}
                self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/council/state":
            with LOCK:
                self._send(200, json.dumps(SESSION.council.state(),
                                           ensure_ascii=False).encode("utf-8"))
        elif path == "/api/attendants":
            from engine import attendants as _att
            from engine import stage as _stage
            with LOCK:
                slots = _att.slots(SESSION.state)
                prompt = dict(getattr(SESSION.state, "attendant_prompt", None) or {})
            self._send(200, json.dumps(
                {"slots": slots, "prompt": prompt,
                 "primary": _att.primary_slot(SESSION.state),
                 "categories": list(_att.CATEGORIES)},
                ensure_ascii=False).encode("utf-8"))
        elif path == "/api/stage":
            from engine import stage as _stage
            q = parse_qs(parsed.query)
            cast = [c for c in (q.get("cast", [""])[0] or "").split(",") if c]
            speaker = (q.get("speaker") or [""])[0] or None
            try:
                plan = _stage.plan(cast, speaker)
                body = {"plan": plan, "ok": True}
            except ValueError as e:
                body = {"ok": False, "error": str(e), "plan": []}
            self._send(200, json.dumps(body, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/dept":
            from engine.depts import build_dept
            q = parse_qs(parsed.query)
            name = q.get("name", [""])[0]
            with LOCK:
                d = build_dept(name, SESSION.state)
            if d is None:
                self._send(404, b'{"error":"unknown dept"}')
            else:
                self._send(200, json.dumps(d, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/roster":
            from engine.officials import (civil_roster, recent_history,
                                          month_key_str, normalize_official)
            with LOCK:
                groups = civil_roster(include_capital=False)
                # 人物标识统一从 officials.normalize_official 出 地点/党派/归属归一
                groups = {k: [normalize_official(o) for o in v] for k, v in groups.items()}
                regions = [{"name": k, "count": len(v)} for k, v in groups.items()]
                total = sum(len(v) for v in groups.values())
                hist = [normalize_official(o) for o in recent_history(40)]
                now = month_key_str(SESSION.state)
            self._send(200, json.dumps(
                {"now": now, "total": total, "regions": regions,
                 "groups": groups, "history": hist},
                ensure_ascii=False).encode("utf-8"))
        elif path == "/api/logs":
            with LOCK:
                self._send(200, json.dumps({"entries": CONSOLE.recent(300)},
                                           ensure_ascii=False).encode("utf-8"))
        elif path == "/api/settings/ai":
            from engine.llm import load_config, load_system_ai_configs
            with LOCK:
                self._send(200, json.dumps({
                    "global": load_config(),
                    "systems": load_system_ai_configs()
                }, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/cards":
            from engine.char_cards import list_cards
            from engine.hum import all_adjust, frame_info
            with LOCK:
                self._send(200, json.dumps({"cards": list_cards(),
                                            "hum_adjust": all_adjust(),
                                            "hum_frame": frame_info()},
                                           ensure_ascii=False).encode("utf-8"))
        elif path == "/api/card":
            from engine.char_cards import card_view
            q = parse_qs(parsed.query)
            name = q.get("name", [""])[0]
            with LOCK:
                c = card_view(name)
                if isinstance(c, dict):
                    from engine.hum import imported as _imp, EXPRS as _EX
                    c["exprs"] = _imp(name)
                    c["expr_all"] = list(_EX)
            if c is None:
                self._send(404, b'{"error":"unknown card"}')
            else:
                self._send(200, json.dumps({"card": c}, ensure_ascii=False).encode("utf-8"))
        elif path in ("/console", "/console.html"):
            try:
                with open(os.path.join(WEB_DIR, "console.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b'{"error":"console.html missing"}')
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/command", "/api/memorial", "/api/side-run",
                        "/api/council/start", "/api/court/start",
                        "/api/council/player", "/api/council/ai-reply",
                        "/api/council/switches", "/api/court/switches",
                        "/api/mainline/switches",
                        "/api/council/switch", "/api/council/edit",
                        "/api/council/delete", "/api/council/pizou",
                        "/api/council/end", "/api/court/end_direct",
                        "/api/court/end_pizou", "/api/council/quick-end",
                        "/api/court/quick-end",
                        "/api/card/save", "/api/card/patch", "/api/card/create_ai", "/api/hum/upload",
                        "/api/hum/adjust",
                        "/api/army/pay_rate", "/api/army/arrears",
                        "/api/army/move", "/api/army/dispatch",
                        "/api/army/equipment/quota", "/api/army/equipment/delete", "/api/army/equipment/add",
                        "/api/army/stance_order", "/api/battles/mark_read",
                        "/api/sacrifice/schedule", "/api/sacrifice/attend", "/api/sacrifice/decline",
                        "/api/emperor/move", "/api/tick",
                        "/api/settings/reject_mode",
                        "/api/settings/ai/save",
                        "/api/court/create_custom", "/api/court/start_achievement",
                        "/api/event-tree/export",
                        "/api/zhongzhi", "/api/tech/unlock",
                        "/api/dialogue/say", "/api/script-events/consume", "/api/council/cast", "/api/dialogue/start", "/api/dialogue/next", "/api/script-events/refuse",
                        "/api/attendants/set", "/api/attendants/phrases",
                        "/api/attendants/answer"):
            self._send(404, b'{"error":"not found"}')
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)
        except Exception:
            self._send(400, b'{"error":"bad json"}')
            return
        # ── 角色立绘表情导入 ──
        if path == "/api/hum/upload":
            from engine.hum import save_png, imported as _imp
            import base64
            try:
                name = str(data.get("name") or "").strip()
                expr = str(data.get("expr") or "").strip()
                raw = str(data.get("image") or "")
                if ";base64," in raw:
                    raw = raw.split(";base64,", 1)[1]
                blob = base64.b64decode(raw, validate=True)
                if not blob:
                    raise ValueError("空文件")
                save_png(name, expr, blob)
            except Exception as e:                      # noqa: BLE001 导入失败要说清原因
                self._send(200, json.dumps({"ok": False, "msg": "导入失败：%s" % e},
                                           ensure_ascii=False).encode("utf-8"))
            else:
                self._send(200, json.dumps({"ok": True, "msg": "已导入 %s 的「%s」表情" % (name, expr),
                                            "exprs": _imp(name)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/script-events/refuse":
            # 回绝求见：发「对话被拒」标签，剧本可据此走另一条线
            eid = str(data.get("event_id") or "").strip()
            tag = str(data.get("tag") or "").strip() or ("对话被拒:%s" % eid)
            with LOCK:
                from engine import script_events as _se
                line = _se.publish_tag(SESSION.state, tag, eid)
            self._send(200, json.dumps({"ok": True, "msg": line or "已回绝"},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/dialogue/start":
            # 开始一轮会话：近侍会话（cast 由圆按钮给）或剧本对话（cast 由事件给）
            names = data.get("names") or []
            prompt_file = str(data.get("prompt_file") or "attendant_talk.txt")
            # {{script}} 是**主线里的一个预期事件**（这次要谈的那一段），由事件自带，
            # 不是整条主线剧本；调用方没给才退回主线大纲，绝不拿全文顶替
            script = str(data.get("script") or "")
            if not script:
                mid = str(data.get("mainline_id") or "")
                if mid:
                    from engine.mainline_script import get_mainline
                    ml = get_mainline(mid) or {}
                    script = str((ml.get("current_phase_script") or {}).get("outline") or "")
            mode = "dialogue"
            with LOCK:
                ok, msg = SESSION.council.start(
                    "dialogue", names, mode="court",
                    cast_source=str(data.get("cast_source") or "attendant_circles"))
                if ok:
                    ok2, msg2 = SESSION.council.next_dialogue(prompt_file, script)
                    ok, msg = ok2, (msg2 if ok2 else msg2)
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "messages": SESSION.council.messages,
                                        "cast": SESSION.council.roster_names(),
                                        "state": snap}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/dialogue/next":
            # 推进下一轮：玩家发过话就带上 player_line
            with LOCK:
                ok, msg = SESSION.council.next_dialogue(
                    str(data.get("prompt_file") or "attendant_talk.txt"),
                    str(data.get("script") or ""),
                    str(data.get("player_line") or ""))
                msgs = SESSION.council.messages
            self._send(200, json.dumps({"ok": ok, "msg": msg, "messages": msgs},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/council/cast":
            # 会话进行中动态增删参会人（近侍点圆 / 剧本事件）
            names = data.get("names") or []
            with LOCK:
                ok, msg = SESSION.council.set_cast(names)
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "cast": SESSION.council.roster_names() if ok else []},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/script-events/consume":
            ids = data.get("ids") or []
            with LOCK:
                keep = []
                for e in (SESSION.state.pending_script_events or []):
                    eid = str(((e or {}).get("event") or {}).get("event_id") or "")
                    if eid and eid in ids:
                        continue
                    keep.append(e)
                SESSION.state.pending_script_events = keep
                self._send(200, json.dumps({"ok": True, "left": len(keep)},
                                           ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/dialogue/say":
            # 玩家在对话框里发话：先落归档，回一句占位。提示词接入后在这里改调 LLM。
            text = str(data.get("text") or "").strip()
            if not text:
                self._send(200, json.dumps({"ok": False, "msg": "空话不发送"},
                                           ensure_ascii=False).encode("utf-8"))
            else:
                try:
                    from engine.logger import CONSOLE
                    CONSOLE.log("sys", "玩家发言", text)
                except Exception:
                    pass
                self._send(200, json.dumps({"ok": True, "reply": "（尚未接入提示词）"},
                                           ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/hum/adjust":
            from engine.hum import save_adjust, all_adjust, frame_info
            try:
                name = str(data.get("name") or "").strip()
                expr = str(data.get("expr") or "").strip()
                val = save_adjust(name, expr,
                                  data.get("scale", 1.0), data.get("dx", 0.0), data.get("dy", 0.0))
            except Exception as e:                      # noqa: BLE001
                self._send(200, json.dumps({"ok": False, "msg": "保存失败：%s" % e},
                                           ensure_ascii=False).encode("utf-8"))
            else:
                self._send(200, json.dumps({"ok": True, "msg": "已保存调整", "adjust": val,
                                            "all": all_adjust(), "frame": frame_info()},
                                           ensure_ascii=False).encode("utf-8"))
            return

        cmd = (data.get("cmd") or "").strip()

        # ── 军队调动与行军指令 ──
        if path in ("/api/army/move", "/api/army/dispatch"):
            from engine.armies import dispatch_army
            army_id = data.get("army_id") or ""
            target_province = data.get("target_province") or data.get("target_pid") or data.get("target") or ""
            with LOCK:
                ok, msg = dispatch_army(army_id, target_province, SESSION.state)
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg, "state": snap}, ensure_ascii=False).encode("utf-8"))
            return

        # ── 军队欠饷调节 每万两减产 1% ──
        if path in ("/api/army/arrears", "/api/army/pay_rate"):
            from engine.armies import update_army_arrears
            army_id = data.get("army_id") or ""
            arrears_wan = float(data.get("arrears_wan", data.get("arrears", 0.0)))
            with LOCK:
                ok = update_army_arrears(army_id, arrears_wan, SESSION.state)
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "army_id": army_id, "arrears_wan": arrears_wan, "state": snap}, ensure_ascii=False).encode("utf-8"))
            return

        # ── 军队装备编制管理与回收  需求4 ──
        if path == "/api/army/equipment/quota":
            from engine.armies import update_equipment_quota
            army_id = data.get("army_id") or ""
            cat_key = data.get("category") or ""
            model_name = data.get("model") or ""
            quota = int(data.get("quota") or 0)
            with LOCK:
                ok, msg = update_equipment_quota(army_id, cat_key, model_name, quota)
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/army/equipment/delete":
            from engine.armies import delete_equipment_row
            army_id = data.get("army_id") or ""
            cat_key = data.get("category") or ""
            model_name = data.get("model") or ""
            with LOCK:
                ok, msg, reclaimed = delete_equipment_row(army_id, cat_key, model_name, SESSION.state)
            self._send(200, json.dumps({"ok": ok, "msg": msg, "reclaimed": reclaimed}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/army/equipment/add":
            from engine.armies import add_equipment_row
            army_id = data.get("army_id") or ""
            cat_key = data.get("category") or ""
            model_name = data.get("model") or ""
            quota = int(data.get("quota") or 1000)
            with LOCK:
                ok, msg = add_equipment_row(army_id, cat_key, model_name, quota)
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8"))
            return

        # ── 军队态势指令 任务指令/交战风险/撤退优先级与作战指令修改 接入快马传令延迟 ──
        if path == "/api/army/stance_order":
            from engine.relay import dispatch_army_order_delay
            army_id = data.get("army_id") or ""
            mission = data.get("mission")
            risk = data.get("risk")
            retreat = data.get("retreat")
            order = data.get("battle_order")
            with LOCK:
                ok, msg = dispatch_army_order_delay(army_id, mission=mission, risk=risk, retreat=retreat, battle_order=order, state=SESSION.state)
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8"))
            return

        # ── 战报已读标记 ──
        if path == "/api/battles/mark_read":
            with LOCK:
                SESSION.unread_battle_reports = False
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            return

        # ── 角色卡：反复写入 / 实时修改 ──
        if path == "/api/card/save":
            from engine.char_cards import save_card
            name = data.get("name") or ""
            card = data.get("card")
            if not name or not isinstance(card, dict):
                self._send(400, b'{"error":"need name & card"}')
                return
            with LOCK:
                save_card(name, card)
            self._send(200, json.dumps({"ok": True, "name": name},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/card/patch":
            from engine.char_cards import patch_state
            name = data.get("name") or ""
            patch = data.get("patch") or {}
            if not name or not isinstance(patch, dict):
                self._send(400, b'{"error":"need name & patch"}')
                return
            with LOCK:
                try:
                    st = patch_state(name, patch)
                except KeyError:
                    self._send(404, b'{"error":"unknown card"}')
                    return
            self._send(200, json.dumps({"ok": True, "name": name, "state": st},
                                       ensure_ascii=False).encode("utf-8"))
            return

        # ── 召集系统与廷议系统 ──
        if path == "/api/council/start":
            # 口径 玩家定义：召对功能已删除，章奏界面不再接入 AI。
            # 集议只有廷议一条路，从【要紧事物】发起，走 /api/court/start。
            self._send(200, json.dumps({
                "ok": False,
                "msg": "召对功能已移除：章奏不再接入 AI，请到【要紧事物】发起廷议。",
                "state": SESSION.council.state()}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/court/start":
            with LOCK:
                ok, msg = SESSION.council.start(data.get("mainline_id") or data.get("memorial_id"),
                                                data.get("participants") or [],
                                                mode="court",
                                                mainline_id=data.get("mainline_id"),
                                                initial_switches=data.get("data_switches") or [],
                                                imported_mids=data.get("imported_memorial_ids") or [],
                                                book_ids=data.get("book_ids") or [],
                                                report_ids=data.get("report_ids") or [])
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "state": SESSION.council.state()},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path in ("/api/court/switches", "/api/council/switches", "/api/council/switch"):
            # 三个路径同一处理：会话进行中增删调阅的数据/章奏/藏书/战报。
            #  /api/council/switch 原先只在白名单里、没有处理分支，会掉到空命令兜底。
            with LOCK:
                ok = SESSION.council.set_data_switches(data.get("data_switches") or [],
                                                       imported_mids=data.get("imported_memorial_ids"),
                                                       book_ids=data.get("book_ids"),
                                                       report_ids=data.get("report_ids"))
            self._send(200, json.dumps({"ok": ok, "state": SESSION.council.state()}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/court/start_achievement":
            from engine.achievements import can_start
            aid = data.get("achievement_id") or ""
            with LOCK:
                ok_cond, cond_msg = can_start(SESSION.state, aid)
                if not ok_cond:
                    ok, msg = False, cond_msg
                else:
                    ok, msg = SESSION.council.start(aid, data.get("participants") or [],
                                                    mode="achievement", achievement_id=aid)
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "state": SESSION.council.state()},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/council/player":
            with LOCK:
                ok, msg = SESSION.council.player_speak(data.get("text") or "")
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/council/ai-reply":
            # 长 LLM 调用，不持全局锁
            ok, msg = SESSION.council.next_ai_reply()
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "state": SESSION.council.state()},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/council/edit":
            with LOCK:
                ok, msg = SESSION.council.edit_message(data.get("msg_id"),
                                                       data.get("text") or "")
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/council/delete":
            with LOCK:
                ok, msg = SESSION.council.delete_message(data.get("msg_id"))
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/council/pizou":
            ok, msg = SESSION.council.generate_pizou()
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "state": SESSION.council.state()},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/council/end":
            ok, msg = SESSION.council.end()
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "state": SESSION.council.state()},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/court/end_direct":
            ok, msg = SESSION.council.end_court_direct()
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "state": SESSION.council.state()},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/court/end_pizou":
            ok, msg, ops = SESSION.council.end_court_with_pizou()
            with LOCK:
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg, "operations": ops,
                                        "state": SESSION.council.state(),
                                        "world_state": snap},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path in ("/api/council/quick-end", "/api/court/quick-end"):
            ok, msg = SESSION.council.quick_end()
            self._send(200, json.dumps({"ok": ok, "msg": msg,
                                        "state": SESSION.council.state()},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/court/create_custom":
            from engine.mainline_script import create_custom_mainline
            title = data.get("title") or "新定大政"
            cat = data.get("category") or "朝堂大政"
            desc = data.get("desc") or ""
            with LOCK:
                new_entry = create_custom_mainline(title, desc=desc, category=cat)
            self._send(200, json.dumps({"ok": True, "mainline": new_entry}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/settings/reject_mode":
            mode = data.get("mode") or "auto"
            with LOCK:
                SESSION.state.reject_test_mode = mode
                snap = snapshot()
            self._send(200, json.dumps({"ok": True, "mode": mode, "state": snap}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/settings/ai/save":
            from engine.llm import save_system_ai_configs, load_system_ai_configs, DEFAULT_CONFIG_PATH, LLMClient
            g_cfg = data.get("global") or {}
            sys_cfg = data.get("systems") or {}
            with LOCK:
                try:
                    with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
                        json.dump(g_cfg, f, ensure_ascii=False, indent=2)
                    # 保留系统项基础定义，支持合并更新而非直接清空覆盖
                    existing_sys = load_system_ai_configs()
                    for k, v in sys_cfg.items():
                        if k in existing_sys:
                            existing_sys[k].update(v)
                        else:
                            existing_sys[k] = v
                    save_system_ai_configs(existing_sys if sys_cfg else existing_sys)
                    SESSION.llm = LLMClient()
                    SESSION.council.llm = SESSION.llm
                    ok, msg = True, "AI模型与系统设置已保存生效。"
                except Exception as e:
                    ok, msg = False, f"保存失败: {e}"
            self._send(200, json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/card/create_ai":
            name = data.get("name") or ""
            title = data.get("title") or "在任官员"
            from engine.officials import canonical_location, CAPITAL_PROVINCE
            # 人物位置只到省份：一律归到省名 京师诸写法 → 北直隶
            loc = canonical_location(data.get("location")) or CAPITAL_PROVINCE
            from engine.llm import get_llm_for_system
            from engine.council import read_prompt
            from engine.char_cards import save_card
            from engine.factions import sample_random_faction, get_character_faction
            
            # 逻辑引擎先根据中央党派分布与官员库获取/确定党派，作为已知参数传递给 AI
            assigned_faction = get_character_faction(name, title=title, location=loc) or sample_random_faction()

            prompt = read_prompt("create_person.txt")
            if not prompt:
                self._send(500, json.dumps({"ok": False, "msg": "未找到人物生成提示词模板 Prompt/create_person.txt"}, ensure_ascii=False).encode("utf-8"))
                return

            prompt_filled = (prompt.replace("{{char_name}}", name)
                             .replace("{{char_title}}", title)
                             .replace("{{char_location}}", loc)
                             .replace("{{char_faction}}", assigned_faction))
            try:
                gen_llm = get_llm_for_system("character_gen", SESSION.llm)
                if gen_llm.mock:
                    self._send(400, json.dumps({"ok": False, "msg": "未配置大模型 API Key，请先前往左上角【设置⚙️】配置大模型接口参数。"}, ensure_ascii=False).encode("utf-8"))
                    return

                # 档案的结构与写法在 finish_person_card 的说明里；引擎已知的事实由引擎写入
                from engine import person_tools
                from engine.ai_workflow import run_tool_workflow
                messages = [
                    {"role": "system",
                     "content": "你现在将扮演人物档案生成器。用 finish_person_card 工具交回档案。"},
                    {"role": "user", "content": prompt_filled},
                ]
                res = run_tool_workflow(
                    gen_llm, messages, person_tools.TOOLS,
                    state=SESSION.state, session=SESSION,
                    finish_name=person_tools.TOOL_FINISH, max_calls=4,
                    payload_keys=person_tools.FINISH_PAYLOAD_KEYS,
                    finish_handler=lambda a, s, ss: person_tools.finish_card(
                        a, name, title, loc, assigned_faction, s, ss))
                new_card = res["value"]
                if not new_card:
                    self._send(500, json.dumps(
                        {"ok": False, "msg": "AI 未交回人物档案：%s" % (res.get("error") or "")},
                        ensure_ascii=False).encode("utf-8"))
                    return

                with LOCK:
                    save_card(name, new_card)
                self._send(200, json.dumps({"ok": True, "card": new_card, "name": name}, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "msg": f"AI生成失败: {str(e)}"}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/side-run":
            # 测试调用：只读游戏状态 + 长 LLM 调用，不持全局锁 避免阻塞其他请求
            instr = data.get("instruction") or ""
            resp = json.dumps(SESSION.run_side_test(instr), ensure_ascii=False).encode("utf-8")
            self._send(200, resp)
            return
        if path == "/api/mainline/switches":
            from engine.mainline_script import set_script_switches
            with LOCK:
                ok = set_script_switches(data.get("mainline_id") or "", data.get("switches") or [])
            self._send(200, json.dumps({"ok": ok}, ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/tech/unlock":
            tid = data.get("tech_id") or ""
            with LOCK:
                ok, msg = tech.unlock(SESSION.state, tid)
                view = tech.tech_view(SESSION.state)
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg, "tech": view, "state": snap},
                                       ensure_ascii=False).encode("utf-8"))
            return
        if path == "/api/attendants/set":
            from engine import attendants as _att
            index = int(data.get("index", -1))
            name = data.get("name") or ""
            try:
                with LOCK:
                    ch = _att.apply_slot_change(SESSION.state, index, name)
                    slots = _att.slots(SESSION.state)
                self._send(200, json.dumps(
                    {"ok": True, "change": ch, "slots": slots},
                    ensure_ascii=False).encode("utf-8"))
            except ValueError as e:
                self._send(200, json.dumps(
                    {"ok": False, "msg": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/attendants/phrases":
            from engine import attendants as _att
            index = int(data.get("index", 0))
            category = data.get("category") or "应答"
            text = data.get("text") or ""
            try:
                with LOCK:
                    _att.set_phrase(SESSION.state, index, category, text)
                    slots = _att.slots(SESSION.state)
                self._send(200, json.dumps({"ok": True, "slots": slots},
                                           ensure_ascii=False).encode("utf-8"))
            except ValueError as e:
                self._send(200, json.dumps(
                    {"ok": False, "msg": str(e)}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/attendants/answer":
            from engine import attendants as _att
            choice = data.get("choice") or ""
            with LOCK:
                res = _att.answer_prompt(SESSION.state, choice)
                snap = snapshot()
            self._send(200, json.dumps({"ok": res.get("ok", False), "res": res, "state": snap},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/zhongzhi":
            command = data.get("command") or data.get("cmd") or ""
            with LOCK:
                res = SESSION.issue_zhongzhi(command)
                snap = snapshot()
            self._send(200, json.dumps({"ok": res.get("ok", False), "msg": res.get("msg", ""),
                                        "res": res, "state": snap}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/memorial":
            mid = data.get("id") or ""
            action = data.get("action") or ""
            text = data.get("text") or ""
            with LOCK:
                ok, msg = SESSION.memorial_act(mid, action, text)
            self._send(200, json.dumps({"ok": ok, "msg": msg},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/tick":
            # 时间推进：按分钟走。跨月时补一次完整月度结算，并让前端暂停
            from engine.time import advance_minute, advance_month
            from engine.session import build_report
            try:
                minutes = int(data.get("minutes") or 1)
            except (TypeError, ValueError):
                minutes = 1
            minutes = max(1, min(minutes, 600))
            with LOCK:
                st = SESSION.state
                before = (st.date.get("year"), st.date.get("month"))
                advance_minute(st, minutes)
                after = (st.date.get("year"), st.date.get("month"))
                lines, pause = [], False
                if after != before:
                    # advance_minute 已静默进月：退回上月末，再用 advance_month 走完整结算
                    st.date["month"], st.date["year"] = before[1], before[0]
                    st.date["day"], st.date["hour"], st.date["minute"] = 30, 23, 59
                    notes = advance_month(st)
                    st.date["minute"] = 0           # 月初从 00:00 起算，不带上次结算的残分钟
                    lines = build_report(st, notes).split(chr(10))
                    pause = True
                # 实时推进也要走一次近侍问寝（20:00 检查），否则只有手动推进才会问
                lines = lines + SESSION._attendant_night_check()
                snap = snapshot()
            self._send(200, json.dumps({"ok": True, "lines": lines, "pause": pause,
                                        "state": snap}, ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/emperor/move":
            name = (data.get("name") or "").strip()
            with LOCK:
                from engine.depts_palaces import room_names
                from engine.time import advance_minute
                if name not in room_names():
                    ok, msg = False, "未知殿宇：%s" % name
                elif SESSION.state.emperor_location == name:
                    ok, msg = True, "圣躬已在【%s】，无需移动。" % name
                else:
                    SESSION.state.emperor_location = name
                    advance_minute(SESSION.state, 10)   # 宫城内任两处之间一律耗时 10 分钟
                    ok, msg = True, "圣躬移驾【%s】 耗时 10 分钟。" % name
                from engine.logger import CONSOLE
                CONSOLE.log("palace", msg if ok else "移驾失败：%s" % msg, "殿宇=%s" % name)
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg, "state": snap},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/sacrifice/schedule":
            sac_id = data.get("id") or ""
            with LOCK:
                from engine.depts_rites import schedule_sacrifice, clear_schedule
                if data.get("clear"):
                    ok, msg = clear_schedule(SESSION.state, sac_id)
                else:
                    ok, msg = schedule_sacrifice(SESSION.state, sac_id,
                                                 int(data.get("year") or 0),
                                                 int(data.get("month") or 0),
                                                 int(data.get("day") or 0))
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg, "state": snap},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/sacrifice/attend":
            sac_id = data.get("id") or ""
            with LOCK:
                from engine.depts_rites import start_sacrifice, due_sacrifice
                due = due_sacrifice(SESSION.state)
                lines = []
                if not due or due.get("id") != sac_id:
                    ok, msg = False, "该祭今日未在行礼之期 须先定行礼日，且已届期"
                else:
                    ok, msg = start_sacrifice(SESSION.state, sac_id)
                    if ok:
                        # 一场祭祀直耗一日：确认前往后即加 24 小时
                        lines = SESSION.run("j 1d")
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg, "lines": lines, "state": snap},
                                       ensure_ascii=False).encode("utf-8"))
            return

        if path == "/api/sacrifice/decline":
            sac_id = data.get("id") or ""
            with LOCK:
                from engine.depts_rites import decline_sacrifice
                ok, msg = decline_sacrifice(SESSION.state, sac_id)
                snap = snapshot()
            self._send(200, json.dumps({"ok": ok, "msg": msg, "state": snap},
                                       ensure_ascii=False).encode("utf-8"))
            return

        # ── 事件树归档导出为 txt 落在项目根目录 default-user/──
        if path == "/api/event-tree/export":
            idx = int(data.get("session", 0) or 0)
            ev = data.get("event")
            with LOCK:
                sessions = SESSION.list_script_sessions()
                if idx < 0 or idx >= len(sessions):
                    self._send(404, json.dumps({"ok": False, "error": "没有这条归档记录：%d" % idx},
                                               ensure_ascii=False).encode("utf-8"))
                    return
                from engine.event_export import export_session_txt
                try:
                    path_out = export_session_txt(
                        sessions[idx], event_index=(int(ev) if ev is not None else None))
                except (KeyError, IndexError) as e:
                    self._send(404, json.dumps({"ok": False, "error": str(e)},
                                               ensure_ascii=False).encode("utf-8"))
                    return
            self._send(200, json.dumps({"ok": True, "path": path_out,
                                        "msg": "已导出到 %s" % path_out},
                                       ensure_ascii=False).encode("utf-8"))
            return

        with LOCK:
            lines = SESSION.run(cmd)
            snap = snapshot()
        resp = json.dumps({"lines": lines, "state": snap}, ensure_ascii=False).encode("utf-8")
        self._send(200, resp)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print ("天启七年 网页外壳")
    print("LLM 模式：%s 游戏内输入 diag 可校验 AI 连接"
          % ("mock 未配置 api_key" if SESSION.llm.mock else "真实 API"))
    print("判定层：%s" %  "解码中 后台，首次约 30-60 秒" if not DETECTOR.ready else "已就绪")
    print ("打开 http://127.0.0.1:%d  Ctrl+C 退出" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print ("\n圣驾回銮。")
        srv.server_close()


if __name__ == "__main__":
    main()
