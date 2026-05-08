from __future__ import annotations

from pathlib import Path
import shutil
from datetime import datetime

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_full_repair_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

MAIN_FILE = PROJECT_ROOT / "app" / "main.py"
BOT_FILE = PROJECT_ROOT / "app" / "telegram_bot.py"

FUTURE = "from __future__ import annotations"
BOOTSTRAP = "import app.core.bootstrap_utf8  # noqa: F401"
TEXT_UTILS = "from app.core.text_utils import clean_text, write_text_utf8"

def backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)

def strip_bom_everywhere(text: str) -> str:
    return text.replace("\ufeff", "")

def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")

def clean_file(path: Path, ensure_text_utils: bool) -> None:
    if not path.exists():
        print(f"[SKIP] missing: {path}")
        return

    backup(path)

    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = strip_bom_everywhere(raw)
    raw = normalize_newlines(raw)

    lines = raw.split("\n")

    shebang = None
    encoding_line = None
    kept = []

    i = 0
    if i < len(lines) and lines[i].startswith("#!"):
        shebang = lines[i]
        i += 1

    if i < len(lines) and "coding" in lines[i]:
        encoding_line = lines[i]
        i += 1

    body = lines[i:]

    had_text_utils = False

    filtered = []
    for line in body:
        stripped = line.strip()

        if stripped == FUTURE:
            continue
        if stripped == BOOTSTRAP:
            continue
        if stripped == TEXT_UTILS:
            had_text_utils = True
            continue

        filtered.append(line)

    while filtered and filtered[0].strip() == "":
        filtered.pop(0)

    new_lines = []

    if shebang is not None:
        new_lines.append(shebang)
    if encoding_line is not None:
        new_lines.append(encoding_line)

    if new_lines:
        new_lines.append("")

    new_lines.append(FUTURE)
    new_lines.append(BOOTSTRAP)

    if ensure_text_utils or had_text_utils:
        new_lines.append(TEXT_UTILS)

    new_lines.append("")

    new_lines.extend(filtered)

    new_text = "\n".join(new_lines).rstrip() + "\n"
    new_text = strip_bom_everywhere(new_text)

    path.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"[OK] repaired: {path}")

def ensure_init(path: Path) -> None:
    if not path.exists():
        path.write_text("", encoding="utf-8", newline="\n")
        print(f"[OK] created: {path}")

def write_backend_script() -> None:
    content = r'''param(
    [int]$Port = 8015,
    [string]$Host = "127.0.0.1"
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

Write-Host ("[INFO] Starting backend on {0}:{1}" -f $Host, $Port)
& $PythonExe -m uvicorn app.main:app --host $Host --port $Port
'''
    (PROJECT_ROOT / "scripts" / "start_backend_safe.ps1").write_text(content, encoding="utf-8", newline="\n")

def write_bot_script() -> None:
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

Write-Host "[INFO] Starting telegram bot"
& $PythonExe $BotFile
'''
    (PROJECT_ROOT / "scripts" / "start_telegram_bot_safe.ps1").write_text(content, encoding="utf-8", newline="\n")

def write_validate_script() -> None:
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
'''
    (PROJECT_ROOT / "scripts" / "validate_project_safe.ps1").write_text(content, encoding="utf-8", newline="\n")

def main() -> None:
    ensure_init(PROJECT_ROOT / "app" / "__init__.py")
    ensure_init(PROJECT_ROOT / "app" / "core" / "__init__.py")

    clean_file(MAIN_FILE, ensure_text_utils=False)
    clean_file(BOT_FILE, ensure_text_utils=True)

    write_backend_script()
    write_bot_script()
    write_validate_script()

    print("")
    print(f"[DONE] backups: {BACKUP_DIR}")
    print("[DONE] scripts created:")
    print("       scripts/start_backend_safe.ps1")
    print("       scripts/start_telegram_bot_safe.ps1")
    print("       scripts/validate_project_safe.ps1")

if __name__ == "__main__":
    main()
