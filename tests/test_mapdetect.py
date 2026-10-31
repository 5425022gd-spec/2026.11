"""地图判定层测试：解码 determination.png -> 缓存 -> mmap 取色查表。"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_mapdetect.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_mapdetect_pos.json"))
from engine.mapdetect import MapDetector, find_color, W, H


def main():
    d = MapDetector()
    print("等待判定层解码（首次约 30-60 秒，之后有缓存秒开）...")
    t0 = time.time()
    while not d.ready and time.time() - t0 < 360:
        time.sleep(1)
    assert d.ready, "解码超时：%r" % d.error
    print("ready in %.0fs" % (time.time() - t0))

    # 越界/未命中
    assert d.pick(-1, 0)["name"] is None
    assert d.pick(0, H)["name"] is None
    assert d.pick(W, 0)["name"] is None

    # 已知色 -> 地块名（新地图色卡：png/Information.txt）
    for color, expect in [((150, 255, 255), "北直隶"),
                          ((150, 175, 255), "辽东"),
                          ((150, 245, 255), "南直隶"),
                          ((200, 245, 255), "盛京"),
                          ((200, 240, 255), "嫩科尔沁")]:
        pos = find_color(color)
        print("颜色", color, "首次出现:", pos)
        assert pos is not None, "图中找不到颜色 %s" % (color,)
        x, y = pos
        r = d.pick(x, y)
        print("  pick(%d,%d) = %s" % (x, y, r))
        assert r["name"] == expect

    print("\nMAPDETECT OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
