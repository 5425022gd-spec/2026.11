"""事件树归档导出为 txt 唯一实现处。

导出到项目根目录的 default-user/ 下：
- 整条归档：一次剧本工作流的完整转录 提示词、工具往返、成稿剧本与展示事件；
- 单条展示事件：只导出那一条事件的正文与字段。
"""
import io
import os
import re
from typing import Any, Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(_BASE, "default-user")


def _safe_name(text: str) -> str:
    """文件名里不能出现的字符换掉 Windows 一套。"""
    out = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text or "")).strip(" .")
    return out or "未命名"


def _lines_for_session(session: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    out.append("事件树归档 %s" % (session.get("title") or ""))
    out.append("记录时间：%s" % (session.get("created") or session.get("date") or ""))
    out.append("要紧事物：%s" % (session.get("mainline_id") or ""))
    if session.get("note"):
        out.append ("备注：%s" % session["note"])
    out.append("")
    script = session.get("script") or {}
    if script:
        out.append("=" * 40)
        out.append("剧本《%s》" % (script.get("name") or ""))
        out.append("=" * 40)
        out.append("剧情介绍：%s" % (script.get("introduction") or ""))
        out.append("")
        out.append ("剧情大纲：")
        out.append(script.get("outline") or "")
        out.append("")
        events = script.get("events") or []
        out.append("展示事件共 %d 条：" % len(events))
        for i, ev in enumerate(events, 1):
            out.append("")
            out.append ("--- 第 %d 条 ---" % i)
            out.extend(_lines_for_event(ev))
        out.append("")
    out.append("=" * 40)
    out.append ("工作流转录")
    out.append("=" * 40)
    for step in (session.get("log") or []):
        kind = step.get("kind")
        if kind == "prompt":
            out.append("[输入提示词] %s" % (step.get("file") or ""))
            out.append(str(step.get("text") or ""))
        elif kind == "tools":
            out.append("[工具包] %s" % "、".join(step.get("names") or []))
        elif kind == "tool":
            mark = " 失败" if step.get("ok") is False else ""
            out.append("[工具调用%s] %s 参数 %s" % (mark, step.get("name") or "",
                                                   step.get("args") or {}))
            if step.get("error"):
                out.append ("错误：%s" % step["error"])
            out.append ("返回值：")
            out.append(str(step.get("result") or ""))
        elif kind == "assistant":
            out.append("[模型] %s" % (step.get("text") or ""))
        elif kind == "finish":
            out.append ("[交卷] 剧本已交回")
        elif kind == "mock":
            out.append("[mock] %s" % (step.get("text") or ""))
        else:
            out.append("[%s] %s" % (kind, step))
        out.append("")
    return out


def _lines_for_event(ev: Dict[str, Any]) -> List[str]:
    rows = [
        ("事件编号", ev.get("event_id")),
        ("触发", ev.get("trigger")),
        ("展示渠道", ev.get("display_type")),
        ("标题", ev.get("title")),
        ("公文类别", ev.get("category_label")),
        ("内容分类", ev.get("content_label")),
        ("地点", ev.get("location")),
        ("正文", ev.get("full_text")),
        ("内阁票拟", ev.get("draft")),
        ("效果说明", ev.get("reward")),
        ("超控影响", ev.get("impact")),
    ]
    return ["%s：%s" % (k, v) for k, v in rows if v not in (None, "")]


def export_session_txt(session: Dict[str, Any], event_index: Optional[int] = None) -> str:
    """写一个 txt 到 default-user/，返回写出的路径。

    event_index 给了就只导出那一条展示事件；越界或该归档没有剧本就报错，不写空文件。
    """
    os.makedirs(EXPORT_DIR, exist_ok=True)
    title = _safe_name(session.get("title") or "事件树归档")
    stamp = _safe_name(session.get("created") or session.get("date") or "")
    if event_index is None:
        lines = _lines_for_session(session)
        name = "事件树_%s_%s.txt" % (title, stamp) if stamp else "事件树_%s.txt" % title
    else:
        script = session.get("script") or {}
        events = script.get("events") or []
        if not events:
            raise KeyError ("这条归档没有剧本，取不到展示事件")
        if event_index < 0 or event_index >= len(events):
            raise IndexError("展示事件序号越界：%d，共 %d 条" % (event_index, len(events)))
        ev = events[event_index]
        lines = ["剧本《%s》展示事件 第 %d 条" % (script.get("name") or "", event_index + 1), ""]
        lines.extend(_lines_for_event(ev))
        name = "事件树_%s_事件%d.txt" % (title, event_index + 1)
    path_out = os.path.join(EXPORT_DIR, name)
    io.open(path_out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return path_out
