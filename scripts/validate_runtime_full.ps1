param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

& (Join-Path $ProjectRoot "scripts\check_runtime_config.ps1")
& (Join-Path $ProjectRoot "scripts\install_runtime_dependencies.ps1")

if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

$env:PYTHONPATH = $ProjectRoot

Write-Host "=== IMPORT VALIDATION ==="
& $PythonExe -c "import app; import aiohttp; import requests; import telebot; import dotenv; import app.main; print('BACKEND_IMPORT_OK')"
if ($LASTEXITCODE -ne 0) { throw "Backend import validation failed" }

& $PythonExe -c "import app; import app.telegram_bot; print('BOT_IMPORT_OK')"
if ($LASTEXITCODE -ne 0) { throw "Bot import validation failed" }

Write-Host "[OK] Full runtime validation passed."
