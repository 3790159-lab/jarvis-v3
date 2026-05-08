from __future__ import annotations

from pathlib import Path
from datetime import datetime
import shutil

PROJECT_ROOT = Path.cwd()
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BACKUP_DIR = PROJECT_ROOT / ("backup_runtime_launchers_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

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
if ($LASTEXITCODE -ne 0) {
    throw "Syntax check failed for app\main.py"
}

Write-Host ("[INFO] Starting backend on {0}:{1}" -f $BindHost, $Port)
& $PythonExe -m uvicorn app.main:app --host $BindHost --port $Port
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_bot_script() -> None:
    path = SCRIPTS_DIR / "start_telegram_bot_safe.ps1"
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

$BotFile = Join-Path $ProjectRoot "app\telegram_bot.py"

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m py_compile $BotFile
if ($LASTEXITCODE -ne 0) {
    throw "Syntax check failed for app\telegram_bot.py"
}

Write-Host "[INFO] Starting telegram bot as module"
& $PythonExe -m app.telegram_bot
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def write_runtime_check_script() -> None:
    path = SCRIPTS_DIR / "validate_runtime_launchers.ps1"
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

& $PythonExe -c "import sys; print(sys.path[0]); import app; import app.main; import app.telegram_bot; print('IMPORT_OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Module import validation failed"
}

Write-Host "[OK] Runtime launcher validation passed."
'''
    path.write_text(content, encoding="utf-8", newline="\n")

def main() -> None:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    write_backend_script()
    write_bot_script()
    write_runtime_check_script()

    print(f"[DONE] backups: {BACKUP_DIR}")
    print("[DONE] updated:")
    print("       scripts/start_backend_safe.ps1")
    print("       scripts/start_telegram_bot_safe.ps1")
    print("       scripts/validate_runtime_launchers.ps1")

if __name__ == "__main__":
    main()
