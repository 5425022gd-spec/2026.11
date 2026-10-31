# AI 请求模板（请求头 / 请求体）

本文件提供完整可复制的 HTTP 请求头与请求体模板，用于在网关、代理或
调试工具（如 Postman / curl）中直接粘贴。引擎实际发出的请求与此一致
（协议：OpenAI 兼容，DeepSeek / 硅基流动 / 各类中转均支持）。

---

## 一、请求头（Headers）

```
Content-Type: application/json
Authorization: Bearer <你的_API_KEY>
Accept: application/json
```

> 说明：
> - `<你的_API_KEY>` 换成 `config/llm.json` 里填写的 api_key。
> - 部分网关要求额外头，如 `HTTP-Referer`、`X-Title`（OpenRouter 风格），
>   按你的服务商文档追加即可。

---

## 二、请求体（Body，普通聊天）

```json
{
  "model": "deepseek-chat",
  "messages": [
    {"role": "system", "content": "你是大明内阁首辅，代皇帝拟条陈。只能使用列出的动作与参数，不得发明动作、省份或机制。只输出 JSON，不要任何其他文字。"},
    {"role": "user", "content": "【天启七年 五月】国库 240.0万两\n陕西：民变64 粮3.2万石 兵1.5万 执行38 腐败61 威胁10\n可用动作（只能从中选）：\n- relieve_famine（开仓赈济）：参数 grain=province.grain\n- raise_militia（募勇）：参数 force=数\n输出 JSON：{\"proposals\": [{\"action\": \"动作id\", \"target\": \"省id\", \"params\": {\"key\": 数值}, \"rationale\": \"一句话理由\"}]}"}
  ],
  "temperature": 0.7,
  "response_format": {"type": "json_object"}
}
```

### 二、请求体（Body，带工具 / function calling）

```json
{
  "model": "deepseek-chat",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "temperature": 0.7,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "echo",
        "description": "回显测试",
        "parameters": {"type": "object", "properties": {}}
      }
    }
  ]
}
```

> 说明：游戏内 `diag` 命令会先发普通聊天请求校验 HTTP/鉴权，
> 再发带 `tools` 的请求校验 function calling 支持情况。

---

## 三、请求尾（响应示例）

```json
{
  "id": "chatcmpl-xxx",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "{\"proposals\": [{\"action\": \"relieve_famine\", \"target\": \"shaanxi\", \"params\": {\"grain\": 1.6}, \"rationale\": \"陕西民变64，请开仓赈济以安民心。\"}]}"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 512, "completion_tokens": 48, "total_tokens": 560}
}
```

> 引擎会从 `choices[0].message.content` 取出文本，剥掉 ```json 围栏后
> `json.loads` 解析，再交给引擎做白名单校验。

---

## 四、curl 一行版（快速自测）

```
curl -X POST https://api.deepseek.com/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer <你的_API_KEY>" ^
  -d "{\"model\":\"deepseek-chat\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}"
```
