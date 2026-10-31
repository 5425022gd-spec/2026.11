"""地图地块判定模块。

determination.png 12000x13000，RGBA是判定层：每个像素的颜色对应一个地块。
首次启动在后台线程解码该 PNG 纯 Python 逐行滤波还原，约 30-40 秒，
把还原后的 RGBA 像素缓存为裸字节文件；之后启动直接 mmap 秒开。
取色 O(1)：mmap 偏移 -> RGB -> 查 Information.txt 颜色表。
"""

import mmap
import os
import struct
import threading
import zlib

W = 12000
H = 13000
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG_PATH = os.path.join(_BASE, "png", "determination.png")
CACHE_DIR = os.path.join(_BASE, "png", ".cache")
CACHE_PATH = os.path.join(CACHE_DIR, "det_rgba.bin")
CACHE_SIZE = W * H * 4


# 地块识别色 -> 地块名 硬编码。png/Information.txt 只是给人/AI 看的说明，运行时不解析
COLOR_MAP = {
    # 后金、蒙古与东北诸部 200 系
    (200, 255, 255): "索伦",
    (200, 250, 255): "海西",
    (200, 245, 255): "盛京",
    (200, 240, 255): "嫩科尔沁",
    (200, 235, 255): "兀良哈",
    (200, 230, 255): "察哈尔",
    (200, 225, 255): "土默特",
    (200, 220, 255): "鄂尔多斯",
    (200, 215, 255): "东海",
    (200, 210, 255): "苦兀",
    # 大明疆域 150 系
    (150, 255, 255): "北直隶",
    (150, 250, 255): "山东",
    (150, 245, 255): "南直隶",
    (150, 240, 255): "浙江",
    (150, 235, 255): "福建",
    (150, 230, 255): "广东",
    (150, 225, 255): "广西",
    (150, 220, 255): "云南",
    (150, 215, 255): "四川",
    (150, 210, 255): "贵州",
    (150, 205, 255): "湖广",
    (150, 200, 255): "江西",
    (150, 195, 255): "河南",
    (150, 190, 255): "山西",
    (150, 185, 255): "陕西",
    (150, 180, 255): "东番",
    (150, 175, 255): "辽东",
}


def _read_idat(path):
    data = open(path, "rb").read()
    pos = 8
    idat = b""
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, bd, ct, *_ = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"IDAT":
            idat += chunk
    assert bd == 8 and ct == 6, "expect RGBA8 png"
    return w, h, zlib.decompress(idat)


def _rebuild_to_file(w, h, raw, path):
    """逐行滤波还原并写 RGBA 裸字节缓存。"""
    bpp = 4
    stride = 1 + w * bpp
    prev = bytearray(w * bpp)
    with open(path, "wb") as f:
        for y in range(h):
            off = y * stride
            fb = raw[off]
            row = raw[off + 1:off + stride]
            out = bytearray(w * bpp)
            if fb == 0:
                out[:] = row
            elif fb == 1:  # Sub
                for i in range(bpp):
                    out[i] = row[i]
                for i in range(bpp, w * bpp):
                    out[i] = (row[i] + out[i - bpp]) & 255
            elif fb == 2:  # Up
                for i in range(w * bpp):
                    out[i] = (row[i] + prev[i]) & 255
            elif fb == 3:  # Average
                for i in range(bpp):
                    out[i] = (row[i] + (prev[i] >> 1)) & 255
                for i in range(bpp, w * bpp):
                    out[i] = (row[i] + ((out[i - bpp] + prev[i]) >> 1)) & 255
            else:  # Paeth
                for i in range(bpp):
                    out[i] = (row[i] + prev[i]) & 255
                for i in range(bpp, w * bpp):
                    a = out[i - bpp]; b = prev[i]; c = prev[i - bpp]
                    p = a + b - c
                    pa = p - a if p >= a else a - p
                    pb = p - b if p >= b else b - p
                    pc = p - c if p >= c else c - p
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    out[i] = (row[i] + pr) & 255
            prev = out
            f.write(out)


class MapDetector:
    """判定层：后台解码 + mmap 取色。线程安全 就绪后只读。"""

    def __init__(self):
        self._mm = None
        self._ready = threading.Event()
        self._error = None
        threading.Thread(target=self._init, daemon=True).start()

    @property
    def ready(self):
        return self._ready.is_set()

    @property
    def error(self):
        return self._error

    def _init(self):
        try:
            need_rebuild = (not os.path.exists(CACHE_PATH)
                            or os.path.getsize(CACHE_PATH) != CACHE_SIZE)
            if not need_rebuild:
                # 底图被替换时缓存必须重建，否则仍按旧像素判定 尺寸不变则大小检查发现不了
                try:
                    need_rebuild = os.path.getmtime(PNG_PATH) > os.path.getmtime(CACHE_PATH)
                except OSError:
                    need_rebuild = True
            if need_rebuild:
                os.makedirs(CACHE_DIR, exist_ok=True)
                w, h, raw = _read_idat(PNG_PATH)
                try:
                    _rebuild_to_file(w, h, raw, CACHE_PATH)
                finally:
                    del raw
            fd = os.open(CACHE_PATH, os.O_RDONLY)
            try:
                self._mm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)
            finally:
                os.close(fd)
            self._ready.set()
        except Exception as e:  # noqa: BLE001
            self._error = repr(e)

    def pick(self, x, y):
        """返回 {name, color}；未就绪返回 {busy: True}；空白/越界 name=None。"""
        if not self._ready.is_set():
            return {"busy": True}
        if x < 0 or y < 0 or x >= W or y >= H:
            return {"name": None}
        off = (y * W + x) * 4
        r, g, b = self._mm[off], self._mm[off + 1], self._mm[off + 2]
        name = COLOR_MAP.get((r, g, b))
        return {"name": name, "color": [r, g, b]}


def find_color(color):
    """扫描缓存找第一个匹配像素 测试用。bytes.find 为 C 速度，需验证 4 字节对齐。"""
    needle = bytes(color)
    base = 0
    with open(CACHE_PATH, "rb") as f:
        while True:
            chunk = f.read(64 * 1024 * 1024)
            if not chunk:
                break
            start = 0
            while True:
                i = chunk.find(needle, start)
                if i < 0:
                    break
                if (base + i) % 4 == 0:
                    p = base + i
                    return (p // 4 % W, p // 4 // W)
                start = i + 1
            base += len(chunk)
    return None
