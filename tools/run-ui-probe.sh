#!/usr/bin/env bash
# 无头浏览器 UI 抓取：启动 Chrome（CDP）→ 跑探针 → 收工
#   用法: bash tools/run-ui-probe.sh [tools/ui-probe.mjs]
#   环境: APP_URL  OUT_DIR  CDP_PORT
# 前置: 先跑起服务器  LLM_MOCK=1 python server.py
set -u
cd "$(dirname "$0")/.." || exit 1

PROBE="${1:-tools/ui-probe.mjs}"
mkdir -p png/ui-audit
mkdir -p /tmp/uiaudit
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
PROF=/tmp/uiaudit/prof-$$

"$CHROME" \
  --headless=new --disable-gpu --hide-scrollbars \
  --window-size=1600,950 \
  --remote-debugging-port="${CDP_PORT:-9222}" \
  --remote-allow-origins=* \
  --user-data-dir="$PROF" \
  --disable-crash-reporter --disable-breakpad --no-first-run \
  --no-default-browser-check --disable-extensions \
  about:blank > /tmp/uiaudit/chrome.log 2>&1 &
CPID=$!

for _ in $(seq 1 40); do
  if curl -s -o /dev/null "http://127.0.0.1:${CDP_PORT:-9222}/json/version"; then break; fi
  sleep 0.25
done

node "$PROBE"
RC=$?

kill "$CPID" 2>/dev/null
wait "$CPID" 2>/dev/null
rm -rf "$PROF" 2>/dev/null
echo "probe exit=$RC  ($PROBE)"
exit $RC
