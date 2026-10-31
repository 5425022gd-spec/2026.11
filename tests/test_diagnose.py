"""诊断模块测试（mock 模式，不发起网络）。

验证：
1. mock 模式下 diag 跳过网络检查、给出配置摘要；
2. config/llm.json 加载：默认文件存在、api_key 为空 → mock；
3. 环境变量覆盖（LLM_MOCK 强制 mock 即使配置了 key 场景由构造逻辑保证）。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import os as _o, tempfile as _t
_o.environ.setdefault("OFFICIALS_DB_PATH", _o.path.join(_t.gettempdir(), "dsh_test_diagnose.db"))
_o.environ.setdefault("POSITIONS_PATH", _o.path.join(_t.gettempdir(), "dsh_test_diagnose_pos.json"))
from engine.llm import LLMClient, load_config, DEFAULT_CONFIG_PATH
from engine.diagnose import run_diagnose


def main():
    # 0) 配置加载
    cfg = load_config()
    print("config 文件:", DEFAULT_CONFIG_PATH)
    print("base_url:", cfg.get("base_url"), "| model:", cfg.get("model"),
          "| api_key 为空:", not cfg.get("api_key"))
    assert cfg.get("base_url"), "config 应含默认 base_url"

    # 1) mock 模式诊断（无 key 时 LLMClient 自动 mock）
    os.environ["LLM_MOCK"] = "1"
    llm = LLMClient()
    lines = run_diagnose(llm)
    print("\n== diag（mock）==")
    for l in lines:
        print(l)
    assert llm.mock
    assert any("模式=mock" in l for l in lines)
    assert any("跳过 mock 模式" in l for l in lines)
    assert not any("POST /chat" in l for l in lines), "mock 模式不应发起网络请求"

    # 2) 优先级：文件已有字段 > 环境变量；文件缺字段 → 回退环境变量
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump({"model": "file-model"}, f)
        tmp = f.name
    os.environ["LLM_BASE_URL"] = "https://example.com/v1"
    os.environ["LLM_MODEL"] = "env-model"
    llm2 = LLMClient(config_path=tmp)
    print("\n文件有 model → 用文件值:", llm2.model, "（文件优先于环境变量）")
    assert llm2.model == "file-model"
    print("文件缺 base_url → 回退环境变量:", llm2.base_url)
    assert llm2.base_url == "https://example.com/v1"
    os.unlink(tmp)

    print("\nDIAGNOSE OK")


def test_main():
    main()


if __name__ == "__main__":
    main()
