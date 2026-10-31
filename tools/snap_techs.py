# -*- coding: utf-8 -*-
"""科技树微调：只把链吸附成列，不重排（【v0.4.0】开发工具，改 world/techs.json 的 x）。

为什么不做"行=层数、统一行距"：实测图上排布有 104 个不同高度（各自就近安放），
硬压成 16 行后，跨片长边（>1500px）从 30 条涨到 54 条——重排只会把线拉长。
所以这里**不动 y**，只做两件小事：
1. **链吸附**：唯一前置、同片、x 相差 ≤ SNAP 的，把子节点 x 吸到父节点那一列（链变直）；
   两个前置的，x 在两者中点 ±SNAP 内的吸到中点（材料化学那种）。
2. **防撞**：吸附后若与同一行（y 相差 ≤ ROW_TOL）的别的节点撞格，就放弃这次吸附。

用法：python tools/snap_techs.py
"""
import io
import json
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

SNAP = 80          # 允许吸附的最大横向偏差
ROW_TOL = 20       # 同一行的 y 容差

PATH = "world/techs.json"
data = json.load(io.open(PATH, encoding="utf-8"))
techs = data["techs"]
byid = {t["id"]: t for t in techs}
byname = {t["name"]: t for t in techs}


def row_key(t):
    return round(t["y"] / ROW_TOL)


def occupied_except(name):
    out = defaultdict(set)
    for t in techs:
        if t["name"] != name:
            out[row_key(t)].add(t["x"])
    return out


snapped, skipped = [], []
for _ in range(4):                     # 多跑几遍，链能一路吸下来
    for t in techs:
        pres = [byid[p] for p in t["prereqs"]]
        if not pres:
            continue
        cand = None
        same_slice = [p for p in pres if p["line"] == t["line"]]
        if len(pres) == 1 and same_slice and abs(t["x"] - pres[0]["x"]) not in (0,) \
                and abs(t["x"] - pres[0]["x"]) <= SNAP:
            cand = pres[0]["x"]
        elif len(pres) >= 2:
            xs = sorted(p["x"] for p in pres)
            mid = (xs[0] + xs[-1]) / 2.0
            if abs(t["x"] - mid) <= SNAP:
                cand = int(mid)
        if cand is None or cand == t["x"]:
            continue
        if cand in occupied_except(t["name"])[row_key(t)]:
            skipped.append(t["name"])
            continue
        snapped.append((t["name"], t["x"], cand))
        t["x"] = cand

maxx = max(t["x"] for t in techs)
maxy = max(t["y"] for t in techs)
dup = len(techs) - len({(t["x"], t["y"]) for t in techs})
if dup:
    raise SystemExit("吸附后出现同格 %d 处" % dup)

io.open(PATH, "w", encoding="utf-8", newline="\r\n").write(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n")

pos = {t["name"]: (t["x"], t["y"]) for t in techs}
sl = {t["name"]: t["line"] for t in techs}
ins = [(abs(pos[t["name"]][0] - pos[byid[p]["name"]][0]), t["name"], byid[p]["name"])
       for t in techs for p in t["prereqs"] if sl[t["name"]] == sl[byid[p]["name"]]]
cr = [(abs(pos[t["name"]][0] - pos[byid[p]["name"]][0]), t["name"], byid[p]["name"])
      for t in techs for p in t["prereqs"] if sl[t["name"]] != sl[byid[p]["name"]]]
print("吸附 %d 处（放弃 %d 处会撞格）：%s"
      % (len(snapped), len(skipped), "、".join("%s %d→%d" % s for s in snapped[:8])))
print("片内边 %d 条：竖线 %d（%.0f%%）平均 %.0fpx 最长 %dpx >1500px %d"
      % (len(ins), sum(1 for d, _, _ in ins if d == 0),
         100.0 * sum(1 for d, _, _ in ins if d == 0) / max(1, len(ins)),
         sum(d for d, _, _ in ins) / max(1, len(ins)),
         max((d for d, _, _ in ins), default=0), sum(1 for d, _, _ in ins if d > 1500)))
print("跨片边 %d 条：平均 %.0fpx >1500px %d"
      % (len(cr), sum(d for d, _, _ in cr) / max(1, len(cr)),
         sum(1 for d, _, _ in cr if d > 1500)))
print("画布 x≤%d y≤%d（y 一律未动）" % (maxx, maxy))
print("病理学→临床药理学：%s"
      % "、".join("%s(x=%d y=%d)" % (n, byname[n]["x"], byname[n]["y"])
                 for n in ("病理学", "病理生理学", "药理学", "诊断学", "临床药理学")))
