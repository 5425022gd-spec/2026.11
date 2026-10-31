"""邸报：把台账/事件/结算转成玩家可读文本。"""

from .time import month_name


def build_report(state, notes) -> str:
    lines = ["【邸报 %s】" % month_name(state.date)]
    for n in notes:
        lines.append("" + n)
    for j in state.journal:
        if j["month"] == state.month_key():
            lines.append("" + j["text"])
    if not notes and not any(j["month"] == state.month_key() for j in state.journal):
        lines.append ("天下无事，惟岁月静好。")
    return "\n".join(lines)
