"""刑部专业模块：刑部修正 律令与刑名（《大明律》、《问刑条例》等）。

功能规范：
1. 刑部修正：
   - 《大明律》：
     - 描述：《大明律》作为“祖制”，其文字和条文在明末依然被奉为不可动摇的法典。然而，法律的尊严在于执行，而明末的司法机制已经无法按照律典设计的节奏运转。
     - 效果：全国所有地方腐败程度+5%，中央腐败程度+15%
   - 《问刑条例》：
     - 描述：明朝末年，律条僵化，条例繁多，针对流民、贪污、党争等新问题，刑部时常需要重新解释，甚至"发明"新条例。
     - 效果：全国所有地方腐败程度+10%，中央腐败程度+5%
"""

from typing import Any, Dict, List, Tuple
from .modifier_registry import initial_buffs

# ── 现行法律配置 ──
# 修正数据在 world/modifiers.json 唯一数据源。
# ⚠ 本列表会被超控指令在运行时增删，故仍以列表形式存于本模块；
#   初始值由 modifier_registry 的兼容投影给出，消费点改成按 target 读效果后即可去掉投影。
ACTIVE_LAWS = initial_buffs("刑部 · 现行法律")

def get_xingbu_overview(state=None) -> Dict[str, Any]:
    """刑部汇总视图数据：刑部修正（法规 + 显示位置为刑部的统一修正）。"""
    from .modifiers import law_rows, merge_modifier_rows, department_modifier_rows

    rows = list(law_rows())
    if state is not None:
        rows += department_modifier_rows(state, "刑部")
    return {
        "law_rows": merge_modifier_rows(rows),
    }
