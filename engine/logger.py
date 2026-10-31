"""集中控制台：统一记录 AI 请求与引擎内部调用。

三处输出：
1. 内存环形缓冲 server 通过 /api/logs 提供给网页控制台
2. stdout start.bat 启动的终端窗口 = 集中控制台
3. 文件 logs/console.log 持久化

kind 约定：
- llm     真实 AI 请求 detail 含完整 headers/body/response
- llm-mock mock 模式请求 detail 含完整提示词与假响应
- cmd     玩家命令
- action  引擎动作执行 含公式计算结果
- event   事件剧本触发
- adv     内阁条陈
- mem     章奏生成 / 已阅 / 回折
- diag    连接诊断
- map     地图取色判定
"""

import os
import threading
import time

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(_BASE, "logs")
LOG_FILE = os.path.join(LOG_DIR, "console.log")
MAX_ENTRIES = 500


class Console:
    def __init__(self):
        self._entries = []
        self._lock = threading.Lock()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
        except OSError:
            pass

    def log(self, kind, title, detail=""):
        entry = {
            "ts": time.strftime("%m-%d %H:%M:%S"),
            "kind": kind,
            "title": title,
            "detail": detail,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > MAX_ENTRIES:
                self._entries = self._entries[-MAX_ENTRIES:]

        line = "[%s][%s] %s" % (entry["ts"], kind, title)
        if detail:
            line += "\n    " + detail.replace("\n", "\n    ")
        print(line, flush=True)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def recent(self, n=200):
        with self._lock:
            return list(self._entries[-n:])


CONSOLE = Console()
