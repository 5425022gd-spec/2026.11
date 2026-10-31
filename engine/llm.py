"""LLM 客户端 OpenAI 兼容+ mock 模式 —— 所有 AI 调用的唯一入口。

配置来源 优先级从高到低：
  1. config/llm.json   base_url / api_key / model / mock / reasoning_effort
  2. 环境变量           LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_MOCK
  3. 内置默认值

特性：
- chat_json / chat_text 两个出口；一律非流式 便于完整追踪
- reasoning_effort 思维链强度，如 high随配置注入；服务不支持时自动降级重试
- 响应中的思考流 reasoning_content与正文一并写入集中控制台日志
- 未配置 api_key 自动 mock 确定性，无网络可测
"""

import json
import os
import urllib.request
from typing import Any, Callable, Dict, Optional

from .logger import CONSOLE

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "llm.json",
)

SYSTEM_AI_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "ai_systems.json",
)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, default)


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """读取 config/llm.json；文件不存在或损坏时返回空字典。"""
    path = path or DEFAULT_CONFIG_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_system_ai_configs() -> Dict[str, Any]:
    """读取 config/ai_systems.json 各系统独立 AI 设置。"""
    try:
        with open(SYSTEM_AI_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_system_ai_configs(cfg_dict: Dict[str, Any]) -> bool:
    """保存各系统独立 AI 设置到 config/ai_systems.json。"""
    try:
        os.makedirs(os.path.dirname(SYSTEM_AI_CONFIG_PATH), exist_ok=True)
        with open(SYSTEM_AI_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg_dict, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_llm_for_system(system_name: str, default_client: Optional['LLMClient'] = None) -> 'LLMClient':
    """根据系统名称获取其专属配置的 LLM 客户端实例 若未配置则回退到全局默认。"""
    all_cfg = load_system_ai_configs()
    sys_cfg = all_cfg.get(system_name) or {}
    global_cfg = load_config()

    base_url = sys_cfg.get("base_url") or global_cfg.get("base_url") or _env("LLM_BASE_URL") or "https://api.deepseek.com/v1"
    api_key = sys_cfg.get("api_key") or global_cfg.get("api_key") or _env("LLM_API_KEY") or ""
    model = sys_cfg.get("model") or global_cfg.get("model") or _env("LLM_MODEL") or "deepseek-chat"
    reasoning_effort = sys_cfg.get("reasoning_effort") or global_cfg.get("reasoning_effort") or _env("LLM_REASONING") or None
    timeout = sys_cfg.get("timeout_seconds") or global_cfg.get("timeout_seconds") or 60

    forced_mock = _env("LLM_MOCK") == "1" or sys_cfg.get("mock") is True or (sys_cfg.get("mock") is None and global_cfg.get("mock") is True)

    client = LLMClient()
    client.base_url = base_url
    client.api_key = api_key
    client.model = model
    client.reasoning_effort = reasoning_effort
    client.timeout = int(timeout)
    client.mock = forced_mock or not api_key
    return client


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, mock_handler: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
                 config_path: Optional[str] = None):
        cfg = load_config(config_path)
        self.base_url = cfg.get("base_url") or _env("LLM_BASE_URL") or "https://api.deepseek.com/v1"
        self.model = cfg.get("model") or _env("LLM_MODEL") or "deepseek-chat"
        self.api_key = cfg.get("api_key") or _env("LLM_API_KEY") or ""
        self.reasoning_effort = cfg.get("reasoning_effort") or _env("LLM_REASONING") or None
        self.timeout = int(cfg.get("timeout_seconds") or _env("LLM_TIMEOUT") or 60)
        # 输出上限：工具调用的参数也算输出，剧本那种一次交十几条事件的调用很容易顶到默认上限
        # 而被截断 截断后参数不是合法 JSON。这里默认放大到 8192，可在配置里逐系统覆盖。
        self.max_tokens = int(cfg.get("max_tokens") or _env("LLM_MAX_TOKENS") or 200000)
        # 单次请求的输入上限 0/缺省＝不设，由模型自己的上下文决定
        self.max_input_tokens = int(cfg.get("max_input_tokens")
                                    or _env("LLM_MAX_INPUT_TOKENS") or 0)
        forced_mock = _env("LLM_MOCK") == "1" or cfg.get("mock") is True
        self.mock = forced_mock or not self.api_key
        self._mock_handler = mock_handler
        # 廷议发言是否走"可先查数据再发言"的工作流：默认否＝一次调用直接交卷
        self.council_workflow = bool(cfg.get("council_workflow", False))

    # ── 出口一：结构化 JSON 对象 ──
    def chat_json(self, system: str, user: str,
                  schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """一次对话，返回 JSON 对象。"""
        text = self.chat_text(system, user, json_mode=True)
        return _parse_json(text)

    # ── 出口一之二：单轮对话 自己掌管消息与工具包时用──
    def chat_once(self, messages: list, tools: Optional[list] = None,
                  think: bool = False, json_mode: bool = False):
        """发一次请求，返回 (content, think_text, tool_calls)。

        给「自己控制多轮与工具包递进」的工作流用 见 engine/script_workflow.py：
        调用方负责把 assistant 消息与 tool 结果追加回 messages。
        mock 模式下不请求网络，返回 ("", "", [])，由调用方走自己的兜底。
        """
        if self.mock:
            if self._mock_handler:
                # mock 想模拟工具调用时返回 {"tool": "<工具名>", "args": {...}}；
                # 其余情况当普通文本返回，供走文本通道的调用方使用
                result = self._mock_handler(
                    "", " ".join(str(m.get("content") or "") for m in messages), {})
                if isinstance(result, dict) and result.get("tool"):
                    call = [{"id": "mock-call-1", "type": "function",
                             "function": {"name": str(result["tool"]),
                                          "arguments": json.dumps(result.get("args") or {},
                                                                  ensure_ascii=False)}}]
                    return "", "", call
                return json.dumps(result, ensure_ascii=False), "", []
            return "", "", []
        return self._request(messages, tools=tools, json_mode=json_mode, think=think)

    # ── 出口二：原始文本 JSONL / 自由文本用──
    def chat_text(self, system: str, user: str, json_mode: bool = False,
                  think: bool = True) -> str:
        """一次对话，返回模型原始文本 非流式。思考流记录进控制台。

        think=False 时本次调用不启用思考模式 群策等场景临时关闭。
        """
        if self.mock:
            if self._mock_handler:
                result = self._mock_handler(system, user, {})
                text = json.dumps(result, ensure_ascii=False)
            else:
                text = ""
            CONSOLE.log("llm-mock", "AI 请求 mock",
                        "SYSTEM:\n%s\n\nUSER:\n%s\n\n=> %s" % (system, user, text))
            return text
        content, _, _ = self._request([{"role": "system", "content": system},
                                       {"role": "user", "content": user}],
                                      json_mode=json_mode, think=think)
        return content

    # ── 出口三：带工具调用 thinking mode 支持，官方 Tool Calls 文档──
    def _request(self, messages, tools=None, json_mode=False, think=True):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,  # 思考模式下不生效 文档：不报错，保留兼容
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        if tools:
            payload["tools"] = tools
        if think and self.reasoning_effort:
            # DeepSeek 思考模式 官方文档：
            # 开关 {"thinking":{"type":"enabled"}} + 强度 reasoning_effort
            # 映射：low→low, medium→high, high→high, max→max 无 medium 档
            payload["reasoning_effort"] = self.reasoning_effort
            payload["thinking"] = {"type": "enabled"}
        elif not think:
            # 临时关闭思考：显式 disabled 服务不支持时走降级链去掉该字段
            payload["thinking"] = {"type": "disabled"}

        # 降级链：thinking+reasoning+json → 去 thinking → 去 reasoning → 去 json_object
        body_variants = []
        body_variants.append(dict(payload))
        if json_mode:
            body_variants[0] = {**body_variants[0], "response_format": {"type": "json_object"}}
        if "thinking" in payload:
            no_think = {k: v for k, v in payload.items() if k != "thinking"}
            body_variants.append(no_think)
            if json_mode:
                body_variants.append({**no_think, "response_format": {"type": "json_object"}})
        if self.reasoning_effort:
            no_reason = {k: v for k, v in payload.items()
                         if k not in ("reasoning_effort", "thinking")}
            body_variants.append(no_reason)
            if json_mode:
                body_variants.append({**no_reason, "response_format": {"type": "json_object"}})
        # 服务端不认这个输出上限就整条去掉重试 降级链的最后一环
        if self.max_tokens:
            body_variants.append({k: v for k, v in payload.items() if k != "max_tokens"})
        # 去重 相同 dict 保留一个
        seen = set()
        unique = []
        for b in body_variants:
            key = json.dumps(b, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(b)
        body_variants = unique

        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.api_key,
        }
        CONSOLE.log("llm", "POST %s/chat/completions" % self.base_url.rstrip("/"),
                    "HEADERS:\n%s\n\nBODY:\n%s" % (
                        "\n".join("%s: %s" % (k, _mask_key(v) if k == "Authorization" else v)
                                  for k, v in headers.items()),
                        json.dumps(body_variants[0], ensure_ascii=False)))

        last_err = None
        for attempt in range(len(body_variants)):
            body = body_variants[attempt]
            req = urllib.request.Request(
                self.base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                try:
                    record_usage(data.get("usage") or {})
                except Exception:
                    pass
                msg = data["choices"][0]["message"]
                think = msg.get("reasoning_content") or msg.get("thinking") or ""
                text = msg.get("content") or ""
                tool_calls = msg.get("tool_calls") or []
                if think:
                    CONSOLE.log("llm", "AI 思考流 %d bytes" % len(think), think)
                CONSOLE.log("llm", "AI 响应 %d bytes" % len(text), "RAW:\n%s" % text)
                return text, think, tool_calls
            except Exception as e:  # 网络/参数不支持：换降级载荷重试
                last_err = e
        CONSOLE.log("llm", "AI 调用失败", repr(last_err))
        raise LLMError("LLM 调用失败: %r" % last_err)


# ═══════════════════════════════════════════════════════════
# Token 统计：每次调用累加 usage，界面按四项显示
# ═══════════════════════════════════════════════════════════
TOKEN_STATS = {
    "calls": 0,
    "prompt_tokens": 0,
    "cache_hit": 0,       # 缓存读取
    "cache_miss": 0,      # 未缓存输入
    "completion": 0,      # 输出
}


def record_usage(usage: dict) -> None:
    """把一次响应的 usage 累加进统计。字段按 OpenAI/DeepSeek 口径，缺项按 0 计。"""
    if not isinstance(usage, dict):
        return
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or (hit + miss))
    TOKEN_STATS["calls"] += 1
    TOKEN_STATS["prompt_tokens"] += prompt
    TOKEN_STATS["cache_hit"] += hit
    TOKEN_STATS["cache_miss"] += miss
    TOKEN_STATS["completion"] += int(usage.get("completion_tokens") or 0)


def token_stats() -> dict:
    """给界面用：四项数值 + 命中率。"""
    hit, miss = TOKEN_STATS["cache_hit"], TOKEN_STATS["cache_miss"]
    base = hit + miss
    return {
        "calls": TOKEN_STATS["calls"],
        "cache_hit": hit,
        "cache_miss": miss,
        "completion": TOKEN_STATS["completion"],
        "hit_rate": round(hit / base, 4) if base else 0.0,
    }


def reset_token_stats() -> None:
    for k in TOKEN_STATS:
        TOKEN_STATS[k] = 0


def _mask_key(key: str) -> str:
    return key[:6] + "***" + key[-4:] if len(key) > 12 else "***"


def _parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def parse_jsonl(text: str) -> list:
    """解析 JSONL 文本 每行一个 JSON 对象，容忍围栏与空行。"""
    out = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("```") or line.startswith("json"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
