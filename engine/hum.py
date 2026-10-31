# -*- coding: utf-8 -*-
"""人物立绘表情库：png/hum/<姓名>_<表情>.png 就是该角色「已导入」的表情。

只有真图（png/jpg）算导入；.svg 是占位，不算。前端与提示词都以本模块为准。
每张图可以带一组「调整」参数（缩放与位移），基准框是 420×760 的 1:1 立绘框，
参数存在 data/hum_adjust.json，键是「姓名_表情」。
"""
import json
import os

HUM_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "png", "hum")
ADJUST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "hum_adjust.json")

# 六种表情：顺序即界面上的排列顺序
EXPRS = ["常", "喜", "怒", "惧", "惊", "思"]
EXPR_LABEL = {"常": "平静", "喜": "欢喜", "怒": "震怒", "惧": "惶恐", "惊": "惊愕", "思": "沉吟"}

# 立绘基准框（1:1 显示窗口）：辅助线十字落在 (210, 100)
FRAME_W = 420
FRAME_H = 760
GUIDE_X = 210
GUIDE_Y = 100


def _key(name: str, expr: str) -> str:
    return "%s_%s" % (name, expr)


def _path(name: str, expr: str, ext: str) -> str:
    return os.path.join(HUM_DIR, "%s_%s%s" % (name, expr, ext))


def imported(name: str):
    """该角色已经导入真图的表情（按 EXPRS 顺序）。"""
    if not name:
        return []
    out = []
    for e in EXPRS:
        if os.path.isfile(_path(name, e, ".png")) or os.path.isfile(_path(name, e, ".jpg")):
            out.append(e)
    return out


def has_any(name: str) -> bool:
    return bool(imported(name))


def save_png(name: str, expr: str, data: bytes) -> str:
    """把导入的表情写成 png/hum/<姓名>_<表情>.png。返回文件路径。"""
    if not name or not name.strip():
        raise ValueError("缺人物姓名")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("姓名里不能带路径字符")
    if expr not in EXPRS:
        raise ValueError("表情只能是 %s" % "、".join(EXPRS))
    os.makedirs(HUM_DIR, exist_ok=True)
    path = _path(name.strip(), expr, ".png")
    with open(path, "wb") as f:
        f.write(data)
    return path


# ═════════════════════════ 调整参数（缩放 / 位移）═════════════════════════
def load_adjust() -> dict:
    try:
        with open(ADJUST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_all(data: dict) -> None:
    os.makedirs(os.path.dirname(ADJUST_FILE), exist_ok=True)
    tmp = ADJUST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, ADJUST_FILE)


def get_adjust(name: str, expr: str) -> dict:
    """取一张立绘的调整参数：scale 倍率，dx/dy 是相对 420×760 框的百分比位移。"""
    d = load_adjust().get(_key(name, expr)) or {}
    return {"scale": float(d.get("scale", 1.0)),
            "dx": float(d.get("dx", 0.0)),
            "dy": float(d.get("dy", 0.0))}


def save_adjust(name: str, expr: str, scale: float, dx: float, dy: float) -> dict:
    if expr not in EXPRS:
        raise ValueError("表情只能是 %s" % "、".join(EXPRS))
    if not name or not name.strip():
        raise ValueError("缺人物姓名")
    scale = max(0.2, min(3.0, float(scale)))
    dx = max(-100.0, min(100.0, float(dx)))
    dy = max(-100.0, min(100.0, float(dy)))
    data = load_adjust()
    data[_key(name.strip(), expr)] = {"scale": round(scale, 4),
                                      "dx": round(dx, 2), "dy": round(dy, 2)}
    _save_all(data)
    return data[_key(name.strip(), expr)]


def all_adjust() -> dict:
    return load_adjust()


def frame_info() -> dict:
    """基准框与辅助线位置，前端不写死。"""
    return {"w": FRAME_W, "h": FRAME_H, "guide_x": GUIDE_X, "guide_y": GUIDE_Y}
