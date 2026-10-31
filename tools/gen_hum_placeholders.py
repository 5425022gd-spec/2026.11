# -*- coding: utf-8 -*-
"""按人物卡生成 png/hum 的占位立绘。

命名：<姓名>_<表情>.svg（真实美术补上来时，放同名 .png 就会优先用 png，见前端 humSrc）。
占位样式：白色空心边框 + 姓名_表情 + 表情符号，一眼能看出该槽位该画谁。
只生成还没有任何美术文件的槽位，不会覆盖你后补的图。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUM_DIR = os.path.join(ROOT, "png", "hum")
CARDS = os.path.join(ROOT, "data", "character_cards.json")

# 表情集：参考 emoji 的九种基本情绪
EXPRS = [
    ("常", "🙂", "平静"),
    ("喜", "😄", "欢喜"),
    ("怒", "😠", "震怒"),
    ("惧", "😨", "惶恐"),
    ("惊", "😲", "惊愕"),
    ("思", "🤔", "沉吟"),
]

TPL = """<svg xmlns="http://www.w3.org/2000/svg" width="420" height="760" viewBox="0 0 420 760">
  <rect x="3" y="3" width="414" height="754" fill="none" stroke="#ffffff" stroke-width="3"
        stroke-dasharray="10 7" opacity="0.85"/>
  <rect x="12" y="12" width="396" height="736" fill="none" stroke="#ffffff" stroke-width="1" opacity="0.35"/>
  <text x="210" y="300" text-anchor="middle" fill="#ffffff" font-size="34"
        font-family="Noto Serif SC, STSong, SimSun, serif" opacity="0.92">{name}</text>
  <text x="210" y="352" text-anchor="middle" fill="#ffffff" font-size="22"
        font-family="Noto Serif SC, STSong, SimSun, serif" opacity="0.66">{expr}　{desc}</text>
  <text x="210" y="430" text-anchor="middle" fill="#ffffff" font-size="72" opacity="0.85">{emoji}</text>
  <text x="210" y="720" text-anchor="middle" fill="#ffffff" font-size="15"
        font-family="Noto Serif SC, STSong, SimSun, serif" opacity="0.45">立绘占位 · 补同名 .png 即自动替换</text>
</svg>
"""


# 关键人物：值得单独配表情的名角（其余人一律共用通用表情）
KEY_CAST = ["魏忠贤", "黄立极", "崔呈秀", "王承恩", "袁崇焕", "毛文龙", "秦良玉", "李国𣚴"]
SHARED = "_通用"          # 共用表情的前缀：png/hum/_通用_喜.svg


def main() -> int:
    with io.open(CARDS, encoding="utf-8") as f:
        cards = json.load(f)
    names = [n for n in sorted(cards.keys()) if n in KEY_CAST]
    os.makedirs(HUM_DIR, exist_ok=True)
    made = skipped = 0
    for name in names:
        for expr, emoji, desc in EXPRS:
            stem = os.path.join(HUM_DIR, "%s_%s" % (name, expr))
            if os.path.exists(stem + ".png") or os.path.exists(stem + ".jpg"):
                skipped += 1          # 已经有真图，别覆盖
                continue
            with io.open(stem + ".svg", "w", encoding="utf-8", newline="") as f:
                f.write(TPL.format(name=name, expr=expr, desc=desc, emoji=emoji))
            made += 1
    # 共用表情：没单独配图的人一律落到这一套
    for expr, emoji, desc in EXPRS:
        stem = os.path.join(HUM_DIR, "%s_%s" % (SHARED, expr))
        if os.path.exists(stem + ".png") or os.path.exists(stem + ".jpg"):
            continue
        with io.open(stem + ".svg", "w", encoding="utf-8", newline="") as f:
            f.write(TPL.format(name="众臣", expr=expr, desc=desc, emoji=emoji))
        made += 1
    print("人物 %d 位 × 表情 %d 种：生成占位 %d，跳过已有真图 %d" % (len(names), len(EXPRS), made, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
