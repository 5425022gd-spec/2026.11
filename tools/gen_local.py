"""地方数据生成器：为 world/provinces.json 各省注入 local 字段。

数据模型（玩家预案，派生值不写入）：
- core 核心情况：核心/宣称/占领/非核（国家认同的统称）
- classes 地方阶级（宗室/士绅/商人/农民/百工/百役/流民）：
  每阶级 {pop 人口(万), attitude 态度,
          output 产出(万两/月), tax_rate 税收比例, support 扶持(两/月)}
  派生：地方人口 = 各阶级人口总和（每月 +0.1%）；税收 = 产出 × 税收比例
- infra 基建程度 0-100
- controller 当前控制权（明/满清/蒙古诸部/诸部）：全国＝当前属明朝控制的省份
- army 军队（机动部队，整个留空）：{}
- projects 地方工程（要塞/城池/工程/港埠）
- carrying 可承载人口(万)
- weather 天气（动态，由 engine/weather.py 生成，不入此表）

运行：python tools/gen_local.py
"""

import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD = os.path.join(BASE, "world", "provinces.json")

# 产出系数（万两 / 万人 / 月）与税收比例
OUTPUT_RATE = {"士绅": 0.30, "商人": 0.40, "农民": 0.015, "百工": 0.08, "百役": 0.02}
TAX_RATE = {"宗室": 0.0, "士绅": 0.05, "商人": 0.20, "农民": 0.30, "百工": 0.15, "百役": 0.10, "流民": 0.0}
# 省税收系数（依据 Prompt/1627年明朝各省数据填充.docx 表2 税负特点）：
# 边镇重赋（陕/晋）与军事后勤（豫/北直隶）调高；江南折银低（南直隶/浙江）调低
PROV_TAX_MULT = {
    "shaanxi": 1.2, "shanxi": 1.2, "henan": 1.1, "beizhili": 1.1,
    "nanzhili": 0.8, "zhejiang": 0.8,
}

# 各省：总人口(万) + 各阶级人口(万) [宗室,士绅,商人,农民,百工,百役,流民]
# 总人口依据 docx 表1（辽东无数据，保持原值）；各阶级按原比例缩放，
# 流民比例按灾情调高（陕西饥荒最重、山西/河南次之）
POP = {
    "beizhili": (800,  [3, 20, 30, 600, 80, 40, 27]),
    "shandong": (1400, [6, 35, 47, 1108, 105, 58, 41]),
    "shanxi":   (300,  [1, 6, 9, 232, 22, 15, 15]),
    "henan":    (1250, [10, 29, 42, 970, 90, 48, 61]),
    "shaanxi":  (1100, [5, 17, 17, 820, 70, 40, 131]),
    "liaodong": (100,  [0, 2, 3, 55, 8, 4, 28]),
    "nanzhili": (4600, [18, 115, 230, 3565, 414, 184, 74]),
    "sichuan":  (300,  [2, 8, 11, 236, 24, 13, 6]),
    "huguang":  (1200, [11, 30, 45, 937, 90, 45, 42]),
    "jiangxi":  (1400, [7, 35, 53, 1097, 128, 52, 28]),
    "zhejiang": (1500, [5, 35, 80, 1150, 140, 60, 30]),
    "fujian":   (800,  [4, 20, 53, 573, 80, 33, 37]),
    "guangdong":(1000, [6, 21, 71, 714, 100, 43, 45]),
    "guangxi":  (500,  [2, 10, 13, 400, 33, 17, 25]),
    "yunnan":   (400,  [3, 8, 10, 312, 29, 13, 25]),
    "guizhou":  (300,  [2, 6, 6, 230, 20, 10, 26]),
}

# 核心情况 / 基建 / 可承载人口(万)（承载为玩家给定）
BASE = {
    "beizhili": ("核心", 80, 800),  "shandong": ("核心", 70, 1050),
    "shanxi":   ("核心", 60, 500),  "henan":    ("核心", 65, 900),
    "shaanxi":  ("核心", 45, 380),  "liaodong": ("宣称", 35, 100),
    "nanzhili": ("核心", 85, 2700), "sichuan":  ("核心", 50, 1150),
    "huguang":  ("核心", 65, 1600), "jiangxi":  ("核心", 70, 900),
    "zhejiang": ("核心", 80, 1100), "fujian":   ("核心", 55, 500),
    "guangdong":("核心", 55, 900),  "guangxi":  ("宣称", 35, 360),
    "yunnan":   ("宣称", 30, 420),  "guizhou":  ("宣称", 25, 200),
}

# 腐败 0-100 + 核心原因简述（玩家修正后数值；东番 78 与广东相当，非核省 0）
CORRUPTION = {
    "beizhili": (85, "京师阉党、厂卫核心，卖官鬻爵严重，但中枢仍有形式监督。"),
    "nanzhili": (82, "江南重赋、织造、盐漕利益巨大，官商勾结，士绅舆论略能制衡。"),
    "shandong": (78, "漕运盐政、辽饷转运，胥吏盘剥，孔府特权。"),
    "shanxi":   (75, "宗藩、边镇军饷、盐引利益交织。"),
    "henan":    (80, "藩王占田、河工贪腐、赋役不均。"),
    "shaanxi":  (88, "大旱加派叠加，赈济侵吞，催科逼民，民变已起，吏治崩坏更明显。"),
    "sichuan":  (78, "奢安之乱军需供应，将领冒饷，地方借战事摊派。"),
    "huguang":  (75, "藩王占田、辽饷加派，胥吏敲诈。"),
    "zhejiang": (75, "织造太监、盐税重赋，但地方士绅制衡较强。"),
    "jiangxi":  (70, "漕粮赋税重，官场贪腐，宗族士绅制衡稍强。"),
    "fujian":   (68, "海贸走私、盐政收规，宗族强，官府控制弱。"),
    "guangdong":(72, "市舶、澳门贸易、盐税、珠池利益。"),
    "guangxi":  (75, "土司与流官勾结，边疆吏治差。"),
    "yunnan":   (78, "沐氏勋贵、土司、矿税太监，偏远监督弱。"),
    "guizhou":  (82, "奢安之乱主战场，军费侵吞、土司盘剥，基层残破。"),
    "liaodong": (90, "辽饷巨大，将领冒饷、镇守太监干预，边军缺饷逃亡。"),
    "dongfan":  (78, "海贸走私、盐政收规，官府控制弱。"),
}

# 镇军（人数 / 军饷需求 / 发饷比例）不在地方数据里：军队数据只有一份，
# 全在 world/armies.json（见 engine/armies.py；军饷需求 = 人数 × 常数，现算不存）。

# 流民赈济（两/月）
RELIEF = {
    "beizhili": 1500, "shandong": 3000, "shanxi": 3000, "henan": 5000,
    "shaanxi": 8000, "liaodong": 4000, "nanzhili": 2000, "sichuan": 1000,
    "huguang": 4000, "jiangxi": 1500, "zhejiang": 2000, "fujian": 2000,
    "guangdong": 2000, "guangxi": 1000, "yunnan": 1000, "guizhou": 1000,
}

# 地方工程：[(名称, 类型, 状态, 备注)]（玩家给定 1627 工程表；
# 未列出的省（河南/浙江/广西/广东）保留旧工程，状态留空）
PROJECTS = {
    "beizhili": [
        ("紫禁城三大殿重建", "宫殿", "刚竣工", "八月初二完工，耗银595.7万两"),
        ("德陵", "陵寝", "即将开工", "九月始建，明最后一座帝陵"),
        ("庆陵（泰昌帝陵）", "陵寝", "已建成", "天启六年六月完工"),
        ("肃宁城改建", "要塞", "已完工", "魏忠贤主导，土城改砖城"),
        ("居庸关长城", "要塞", "前朝遗留·维护", "万历年间修筑"),
        ("昌镇右车营长城", "要塞", "已完工", "天启五年九月竣工"),
        ("天津水田屯田", "水利", "运营中", "天启初年开田三千亩"),
    ],
    "nanzhili": [
        ("通济新河", "水利", "已建成", "天启五年完工，河长57里"),
        ("泇河运河", "水利", "已建成运行", "万历三十二年开通"),
        ("南旺分水枢纽", "水利", "前朝遗留·维护", "运河\"水脊\""),
    ],
    "shandong": [("莱芜铁矿", "矿场", "持续运营", "碑刻载\"炉火成片，犹城池焉\"")],
    "liaodong": [
        ("关宁锦防线", "要塞群", "基本成形", "历时五年，绵延400余里"),
        ("大凌河城修复", "要塞", "搁置/中止", "天启七年五月因后金回师中止"),
    ],
    "shanxi": [
        ("平型关砖石包砌", "要塞", "在建", "城墙458丈，天启七年分议修"),
        ("太原烈石口开渠", "水利", "进行中/刚完成", "天启七年立碑引水"),
    ],
    "shaanxi": [
        ("延绥镇边墙", "要塞", "严重失修", "边饷断供，无人维护"),
        ("嘉峪关", "要塞", "前朝遗留·维护", "洪武五年始建，甘肃镇辖"),
        ("悬臂长城", "要塞", "前朝遗留·维护", "嘉靖年间修筑，甘肃镇辖"),
        ("灵州黄河石堤", "水利", "已建成", "天启五年完工，堤长6000余丈，宁夏镇辖"),
        ("秦渠长堤/汉渠芦洞", "水利", "已建成", "张九德主持，宁夏镇辖"),
    ],
    "jiangxi": [
        ("广丰银山银矿", "矿场", "大规模开采", "天启年间，高峰万余人"),
        ("德兴/铅山铜矿", "矿场", "持续运营", "明初即有"),
    ],
    "yunnan": [
        ("银矿", "矿场", "持续运营", "占全国产量之半"),
        ("铜矿", "矿场", "持续运营", "天启六年开局铸钱刺激"),
        ("金沙江等金矿", "矿场", "持续运营", "永北、宝山、永宁"),
        ("铁矿/锡矿", "矿场", "持续运营", "大理、临安个旧等地"),
    ],
    "sichuan": [("自流井盐井群", "矿场", "大规模运营", "380眼盐井")],
    "fujian": [("尤溪银屏山银场", "矿场", "衰退中运营", "洪武十九年设42座炉冶，明后期严重萎缩")],
    "huguang": [("辰州银矿", "矿场", "间歇性运营", "《天工开物》列为美矿，万历矿祸后规模缩减")],
    "guizhou": [("铜仁银矿/水银朱砂场", "矿场", "间歇性运营", "史载产银产朱砂，规模缩减")],
    # 未列入 1627 工程表的省：保留旧工程，状态留空
    "henan":    [("开封府城", "城池", "", "周藩所在，黄河南岸")],
    "zhejiang": [("海宁海塘", "工程", "", "捍海石塘")],
    "guangdong": [("广州府城", "港埠", "", "岭南会城通商")],
    "guangxi":  [("桂林府城", "城池", "", "靖江藩所在")],
}


def build_local(pid):
    core, infra, carrying = BASE[pid]
    total, pops = POP[pid]
    relief = RELIEF[pid]
    mult = PROV_TAX_MULT.get(pid, 1.0)
    corr, corr_note = CORRUPTION.get(pid, (0, ""))
    classes = {}
    for i, name in enumerate(["宗室", "士绅", "商人", "农民", "百工", "百役", "流民"]):
        pop = pops[i]
        if name == "宗室":
            output, tax_rate, support = 0.0, 0.0, round(pop * 1000)   # 宗禄
        elif name == "流民":
            output, tax_rate, support = 0.0, 0.0, relief
        else:
            output = round(pop * OUTPUT_RATE[name], 1)
            tax_rate = round(TAX_RATE[name] * mult, 4)
            support = 0
        classes[name] = {
            "pop": pop,
            "attitude": 65 if name != "流民" else 38,
            "output": output,
            "tax_rate": tax_rate,
            "support": support,
            # 税收为派生值（产出 × 税收比例），不入表
        }
    return {
        "core": core,
        "corruption": corr,
        "corruption_note": corr_note,
        "infra": infra,
        "army": {},
        "classes": classes,
        "projects": [{"name": n, "kind": k, "status": st, "note": nt}
                     for n, k, st, nt in PROJECTS[pid]],
        "carrying": carrying,
        "weather": None,   # 动态，由 engine/weather.py 初始化
    }


def main():
    with open(WORLD, encoding="utf-8") as f:
        data = json.load(f)
    for p in data["provinces"]:
        if p["id"] in BASE:
            p["local"] = build_local(p["id"])
        elif p["owner"] != "ming":
            # 敌国/藩属：简化数据（核心=非核）
            p["local"] = {
                "core": "非核", "corruption": 0, "corruption_note": "",
                "infra": 20,
                "army": {},
                "classes": {}, "projects": [], "carrying": 0,
                "weather": None,
            }
    with open(WORLD, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 自检：阶级人口总和 = 源表总人口（地方人口为派生值，不入表）
    ok = True
    for p in data["provinces"]:
        loc = p.get("local") or {}
        cls = loc.get("classes") or {}
        if cls and p["id"] in POP:
            s = sum(c["pop"] for c in cls.values())
            if s != POP[p["id"]][0]:
                print("人口不符 %s: 阶级和=%d 源表总人口=%d" % (p["id"], s, POP[p["id"]][0]))
                ok = False
    print("地方数据已写入 world/provinces.json" + ("（自检通过）" if ok else "（自检有误！）"))


if __name__ == "__main__":
    main()
