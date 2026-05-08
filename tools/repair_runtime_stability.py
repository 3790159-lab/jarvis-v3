from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
TOOLS_DIR = PROJECT_ROOT / "tools"
BACKUP_DIR = PROJECT_ROOT / ("backup_runtime_stability_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

def write_dependency_installer() -> None:
    path = SCRIPTS_DIR / "install_runtime_dependencies.ps1"
    backup(path)
    content = r'''param()

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

if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip/setuptools/wheel" }

$Packages = @(
    "aiohttp",
    "fastapi",
    "uvicorn",
    "requests",
    "pyTelegramBotAPI",
    "python-dotenv",
    "pydantic",
    "pydantic-settings"
)

Write-Host "[INFO] Installing runtime packages..."
& $PythonExe -m pip install @Packages
if ($LASTEXITCODE -ne 0) { throw "Failed to install runtime packages" }

Write-Host "[OK] Runtime dependencies installed."
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_stop_bot_script() -> None:
    path = SCRIPTS_DIR / "stop_telegram_bot_safe.ps1"
    backup(path)
    content = r'''param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

$targets = @(
    "app.telegram_bot",
    "app\telegram_bot.py",
    "telegram_bot.py"
)

$killed = 0

Get-CimInstance Win32_Process | ForEach-Object {
    $cmd = $_.CommandLine
    if ([string]::IsNullOrWhiteSpace($cmd)) { return }

    foreach ($target in $targets) {
        if ($cmd -like "*$target*") {
            try {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
                Write-Host ("[OK] Stopped PID {0}: {1}" -f $_.ProcessId, $cmd)
                $script:killed++
                break
            } catch {
                Write-Host ("[WARN] Failed to stop PID {0}: {1}" -f $_.ProcessId, $_.Exception.Message)
            }
        }
    }
}

if ($killed -eq 0) {
    Write-Host "[INFO] No running telegram bot processes found."
} else {
    Write-Host ("[OK] Stopped {0} bot process(es)." -f $killed)
}
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_backend_script() -> None:
    path = SCRIPTS_DIR / "start_backend_safe.ps1"
    backup(path)
    content = r'''param(
    [int]$Port = 8015,
    [string]$BindHost = "127.0.0.1"
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

if (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

$MainFile = Join-Path $ProjectRoot "app\main.py"

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m py_compile $MainFile
if ($LASTEXITCODE -ne 0) { throw "Syntax check failed for app\main.py" }

& $PythonExe -c "import aiohttp; import fastapi; import uvicorn; print('IMPORTS_OK')"
if ($LASTEXITCODE -ne 0) { throw "Required backend packages are missing" }

Write-Host ("[INFO] Starting backend on {0}:{1}" -f $BindHost, $Port)
& $PythonExe -m uvicorn app.main:app --host $BindHost --port $Port
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_bot_script() -> None:
    path = SCRIPTS_DIR / "start_telegram_bot_safe.ps1"
    backup(path)
    content = r'''param(
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

$BotFile = Join-Path $ProjectRoot "app\telegram_bot.py"

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m py_compile $BotFile
if ($LASTEXITCODE -ne 0) { throw "Syntax check failed for app\telegram_bot.py" }

& $PythonExe -c "import requests; import telebot; import dotenv; print('BOT_IMPORTS_OK')"
if ($LASTEXITCODE -ne 0) { throw "Required bot packages are missing" }

Write-Host "[INFO] Starting telegram bot as module"
& $PythonExe -m app.telegram_bot
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_config_check_script() -> None:
    path = SCRIPTS_DIR / "check_runtime_config.ps1"
    backup(path)
    content = r'''param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[WARN] .env file not found."
    exit 0
}

Write-Host "=== .env key lines ==="
Get-Content $EnvFile -Encoding UTF8 | Where-Object {
    $_ -match "^(APP_PORT|APP_HOST|API_BASE_URL|TELEGRAM_|BACKEND_|SUPERVISOR_)="
}

Write-Host "----------------------------------------"
Write-Host "[INFO] Verify that bot API base URL points to the same backend port you start."
Write-Host "[INFO] Current backend launcher default port: 8015"
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_full_validation_script() -> None:
    path = SCRIPTS_DIR / "validate_runtime_full.ps1"
    backup(path)
    content = r'''param()

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
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def main() -> None:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    write_dependency_installer()
    write_stop_bot_script()
    write_backend_script()
    write_bot_script()
    write_config_check_script()
    write_full_validation_script()

    print(f"[DONE] backups: {BACKUP_DIR}")
    print("[DONE] updated:")
    print("       scripts/install_runtime_dependencies.ps1")
    print("       scripts/stop_telegram_bot_safe.ps1")
    print("       scripts/start_backend_safe.ps1")
    print("       scripts/start_telegram_bot_safe.ps1")
    print("       scripts/check_runtime_config.ps1")
    print("       scripts/validate_runtime_full.ps1")

if __name__ == "__main__":
    main()
