"""AI 连接诊断（独立脚本）：python diag.py

不加载游戏世界，只校验 AI 配置与连接：
配置摘要 → HTTP/聊天请求 → 工具（function calling）请求。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.llm import LLMClient
from engine.diagnose import run_diagnose


def main():
    llm = LLMClient()
    for line in run_diagnose(llm):
        print(line)
    sys.exit(0 if llm.mock else (0 if any("基础连接正常" in l for l in run_diagnose(llm)) else 1))


if __name__ == "__main__":
    main()
