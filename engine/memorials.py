"""章奏生成与事件管理引擎 四类事件体系。

事件四类体系：
1. 一类主要事件 source=mainline：由大模型/事件驱动根据大量信息思考推进 l 层。
2. 二类次要事件 source=side：由小模型/Prompt 思考后生成 m 层。
3. 三类不重要事件 source=filler：由随机数用模板文本生成 n 层。
4. 四类玩家发起事件 source=player_origin：玩家发起中旨转化为系统指令或正式章奏。

机制：
- 双时间戳：以送达时间 今日为基准，根据驿站传播时间 travel_days倒推发生时间。
- 撰写人抽取：由逻辑引擎从吏部/兵部官员名单及人物统计库，结合地区与职能词符匹配抽取。
- 双票拟与双奖励：每份章奏带处理/不处理票拟与奖励，由涉事人党派 vs 内阁多数党派决定触发。
- 变化池：游戏中的数据变化 如灾异、官员更替进入变化池，供生成时抽取为事件点。
"""

import json
import os
import random
import re
from typing import Any, Dict, List, Optional

from .logger import CONSOLE
from .llm import parse_jsonl
from .officials import list_active
from .factions import evaluate_memorial_decision, get_character_faction
from .time import date_key, date_shift_key

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD_DIR = os.path.join(_BASE, "world")
PROMPT_DIR = os.path.join(_BASE, "Prompt")

CATEGORIES = [
    "钱粮类", "人事类", "刑名类", "军务类", "工程类", "奏本类",
    "弹劾疏", "规谏疏", "灾异奏报", "边情塘报", "密揭", "请封疏"
]
FORMATS = ["题本", "奏本", "表", "揭帖", "塘报", "露布"]

SURNAMES = list("王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文")
GIVEN_NAMES = ["文远", "伯安", "子明", "仲达", "公谨", "元直", "士元", "孝先",
               "景升", "孟起", "汉升", "子龙", "奉孝", "文若", "公达", "幼平",
               "兴霸", "德谋", "承渊", "文则", "妙才", "元让", "仲康", "公明",
               "子义", "伯符", "仲谋", "子敬", "公瑾", "德润", "子山", "休穆",
               "伯言", "承宗", "元化", "景和", "子实", "用宾", "本初", "公台",
               "文和", "孝直", "伯约", "士季", "文长", "幼常", "公休", "文鸯",
               "元俭", "子初", "季常", "德信", "文虎", "子衡", "公绩", "元放"]


def _rng():
    return random.Random()


def _name(r):
    return r.choice(SURNAMES) + r.choice(GIVEN_NAMES)


def _num(r, lo, hi):
    return r.randint(lo, hi)


def read_prompt(name: str) -> str:
    """读 Prompt 文件夹中的提示词/模板文件。"""
    try:
        with open(os.path.join(PROMPT_DIR, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def extract_history(state, n=6) -> str:
    """从主线剧情 台账抽取故事大纲；开局世界总结始终置首。"""
    lines = [j["text"] for j in getattr(state, "journal", []) if j.get("month") == "开局"]
    lines += [j["text"] for j in getattr(state, "journal", [])[-n:] if j.get("month") != "开局"]
    if lines:
        return "\n".join(lines)
    return read_prompt("ceshi1.txt") or "大明天启七年八月，熹宗寝疾，朝局震荡。"


# ── 事发地 → 传递天数 省际最短路径用时总和──

from .distances import find_shortest_path, PROVINCE_NAMES

PROV_NAME_TO_ID = {v: k for k, v in PROVINCE_NAMES.items()}
PROV_NAME_TO_ID["京师"] = "beizhili"
PROV_NAME_TO_ID["北京"] = "beizhili"
PROV_NAME_TO_ID["顺天"] = "beizhili"


def travel_days(location: str, state: Any = None) -> int:
    """事发地 -> 公文从该地经由省际最短路驿站到达北京的单程传递天数 至少 1 天。"""
    if not location or "京师" in location or "北京" in location or "顺天" in location or "北直隶" in location:
        return 1

    src_pid = "beizhili"
    for p_name, pid in PROV_NAME_TO_ID.items():
        if p_name in location:
            src_pid = pid
            break

    if src_pid == "beizhili":
        return 1

    res = find_shortest_path(src_pid, "beizhili", state)
    t_days = res.get("time_days", 2.0)
    return max(1, int(round(t_days)))


def _stamp(d: Dict[str, Any], state, delivery_time: Optional[str] = None) -> Dict[str, Any]:
    """以送达时间为唯一标准，通过驿站时间倒推发生时间。"""
    today = delivery_time or date_key(state.date)
    d["delivery_time"] = today
    days = travel_days(d.get("Location", ""), state)
    d["event_time"] = date_shift_key(state.date, -days)
    return d


# ── 撰写人词符匹配抽取 ──

def pick_memorial_writer(location: str = "", content: str = "", title: str = "",
                         category: str = "", format_type: str = "") -> str:
    """从吏部/兵部官员名单及人物统计库，结合地区与职能词符匹配抽取撰写人。"""
    act = list_active()

    # 提取省份
    prov = ""
    provs = ["北直隶", "南直隶", "山东", "山西", "河南", "陕西", "四川",
             "湖广", "江西", "浙江", "福建", "广东", "广西", "云南", "贵州", "辽东"]
    for p in provs:
        if p in location or p in content or p in title:
            prov = p
            break

    # 1. 规谏 / 言官 / 封驳 / 给事中
    if "封驳" in title or "六科" in title or "规谏" in category or "给事中" in content:
        ke_officials = [o for o in act if "给事中" in o.get("title", "")]
        if ke_officials:
            o = random.choice(ke_officials)
            return "%s %s" % (o["title"], o["name"])
        return "吏科都给事中 周之纲"

    # 2. 都察院 / 监察御史
    if "御史" in content or "弹劾" in category:
        censors = [o for o in act if "御史" in o.get("title", "") or "巡按" in o.get("title", "")]
        if prov:
            censors_prov = [o for o in censors if o.get("province") == prov]
            if censors_prov:
                o = random.choice(censors_prov)
                return "%s %s" % (o["title"], o["name"])
        if censors:
            o = random.choice(censors)
            return "%s %s" % (o["title"], o["name"])

    # 3. 军情 / 塘报 / 露布 / 边备
    if format_type in  ("塘报", "露布" or "军务" in category or "总兵" in content):
        generals = [o for o in act if "总兵" in o.get("title", "") or "督师" in o.get("title", "")]
        if prov:
            gen_prov = [o for o in generals if o.get("province") == prov]
            if gen_prov:
                o = random.choice(gen_prov)
                return "%s %s" % (o["title"], o["name"])
        if generals:
            o = random.choice(generals)
            return "%s %s" % (o["title"], o["name"])

    # 4. 巡抚 地方大员上奏题本
    if prov:
        fuyus = [o for o in act if "巡抚" in o.get("title", "") and o.get("province") == prov]
        if fuyus:
            o = random.choice(fuyus)
            return "钦差巡抚%s等处地方都察院右副都御史 %s" % (prov, o["name"])

    # 5. 知府 / 府尹
    if location:
        prefects = [o for o in act if "知府" in o.get("title", "") and (o.get("location") in location or o.get("province") in location)]
        if prefects:
            o = random.choice(prefects)
            return "%s %s" % (o["title"], o["name"])

    # 6. 中央部院 尚书/侍郎/内阁
    if "户部" in content or "钱粮" in category:
        return "户部尚书 郭允厚"
    if "兵部" in content or "军务" in category:
        return "兵部尚书 王在晋"
    if "工部" in content or "工程" in category:
        return "工部尚书 崔呈秀"
    if "刑部" in content or "刑名" in category:
        return "刑部尚书 薛贞"
    if "吏部" in content or "人事" in category:
        return "吏部尚书 王永光"
    if "礼部" in content or "请封" in category or "礼仪" in category:
        return "礼部尚书 来宗道"
    if "内阁" in content or format_type == "揭帖" or "密揭" in category:
        return "内阁首辅武英殿大学士 黄立极"

    # 默认兜底
    if prov:
        return "钦差巡抚%s等处地方都察院右副都御史 %s" % prov, _name_rng
    return "通政使司通政使 曾秉正"


# ── 不重要事件 unzhixian / n 层：硬编码模板库 engine/unzhixian_data.py ──

def load_filler_templates() -> List[Dict[str, Any]]:
    """读取不重要事件 unzhixian全量硬编码模板库。"""
    try:
        from .unzhixian_data import get_unzhixian_templates
        return get_unzhixian_templates()
    except ImportError:
        try:
            with open(os.path.join(WORLD_DIR, "filler_events.json"), encoding="utf-8") as f:
                return json.load(f).get("templates", []) or []
        except OSError:
            return []


PROV_FU_MAP = {
    "北直隶": ["顺天府", "保定府", "真定府", "河间府", "顺德府", "广平府", "大名府"],
    "山东": ["济南府", "东昌府", "青州府", "兖州府", "登州府", "莱州府"],
    "山西": ["太原府", "平阳府", "大同府", "潞安府", "汾州府"],
    "河南": ["开封府", "河南府", "归德府", "南阳府", "汝宁府", "彰德府", "卫辉府", "怀庆府"],
    "陕西": ["西安府", "凤翔府", "汉中府", "延安府", "平凉府", "巩昌府", "庆阳府"],
    "南直隶": ["应天府", "苏州府", "松江府", "常州府", "镇江府", "扬州府", "淮安府", "徽州府", "安庆府"],
    "浙江": ["杭州府", "嘉兴府", "湖州府", "宁波府", "绍兴府", "台州府", "金华府", "衢州府", "严州府", "温州府", "处州府"],
    "江西": ["南昌府", "九江府", "饶州府", "广信府", "南康府", "抚州府", "建昌府", "吉安府", "赣州府"],
    "湖广": ["武昌府", "汉阳府", "黄州府", "承天府", "德安府", "荆州府", "襄阳府", "岳州府", "长沙府", "宝庆府", "衡州府"],
    "四川": ["成都府", "保宁府", "顺庆府", "重庆府", "夔州府", "叙州府", "龙安府", "马湖府"],
    "福建": ["福州府", "泉州府", "建宁府", "延平府", "汀州府", "兴化府", "邵武府", "漳州府"],
    "广东": ["广州府", "韶州府", "南雄府", "惠州府", "潮州府", "肇庆府", "高州府", "廉州府", "雷州府", "琼州府"],
    "广西": ["桂林府", "柳州府", "庆远府", "思恩府", "思明府", "平乐府", "梧州府", "浔州府", "南宁府"],
    "云南": ["云南府", "大理府", "临安府", "楚雄府", "澂江府", "曲靖府", "姚安府"],
    "贵州": ["贵阳府", "安顺府", "都匀府", "镇远府", "思南府", "思州府", "平越府"],
    "辽东": ["辽阳府", "广宁卫", "宁远卫", "锦州卫", "海州卫"],
}


def render_filler(tpl: Dict[str, Any], r, state=None) -> Dict[str, Any]:
    """根据 Prompt/unzhixian 规范与规则.txt 渲染不重要支线事件。"""
    sub_cat = tpl.get ("sub_category", "弹劾地方")
    act = list_active()

    # 1. 确定事发省份与地点
    prov_name = "陕西"
    ming_provs = ["陕西", "山西", "山东", "河南", "南直隶", "浙江", "江西", "湖广", "四川", "福建", "广东", "广西", "云南", "贵州", "辽东", "北直隶"]

    fu = "西安府"
    if sub_cat == "弹劾中央":
        prov_name = "北直隶"
        fu = "顺天府"
        location_str = "京师顺天府"
    elif sub_cat in  ("小黄汛", "大黄汛"):
        prov_name = r.choice ["河南", "山东", "南直隶"]
        fu = r.choice(PROV_FU_MAP.get(prov_name, ["开封府"]))
        location_str = f"{prov_name}{fu}"
    else:
        # 如果有特定灾害省份则优先选择
        disaster_provs = []
        if state and hasattr(state, "provinces"):
            for pid, p in state.provinces.items():
                p_loc = p.get("local") or {}
                w = p_loc.get("weather") or {}
                if w.get("disaster"):
                    disaster_provs.append(p.get("name", pid))
        if disaster_provs and sub_cat in  ("旱灾", "水涝", "高温", "严寒", "台风"):
            prov_name = r.choice(disaster_provs)
        else:
            prov_name = r.choice(ming_provs)

        fu = r.choice(PROV_FU_MAP.get(prov_name, ["西安府"]))
        location_str = f"{prov_name}{fu}"

    # 2. 抽取巡抚人名
    fuyus = [o for o in act if "巡抚" in o.get("title", "") and o.get("province") == prov_name]
    if fuyus:
        gov_name = r.choice(fuyus)["name"]
    else:
        gov_name = _name(r)

    # 3. 抽取府官 职位与人名绑定
    pos_options = ["知府", "分巡道", "兵备道", "按察司副使", "布政司参政", "粮储道", "都转运盐使", "管河同知", "通判"]
    pos_name = r.choice(pos_options)
    local_officials = [o for o in act if o.get("province") == prov_name and "巡抚" not in o.get("title", "")]
    if local_officials:
        matched = r.choice(local_officials)
        fu_name = matched["name"]
        if matched.get("title"):
            # 职位分隔既可能是顿号「、」也可能是旧的间隔点「·」，两种都切
            pos_name = re.split(r"[、·]", matched["title"])[-1]
    else:
        fu_name = _name(r)

    # 4. 抽取中央人名 按 level 属性取，不按地点文本
    central_officials = [o for o in act if o.get("level") == "capital"]
    if central_officials:
        matched_c = r.choice(central_officials)
        central_name = matched_c["name"]
        central_title = matched_c["title"]
    else:
        central_name = _name(r)
        central_title = "通政使司通政使"

    # 5. 随机人名字典  同编号同名
    random_names = {i: _name(r) for i in range(1, 15)}

    content = tpl.get("content_template", "")
    draft_proc = tpl.get("draft_processed", "")
    draft_unproc = tpl.get("draft_unprocessed", "")
    reward_proc = tpl.get("reward_processed", "")
    reward_unproc = tpl.get("reward_unprocessed", "")

    # 执行文本替换
    def replace_vars(text: str) -> str:
        if not text:
            return ""
        # 巡抚
        text = re.sub(r"[（(]巡抚人名[）)]", gov_name, text)
        text = re.sub(r"[（(]巡抚地方[）)]", prov_name, text)
        # 府官职位与人名
        text = re.sub(r"[（(]官员职位[）)]", pos_name, text)
        text = re.sub(r"[（(]府官人名1?[）)]", fu_name, text)
        # 中央人名
        text = re.sub(r"[（(]中央人名1?[）)]", central_name, text)
        # 地震地点
        text = re.sub(r"[（(]地震发生地点府级知府[）)]", f"{prov_name}{fu}知府", text)
        text = re.sub(r"[（(]地震发生地点府级[）)]", f"{prov_name}{fu}", text)
        # 随机时间
        text = re.sub(r"[（(]随机时间[）)]", r.choice(["上月", "季初", "近日", "望日"]), text)
        # 随机人名
        for i in range(1, 15):
            text = re.sub(rf"[（(]随机人名{i}[）)]", random_names[i], text)
        # 随机数  随机数X-Y
        def _repl_num(m):
            lo, hi = int(m.group(1)), int(m.group(2))
            return str(r.randint(lo, hi))
        text = re.sub(r"[（(]随机数(\d+)-(\d+)[）)]", _repl_num, text)
        return text

    content = replace_vars(content)
    draft_proc = replace_vars(draft_proc)
    draft_unproc = replace_vars(draft_unproc)
    reward_proc = replace_vars(reward_proc)
    reward_unproc = replace_vars(reward_unproc)

    title = tpl.get("title", "题本 地方奏报事")
    if sub_cat in  ("弹劾地方", "贪腐"):
        title = f"{title} {pos_name} {fu_name}"
        accused_name = fu_name
        accused_title = pos_name
    elif sub_cat == "弹劾中央":
        title = f"{title} {central_title} {central_name}"
        accused_name = central_name
        accused_title = central_title
    else:
        accused_name = ""
        accused_title = ""

    writer = pick_memorial_writer(location_str, content, title,
                                  tpl.get("category", "言官"), tpl.get("format", "题本"))

    d = {
        "title": title,
        "from": writer,
        "content": content,
        "format": tpl.get ("format", "题本"),
        "category": tpl.get ("category", "言官"),
        "urgent": bool(tpl.get("urgent")),
        "Location": location_str,
        "accused_name": accused_name,
        "accused_title": accused_title,
        "draft_processed": draft_proc,
        "draft_unprocessed": draft_unproc,
        "reward_processed": reward_proc,
        "reward_unprocessed": reward_unproc,
    }

    # 党派决策分析
    dec = evaluate_memorial_decision(d, state)
    d.update(dec)
    return d


def generate_filler(count: int, state=None, id_fn=None) -> List[Dict[str, Any]]:
    """不重要事件 n 层：从 world/filler_events.json 模板生成，零 LLM。"""
    templates = load_filler_templates()
    if not templates:
        return []
    out = []

    # 按照条件筛选适格模板
    curr_month = state.date["month"] if state and hasattr(state, "date") else 8
    # 常态类别
    normal_cats = {"弹劾地方", "弹劾中央", "贪腐", "举荐"}
    # 丰收类别
    harvest_cats = set()
    if curr_month in (5, 6):
        harvest_cats.add ("夏丰收")
    if curr_month in (8, 9):
        harvest_cats.add ("秋丰收")

    # 灾害类别
    disaster_cats = set()
    if state and hasattr(state, "provinces"):
        for p in state.provinces.values():
            loc = p.get("local") or {}
            w = loc.get("weather") or {}
            dis = w.get("disaster") or ""
            if "旱" in dis: disaster_cats.add ("旱灾")
            if "涝" in dis: disaster_cats.add ("水涝")
            if "热浪" in dis: disaster_cats.add ("高温")
            if "寒潮" in dis: disaster_cats.add ("严寒")
            if "台风" in dis: disaster_cats.add ("台风")
            if "大黄汛" in dis: disaster_cats.add ("大黄汛")
            elif "黄汛" in dis: disaster_cats.add ("小黄汛")
            if loc.get("earthquake_penalty"): disaster_cats.add ("地震")

    active_cats = normal_cats | harvest_cats | disaster_cats

    candidate_templates = [t for t in templates if t.get("sub_category") in active_cats]
    if not candidate_templates:
        candidate_templates = [t for t in templates if t.get("sub_category") in normal_cats] or templates

    for i in range(count):
        r = _rng()
        tpl = r.choice(candidate_templates)
        d = render_filler(tpl, r, state=state)
        d["id"] = id_fn("n") if id_fn else "n-%d" % (i + 1)
        d["source"] = "filler"
        d["status"] = "new"
        d["date"] = ""
        if state is not None:
            _stamp(d, state)
        out.append(d)
    return out


# ── 次要事件 m 层：AI 生成 Prompt/zhixian.txt──

def extract_event_points(state) -> str:
    """近期 30 天内变化点清单的文本形式 控制台与日志用；同一窗口口径见 variations.recent_variations。"""
    from .variations import recent_variations, format_variations_for_prompt

    rows = recent_variations(state, days=30)
    if rows:
        res = format_variations_for_prompt(rows)
        if res.strip():
            return res

    # 若过去30天内暂无异动，返回地方微观异动条目
    return "[var-001] 【陕西】赋税催科急迫，民情浮动\n[var-002] 【山东】登莱海防戒严，有司查缉私盐\n[var-003] 【四川】川东粮台转运稍迟，驻军严查关隘"


def build_side_prompt(state, count: int = 1, extra_instruction: str = "") -> str:
    """填章奏提示词里的**数据变量**。

    变化清单、超控范围、奖惩规则都不再塞进提示词——它们由 AI 工具按需取
     见 engine/memorial_tools.py：list_variations / get_variation_scope / get_reward_rules。
    """
    from .council import _ensure_timestamp

    prompt = read_prompt("zhixian.txt")
    if not prompt:
        return ""
    out = prompt.replace("{{count}}", str(count))
    return _ensure_timestamp(out, state.date if state is not None else None)


def generate_side(state, llm, count=2, extra_instruction="", id_fn=None) -> List[Dict[str, Any]]:
    """次要事件 m 层生成：跑 Prompt/zhixian.txt 的 AI 工具工作流。

    每条章奏都是一次独立的工作流 取清单 → 查范围 → 查规则 → 交卷；
    没配 key、模型不交卷或工具报错时，退回确定性 mock 不编造假章奏入库。
    """
    from .variations import normalize_variation_type

    if llm is None or getattr(llm, "mock", True):
        return _side_mock(count, state=state, id_fn=id_fn)

    prompt = build_side_prompt(state, count=count, extra_instruction=extra_instruction)
    if not prompt:
        CONSOLE.log("side", "缺提示词", "未找到 Prompt/zhixian.txt，改用 mock")
        return _side_mock(count, state=state, id_fn=id_fn)

    from .memorial_workflow import run_memorial_workflow

    events: List[Dict[str, Any]] = []
    for _ in range(count):
        res = run_memorial_workflow(state, llm, prompt,
                                    already_titles=[e.get("title") or "" for e in events])
        if not res["event"]:
            break
        events.append(res["event"])

    if not events:
        CONSOLE.log("side", "章奏工作流失败，改用 mock", "一条也没交卷")
        return _side_mock(count, state=state, id_fn=id_fn)

    out = []
    for ev in events[:count]:
        loc_str = ev.get("Location") or "陕西西安府"
        cat = ev.get("content_label") or "灾异奏报"
        fmt = ev.get("category_label") or "题本"
        title = ev.get("title") or "题本 地方奏报事"
        content = ev.get("full_text") or ""
        writer = pick_memorial_writer(loc_str, content, title, cat, fmt)
        vid = ev.get("variation_id") or "var-001"

        # 匹配绑定的变化类型 超控范围由它现算，见 session._generate_next_event
        v_item = next((v for v in state.variation_pool if v.get("id") == vid), None)
        vtype = normalize_variation_type(v_item.get("type", "") if v_item else "disaster", (v_item.get("text", "") if v_item else "") + title)

        d = {
            "id": id_fn("m") if id_fn else "m-%d" % (len(out) + 1),
            "title": title,
            "from": writer,
            "content": content,
            "format": fmt,
            "category": cat,
            "source": "side",
            "urgent": False,
            "status": "new",
            "date": "",
            "Location": loc_str,
            "variation_id": vid,
            "vtype": vtype,
            "draft_processed": ev.get("draft_processed") or "据奏事关地方大局，著该抚按亲勘确勘，酌议具奏。",
            "draft_unprocessed": ev.get("draft_unprocessed") or "事关紧要，尚需确核，原疏暂留候再议。",
            "reward_processed": ev.get("reward_processed") or "积极处理：地方安抚，民怨缓和。",
            "reward_unprocessed": ev.get("reward_unprocessed") or "消极放任：地方矛盾潜伏，民怨上升。",
        }
        if state is not None:
            _stamp(d, state)
        dec = evaluate_memorial_decision(d, state)
        d.update(dec)
        out.append(d)

    return out


def _side_mock(count: int, state=None, id_fn=None) -> List[Dict[str, Any]]:
    """mock 兜底生成次要事件。"""
    out = []
    templates = [
        {
            "title": "题本 山东济南府旱荒赈济疏",
            "Location": "山东济南府历城县",
            "category": "灾异奏报",
            "format": "题本",
            "content": "臣山东巡抚谨题，为地方亢旱成灾、恳请动支帑银蠲赈事。伏查济南等府，入夏以来赤地千里，禾稼枯焦，饥民嗷嗷待哺。臣等公同勘酌，拟请动支国库存银三万两发赈，以安民心。\n伏乞圣裁。",
            "draft_processed": "灾情重大，关乎民命。拟敕户部即发银两，饬令该抚亲行平籴赈济，毋致流殍。",
            "draft_unprocessed": "度支告匮，辽饷方殷，原疏宜暂留中，责成该抚动用本省常平仓谷设法调剂。",
            "reward_processed": "赈济得力：灾情平复，百姓态度 +5，国库支出 3.0万两",
            "reward_unprocessed": "赈务稽延：饥民聚散，地方治安 -3%，地方腐败 +0.2%",
            "vtype": "disaster_weather",
            "province": "山东"
        },
        {
            "title": "题本 浙江海防备寇增设汛船疏",
            "Location": "浙江宁波府鄞县",
            "category": "边情塘报",
            "format": "题本",
            "content": "臣浙江巡抚谨题，为防范外洋红毛与海寇肆扰事。伏查近来东南海面番船出没，汛防哨船多有损坏。拟请工部拨造战船二十艘，选练水勇，以固海疆。\n伏乞圣裁。",
            "draft_processed": "海防紧要，宜修戎备。拟敕工部核准造船之费，兵部督令沿海严防。",
            "draft_unprocessed": "近闻海氛稍敛，动工造船费耗繁多，原疏宜暂留中，令该抚就现存船只整饬巡防。",
            "reward_processed": "海防坚固：水陆策应，海疆安定，地方治安 +4%",
            "reward_unprocessed": "防务疏漏：海盗袭扰，沿海商贾受损，地方态度 -3%",
            "vtype": "military_defense_war",
            "province": "浙江"
        }
    ]
    for i in range(count):
        t = templates[i % len(templates)]
        loc_str = t["Location"]
        writer = pick_memorial_writer(loc_str, t["content"], t["title"], t["category"], t["format"])
        vtype = t.get("vtype", "disaster_weather")
        d = {
            "id": id_fn("m") if id_fn else "m-%d" % (i + 1),
            "title": t["title"],
            "from": writer,
            "content": t["content"],
            "format": t["format"],
            "category": t["category"],
            "source": "side",
            "urgent": False,
            "status": "new",
            "date": "",
            "Location": loc_str,
            "variation_id": f"var-mock-{i+1:03d}",
            "vtype": vtype,
            "draft_processed": t["draft_processed"],
            "draft_unprocessed": t["draft_unprocessed"],
            "reward_processed": t["reward_processed"],
            "reward_unprocessed": t["reward_unprocessed"],
        }
        if state is not None:
            _stamp(d, state)
        dec = evaluate_memorial_decision(d, state)
        d.update(dec)
        out.append(d)
    return out


# ── 主要事件 l 层──

def generate_mainline(state, llm, mock=True, id_fn=None) -> List[Dict[str, Any]]:
    """主要事件 l 层占位：事件驱动 从台账取事实。"""
    out = []
    events = [j["text"] for j in getattr(state, "journal", [])[-6:]][-3:]
    if events:
        last = events[-1]
        loc = "辽东宁远卫"
        writer = pick_memorial_writer(loc, last, "兵部塘报 军情要闻", "边情塘报", "塘报")
        d = {
            "id": id_fn("l") if id_fn else "l-1",
            "title": "兵部塘报 军情要闻",
            "from": writer,
            "content": "塘报：据边镇奏报，近事如下——\n%s\n伏乞圣裁。 主要事件，事件驱动生成" % last,
            "format": "塘报",
            "category": "边情塘报",
            "source": "mainline",
            "urgent": True,
            "status": "new",
            "date": "",
            "Location": loc,
            "draft_processed": "边务第一要著，拟敕督抚将士同心戮力，相机战守。",
            "draft_unprocessed": "边报未确，留中再勘。",
            "reward_processed": "边防激励：辽东士气 +5",
            "reward_unprocessed": "延误战机：边关戒严，军费耗费增加",
        }
        _stamp(d, state)
        dec = evaluate_memorial_decision(d, state)
        d.update(dec)
        out.append(d)
    else:
        loc = "京师"
        writer = "内阁首辅武英殿大学士 黄立极"
        d = {
            "id": id_fn("l") if id_fn else "l-1",
            "title": "内阁揭帖 机务密陈",
            "from": writer,
            "content": "臣等密揭：当今政务，首在辽东与抚民。朝局初定，伏惟圣明乾纲独断，留意庶政。 主要事件占位",
            "format": "揭帖",
            "category": "密揭",
            "source": "mainline",
            "urgent": True,
            "status": "new",
            "date": "",
            "Location": loc,
            "draft_processed": "拟遵圣谕，通饬内外百司恪共乃职。",
            "draft_unprocessed": "机务崇闳，原揭留中。",
            "reward_processed": "朝纲整饬：中央威望提升",
            "reward_unprocessed": "因循守旧：朝政平淡",
        }
        _stamp(d, state)
        dec = evaluate_memorial_decision(d, state)
        d.update(dec)
        out.append(d)
    return out


def generate_daily(state, llm, counts=None, snaps=None, id_fn=None) -> List[Dict[str, Any]]:
    """生成当日章奏一批。默认：主线 1 + 支线 2 + 填充 4。"""
    counts = counts or {"mainline": 1, "side": 2, "filler": 4}
    batch = []
    if counts.get("mainline"):
        batch += generate_mainline(state, llm, id_fn=id_fn)
    if counts.get("side"):
        batch += generate_side(state, llm, counts["side"], id_fn=id_fn)
    if counts.get("filler"):
        batch += generate_filler(counts["filler"], state=state, id_fn=id_fn)
    return batch
