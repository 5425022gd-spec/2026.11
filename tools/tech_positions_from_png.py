# -*- coding: utf-8 -*-
"""从图里取科技坐标并对齐行列（唯一一处算坐标的地方）。

一条路走完：
1. **按树选图**：理论科学看 png/keji.png，科学技术看 png/keji2.png。
2. 认领：先按名字精确匹配 PSD 文字层；图上若把名字写成了别的字（车床/制硫酸/过磷酸钙之类），
   就取**最相近的那个层名**（相似度 ≥ 0.8）——只用于找位置，**不在数据里造第二个名字**。
   每个层名若有多个候选位置，优先取落在 PNG 标签框里的那一个。
3. 对齐行：y 相差不到 ROW_TOL 的算同一行，全行归到 y 的中位数；x 相差不到 COL_TOL 的算同一列，
   全列归到 x 的中位数。
4. 同一行名牌挤到一起时，按名牌宽度推开（只挪必挪的）。
"""
import io
import json
import os
import subprocess
import sys
from difflib import SequenceMatcher

sys.stdout.reconfigure(encoding="utf-8")

ROW_TOL = 25
COL_TOL = 35
NAME_SIM = 0.80     # 图上写成别字时，层名相似度下限
BOX_PAD = 30        # 层位置与标签框中心的容差
GAP = 26
CHAR_W = 19.0
PAD_W = 26.0

IMAGE_OF_TREE = {"theory": "keji", "applied": "keji2"}


def half_width(name):
    return CHAR_W * len(str(name)) / 2.0 + PAD_W / 2.0


def image_labels(tag):
    """某张图的标签框与 PSD 文字层。"""
    img = "png/%s.png" % tag
    out = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File",
                          os.path.join("tools", "keji_nodes.ps1"), "-Image", img],
                         capture_output=True, text=True, encoding="utf-8").stdout
    boxes = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            try:
                boxes.append(tuple(float(p) for p in parts))
            except ValueError:
                pass
    psd = subprocess.run([sys.executable, os.path.join("tools", "keji_texts.py"), tag],
                         capture_output=True, text=True, encoding="utf-8").stdout
    layers = {}
    for line in psd.splitlines():
        if line.startswith("#"):
            continue
        seg = line.split()
        if len(seg) >= 3 and seg[1].lstrip("-").isdigit() and seg[2].lstrip("-").isdigit():
            layers.setdefault(seg[0], []).append((int(seg[1]), int(seg[2])))
    print("%s：标签框 %d、文字层名 %d" % (tag, len(boxes), len(layers)))
    return boxes, layers


def pick_position(cands, boxes):
    """层名有多个候选位置时，优先取落在标签框里的那个。"""
    best, score = None, 1e9
    for (x, y) in cands:
        d = min((((x - b[0]) ** 2 + (y - b[1]) ** 2) ** 0.5 for b in boxes), default=1e9)
        if d < score:
            best, score = (x, y), d
    return best, score


def match_layer(name, layers):
    """精确层名优先；没有就取最相近的层名（只用于找位置）。"""
    if name in layers:
        return name, 1.0
    best, sim = None, 0.0
    for cand in layers:
        s = SequenceMatcher(None, name, cand).ratio()
        if s > sim:
            best, sim = cand, s
    return (best, sim) if sim >= NAME_SIM else (None, sim)


def main():
    data = json.load(io.open("world/techs.json", encoding="utf-8"))
    techs = data["techs"]
    byid = {t["id"]: t for t in techs}
    cache = {}

    for tree, tag in IMAGE_OF_TREE.items():
        group = [t for t in techs if t["tree"] == tree]
        if not group:
            continue
        if tag not in cache:
            cache[tag] = image_labels(tag)
        boxes, layers = cache[tag]
        unmatched = []
        for t in group:
            lname, sim = match_layer(t["name"], layers)
            if not lname:
                unmatched.append(t)
                continue
            pos, d = pick_position(layers[lname], boxes)
            t["x"], t["y"] = int(pos[0]), int(pos[1])
            if lname != t["name"]:
                print("  图上写作「%s」（相近度 %.2f）：%s" % (lname, sim, t["name"]))
        for t in unmatched:
            print("  图上找不到：%s" % t["name"])

        # 对齐行
        rows, cur = [], []
        for t in sorted(group, key=lambda x: x["y"]):
            if cur and t["y"] - cur[-1]["y"] <= ROW_TOL:
                cur.append(t)
            else:
                if cur:
                    rows.append(cur)
                cur = [t]
        if cur:
            rows.append(cur)
        for r in rows:
            ys = sorted(t["y"] for t in r)
            ry = int(round(ys[len(ys) // 2]))
            for t in r:
                t["y"] = ry
        # 对齐列
        cols, cur = [], []
        for t in sorted(group, key=lambda x: x["x"]):
            if cur and t["x"] - cur[-1]["x"] <= COL_TOL:
                cur.append(t)
            else:
                if cur:
                    cols.append(cur)
                cur = [t]
        if cur:
            cols.append(cur)
        for c in cols:
            xs = sorted(t["x"] for t in c)
            cx = int(round(xs[len(xs) // 2]))
            for t in c:
                t["x"] = cx
        # 同行推开
        moved = 0
        for r in rows:
            r.sort(key=lambda t: t["x"])
            for i in range(1, len(r)):
                need = half_width(r[i - 1]["name"]) + half_width(r[i]["name"]) + GAP
                if r[i]["x"] - r[i - 1]["x"] < need:
                    r[i]["x"] = int(round(r[i - 1]["x"] + need))
                    moved += 1
        print("  %s：%d 条 → %d 行、%d 列，推开 %d 处" % (tree, len(group), len(rows), len(cols), moved))

    io.open("world/techs.json", "w", encoding="utf-8", newline="\r\n").write(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
