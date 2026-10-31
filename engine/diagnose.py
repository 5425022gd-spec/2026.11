"""AI 连接诊断：配置 / HTTP 连通 / 聊天请求 / 工具请求 四项检查。

游戏内命令 `diag` 调用；启动时也会提示。mock 模式跳过一切网络检查。
"""

import json
import time
import urllib.request
from typing import Any, Dict, List

from .llm import LLMClient


def _mask_key(key: str) -> str:
    if not key:
        return "未设置"
    if len(key) <= 12:
        return "已设置"
    return key[:6] + "***" + key[-4:]


def _post_json(llm: LLMClient, payload: Dict[str, Any], timeout: int = 15):
    req = urllib.request.Request(
        llm.base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + llm.api_key},
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return resp.status, body, time.time() - t0


def run_diagnose(llm: LLMClient) -> List[str]:
    """返回诊断输出行 中文，可直接展示。"""
    lines = ["【AI 连接诊断】"]
    mode = "mock" if llm.mock else "live"
    lines.append("[配置] 模式=%s | base_url=%s | model=%s | api_key=%s"
                 % (mode, llm.base_url, llm.model, _mask_key(llm.api_key)))

    if llm.mock:
        lines.append ("[HTTP] 跳过 mock 模式，未发起网络请求")
        lines.append ("[聊天] 跳过 mock 模式")
        lines.append ("[工具] 跳过 mock 模式")
        lines.append ("结论：当前为 mock 模式 引擎自带确定性条陈。")
        lines.append ("      如需真实 AI：编辑 config/llm.json 填入 api_key 后重启。")
        return lines

    # HTTP 连通 + 普通聊天请求 合并：最小对话即验证连通与鉴权
    ping = {
        "model": llm.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }
    try:
        status, body, dt = _post_json(llm, ping)
        usage = body.get("usage", {})
        extra = "，tokens=%s" % usage.get("total_tokens") if usage else ""
        lines.append("[HTTP/聊天] POST /chat/completions → %d，耗时 %.0fms%s"
                     % (status, dt * 1000, extra))
    except Exception as e:
        lines.append ("[HTTP/聊天] 失败：%r" % e)
        lines.append ("结论：网络或鉴权异常。请检查 config/llm.json 的 base_url 与 api_key。")
        return lines

    # 工具请求 function calling 支持情况
    tool_req = {
        "model": llm.model,
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 8,
        "tools": [{
            "type": "function",
            "function": {
                "name": "echo",
                "description": "回显测试",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
    }
    try:
        status, _, dt = _post_json(llm, tool_req)
        lines.append("[工具] 带 tools 的对话 → %d，耗时 %.0fms 支持 function calling"
                     % (status, dt * 1000))
    except Exception as e:
        lines.append ("[工具] 失败：%r" % e)
        lines.append ("        服务可能不支持 tools，不影响普通条陈功能")

    lines.append ("结论：基础连接正常。")
    return lines
