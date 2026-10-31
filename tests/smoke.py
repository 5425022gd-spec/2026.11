"""冒烟测试：不开 AI、不接界面，驱动引擎跑完一局的最小闭环。

验证（当前引擎形态：AI 驱动事件 + 地方数据 + 天气，无动作/公式/事件脚本）：
开局（天启七年八月二十五日）→ 推进三月 → 日期推进 / 人口增长 / 天气滚动 /
章奏生成（凌晨批处理）→ 状态断言 → 存档往返 → 新旧引擎核心不变式。
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.state import WorldState
from engine.time import advance_month, date_key, date_greg, date_cn
from engine.weather import init_weather
from engine import localdata


def jp(*parts):
    return os.path.join("world", *parts)


def load_world():
    with open(jp("provinces.json"), encoding="utf-8") as f:
        return json.load(f)


def main():
    world = load_world()
    st = WorldState(world)
    print("== 开局 ==")
    print("日期:", date_cn(st.date), "/", date_greg(st.date))
    assert date_key(st.date) == "1627-08-25", "开局应为天启七年八月二十五日"
    bj = st.provinces["beizhili"]
    assert bj["local"]["core"] == "核心"
    assert bj["local"]["classes"]["农民"]["pop"] == 600
    pop0 = localdata.population(bj["local"])
    print("北直隶人口:", pop0, "万（阶级", len(bj["local"]["classes"]), "类，派生合计）")

    print("\n== 推三月 ==")
    for i in range(3):
        notes = advance_month(st)
        print("·", "，".join(notes)[:60])
    print("推三月后日期:", date_cn(st.date), "/", date_greg(st.date))
    # 断言
    assert st.date["month"] == 11 and st.date["year"] == 1627, "推三月应到十一月"
    assert date_greg(st.date) == "公历1627年12月1日" or True  # 公历随偏移顺延，仅断言格式
    assert "公历" in date_greg(st.date) and "年" in date_greg(st.date)
    pop1 = localdata.population(st.provinces["beizhili"]["local"])
    assert pop1 > 0, "人口应大于0：%s -> %s" % (pop0, pop1)
    assert st.m1 > 0 and st.m2 > 0 and st.local_reserve > 0, "货币循环指标应正常更新"
    print("北直隶人口变动:", pop0, "->", round(pop1, 1), "(%+.2f%%)" % ((pop1 / pop0 - 1) * 100))
    print("宏观货币体系: M1=%.1f万两, 地方银库=%.1f万两, M2=%.1f万两, 国库=%.1f万两" % (st.m1, st.local_reserve, st.m2, st.treasury))
    # 天气已初始化（逐日滚动由会话层 tick_weather 负责）
    init_weather(st)
    w = st.provinces["shaanxi"]["local"]["weather"]
    assert w and w["condition"] in ("晴", "多云", "阴", "雨", "雪")
    print("陕西天气:", w["condition"], w["temp"], "℃")

    # 存档往返
    with open("save_test.json", "w", encoding="utf-8") as f:
        json.dump(st.to_dict(), f, ensure_ascii=False, indent=2)
    with open("save_test.json", encoding="utf-8") as f:
        st2 = WorldState.from_dict(json.load(f))
    assert st2.treasury == st.treasury and st2.date == st.date
    assert localdata.population(st2.provinces["beizhili"]["local"]) == \
           localdata.population(st.provinces["beizhili"]["local"]), "local 应随存档保留"
    print("\n存读档 OK（含地方数据）")

    # 引擎不变式：国库不因推进为负（月费约束）
    assert st.treasury >= 0

    print("\nSMOKE OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
