param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

chcp 65001 | Out-Null
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

$MainFile = Join-Path $ProjectRoot "app\main.py"
$BotFile = Join-Path $ProjectRoot "app\telegram_bot.py"

Write-Host "=== HEAD main.py ==="
Get-Content $MainFile -Encoding UTF8 -TotalCount 12
Write-Host "----------------------------------------"
Write-Host "=== HEAD telegram_bot.py ==="
Get-Content $BotFile -Encoding UTF8 -TotalCount 12
Write-Host "----------------------------------------"

Write-Host "=== PY_COMPILE ==="
& $PythonExe -m py_compile $MainFile
if ($LASTEXITCODE -ne 0) { throw "main.py compile failed" }
& $PythonExe -m py_compile $BotFile
if ($LASTEXITCODE -ne 0) { throw "telegram_bot.py compile failed" }

Write-Host "[OK] Validation passed."
