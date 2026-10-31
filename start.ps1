# 天启七年 · 启动脚本（一键进网页）
#
# 用法：
#   双击 start.bat / 运行 ./start.ps1    -> 自动启动网页界面并打开浏览器
#   ./start.ps1 -Diag                    -> 只跑 AI 连接诊断
#   ./start.ps1 -NoDiag                  -> 跳过启动前自动连接校验
#   ./start.ps1 -NoBrowser               -> 不自动打开浏览器（仅启动服务器）
#
# 说明：live 模式（config\llm.json 已填 api_key）启动前自动先跑一遍连接校验；
# mock 模式不发网络请求，直接进游戏。

[CmdletBinding()]
param(
    [switch]$Diag,
    [switch]$NoDiag,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Find-Python {
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c) { return @{ exe = $c.Source; args = @() } }
    $c = Get-Command py -ErrorAction SilentlyContinue
    if ($c) { return @{ exe = $c.Source; args = @("-3") } }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host "[错误] 未找到 Python。请先安装 Python 3 并加入 PATH。" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "═══ 天启七年 · AI 驱动文字游戏 ═══" -ForegroundColor Green
Write-Host ""

# 读取 AI 配置
$cfg = @{}
if (Test-Path "config\llm.json") {
    try { $cfg = Get-Content "config\llm.json" -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
}
$forcedMock = ($env:LLM_MOCK -eq "1") -or ($cfg.mock -eq $true)
$mode = if ($forcedMock -or -not $cfg.api_key) { "mock" } else { "live" }

if ($mode -eq "live") {
    Write-Host ("AI 模式: 真实 API" + "  base_url=" + $cfg.base_url + "  model=" + $cfg.model) -ForegroundColor Cyan
} else {
    Write-Host "AI 模式: mock（未配置 api_key；编辑 config\llm.json 可接入真实 AI）" -ForegroundColor Yellow
}

# 诊断：-Diag 显式要求；live 模式启动前自动跑；-NoDiag 跳过
if ($Diag) {
    Write-Host ""
    Write-Host ">> 运行 AI 连接诊断 ..." -ForegroundColor Cyan
    & $py.exe @($py.args + "diag.py")
    exit $LASTEXITCODE
}
if (-not $NoDiag -and $mode -eq "live") {
    Write-Host ""
    Write-Host ">> 启动前校验 AI 连接（-NoDiag 可跳过）..." -ForegroundColor Cyan
    & $py.exe @($py.args + "diag.py")
}

# 启动网页服务器：前台运行，本窗口即集中控制台
# （AI 请求头/请求体/响应、引擎调用日志实时滚动显示；也写入 logs/console.log）
Write-Host ""
Write-Host ">> 启动网页界面 http://127.0.0.1:8010 ..." -ForegroundColor Cyan
if (-not $NoBrowser) {
    Start-Sleep -Seconds 2
    Start-Process "http://127.0.0.1:8010"
}
Write-Host "本窗口 = 集中控制台（AI 请求与引擎调用日志）。"
Write-Host "网页控制台：http://127.0.0.1:8010/console"
Write-Host "Ctrl+C 结束游戏。" -ForegroundColor DarkGray
& $py.exe @($py.args + "server.py")
