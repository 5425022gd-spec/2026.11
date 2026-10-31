# -*- coding: utf-8 -*-
"""科技树图片 → 每个科技名在图上中心坐标（开发工具，不参与游戏运行）。

数据来源：png/keji.psd（理论科学树）与 png/keji2.psd（科学技术树）的文字图层 ——
每个文字层带自己的变换矩阵，取 tx,ty 就是该名字在画布上的位置（零点＝图片左上角，x 向右、y 向下）。
文字层里可能有已经被隐藏的重名层，同一个名字出现多次时本工具会标出来，需要对着图片确认用哪一个。

用法：
    python tools/keji_texts.py                # 理论科学树 png/keji.psd
    python tools/keji_texts.py keji2          # 科学技术树 png/keji2.psd
    python tools/keji_texts.py keji 高等数学    # 只看某几个名字
输出：每行「名字 x y」；x,y 为该名字文字左端与基线，科技节点用的中心点 ≈ (x + 11.5×字数, y - 11)。
"""
import collections
import json
import os
import struct
import sys

sys.stdout.reconfigure(encoding="utf-8")


def read_text(body: bytes) -> str:
    """TySh 体内的文字串：'TEXT' + 长度(4) + UTF-16BE。"""
    pos = 0
    while True:
        k = body.find(b"TEXT", pos)
        if k < 0:
            return ""
        pos = k + 4
        if pos + 4 > len(body):
            return ""
        ln = struct.unpack(">I", body[pos:pos + 4])[0]
        if 1 <= ln <= 60 and pos + 4 + ln * 2 <= len(body):
            try:
                s = body[pos + 4:pos + 4 + ln * 2].decode("utf-16-be").replace("\x00", "").strip()
            except Exception:
                continue
            if s and all("\u2e80" <= ch <= "\u9fff" or ch in "《》()（）" for ch in s):
                return s


def psd_texts(path: str):
    psd = open(path, "rb").read()
    out, pos = [], 0
    while True:
        pos = psd.find(b"8BIMTySh", pos)
        if pos < 0:
            break
        ln = struct.unpack(">I", psd[pos + 8:pos + 12])[0]
        body = psd[pos + 12:pos + 12 + ln]
        mx = struct.unpack(">6d", body[2:50])
        nm = read_text(body)
        if nm:
            cx = mx[4] + 11.5 * len(nm)
            cy = mx[5] - 11.0
            out.append({"name": nm, "x": round(cx), "y": round(cy)})
        pos += 12 + ln + (ln % 2)
    return out


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "keji"
    want = sys.argv[2:]
    path = os.path.join("png", base + ".psd")
    texts = psd_texts(path)
    texts.sort(key=lambda t: (t["y"], t["x"]))
    cnt = collections.Counter(t["name"] for t in texts)
    dups = {n for n, c in cnt.items() if c > 1}
    print("# %s 文字层 %d 条" % (path, len(texts)))
    for t in texts:
        if want and t["name"] not in want:
            continue
        flag = "  ← 图上有多份同名，需对着图片确认" if t["name"] in dups else ""
        print("%s %d %d%s" % (t["name"], t["x"], t["y"], flag))
    if dups:
        print("# 重名：%s" % "、".join(sorted(dups)))
    json.dump(texts, open(".keji_texts.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
