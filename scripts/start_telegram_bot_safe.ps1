param(
    [switch]$SkipStop
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

chcp 65001 | Out-Null
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $ProjectRoot

if (-not $SkipStop) {
    & (Join-Path $ProjectRoot "scripts\stop_telegram_bot_safe.ps1")
    Start-Sleep -Seconds 2
}

if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

$BotFile = Join-Path $ProjectRoot "tools\jarvis_smart_telegram_control.py"

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m py_compile $BotFile
if ($LASTEXITCODE -ne 0) { throw "Syntax check failed for tools\jarvis_smart_telegram_control.py" }

Write-Host "[INFO] Starting Jarvis Telegram bot"
& $PythonExe $BotFile
