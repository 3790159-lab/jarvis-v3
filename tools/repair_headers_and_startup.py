from __future__ import annotations

from pathlib import Path
import shutil
import re
import ast
from typing import Optional

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_header_repair_" + __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

FILES = [
    PROJECT_ROOT / "app" / "main.py",
    PROJECT_ROOT / "app" / "telegram_bot.py",
]

BOOTSTRAP = "import app.core.bootstrap_utf8  # noqa: F401"
FUTURE = "from __future__ import annotations"

TEXT_UTILS_IMPORTS = {
    "from app.core.text_utils import clean_text, write_text_utf8",
    "from app.core.text_utils import write_text_utf8, clean_text",
}

def detect_docstring_block(text: str) -> tuple[Optional[str], str]:
    try:
        module = ast.parse(text)
    except SyntaxError:
        return None, text

    if not module.body:
        return None, text

    first = module.body[0]
    if not (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return None, text

    lines = text.splitlines()
    start = first.lineno - 1
    end = first.end_lineno
    doc = "\n".join(lines[start:end])
    remaining = "\n".join(lines[end:])
    return doc, remaining

def normalize_file(path: Path) -> None:
    if not path.exists():
        print(f"[SKIP] not found: {path}")
        return

    shutil.copy2(path, BACKUP_DIR / path.name)
    original = path.read_text(encoding="utf-8")

    # Сохраняем shebang/coding, если есть
    lines = original.splitlines()
    prefix = []
    idx = 0

    if idx < len(lines) and lines[idx].startswith("#!"):
        prefix.append(lines[idx])
        idx += 1

    if idx < len(lines) and re.match(r"^#.*coding[:=]\s*[-\w.]+", lines[idx]):
        prefix.append(lines[idx])
        idx += 1

    body = "\n".join(lines[idx:])

    docstring, rest = detect_docstring_block(body)
    if docstring is None:
        rest = body

    body_lines = rest.splitlines()

    future_found = False
    bootstrap_found = False
    text_utils_line = None
    cleaned = []

    for line in body_lines:
        stripped = line.strip()

        if stripped == FUTURE:
            future_found = True
            continue

        if stripped == BOOTSTRAP:
            bootstrap_found = True
            continue

        if stripped in TEXT_UTILS_IMPORTS:
            text_utils_line = stripped
            continue

        cleaned.append(line)

    new_lines = []
    new_lines.extend(prefix)

    if prefix:
        new_lines.append("")

    if docstring:
        new_lines.append(docstring)
        new_lines.append("")

    # Всегда ставим future первым допустимым импортом
    new_lines.append(FUTURE)
    new_lines.append(BOOTSTRAP)

    if path.name == "telegram_bot.py" and text_utils_line:
        new_lines.append(text_utils_line)

    new_lines.append("")

    # Убираем лишние пустые строки в начале оставшегося тела
    while cleaned and cleaned[0].strip() == "":
        cleaned.pop(0)

    new_lines.extend(cleaned)

    new_text = "\n".join(new_lines).rstrip() + "\n"
    path.write_text(new_text, encoding="utf-8", newline="\n")

    print(f"[OK] normalized: {path}")
    print(f"     future_found={future_found}, bootstrap_found={bootstrap_found}, text_utils_preserved={bool(text_utils_line)}")

def ensure_init(path: Path) -> None:
    if not path.exists():
        path.write_text("", encoding="utf-8", newline="\n")
        print(f"[OK] created: {path}")

def write_start_script_backend() -> None:
    content = r'''param(
    [int]$Port = 8015,
    [string]$Host = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot | Split-Path -Parent
Set-Location $ProjectRoot

chcp 65001 | Out-Null
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path ".\.venv\Scripts\python.exe") {
    $PythonExe = ".\.venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m py_compile ".\app\main.py"
if ($LASTEXITCODE -ne 0) {
    throw "Syntax check failed for app\main.py"
}

Write-Host "[INFO] Starting backend on $Host:$Port"
& $PythonExe -m uvicorn app.main:app --host $Host --port $Port
'''
    (PROJECT_ROOT / "scripts" / "start_backend_safe.ps1").write_text(content, encoding="utf-8", newline="\n")

def write_start_script_bot() -> None:
    content = r'''param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot | Split-Path -Parent
Set-Location $ProjectRoot

chcp 65001 | Out-Null
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path ".\.venv\Scripts\python.exe") {
    $PythonExe = ".\.venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

Write-Host "[INFO] Using Python: $PythonExe"
& $PythonExe -m py_compile ".\app\telegram_bot.py"
if ($LASTEXITCODE -ne 0) {
    throw "Syntax check failed for app\telegram_bot.py"
}

Write-Host "[INFO] Starting telegram bot"
& $PythonExe ".\app\telegram_bot.py"
'''
    (PROJECT_ROOT / "scripts" / "start_telegram_bot_safe.ps1").write_text(content, encoding="utf-8", newline="\n")

def main() -> None:
    ensure_init(PROJECT_ROOT / "app" / "__init__.py")
    ensure_init(PROJECT_ROOT / "app" / "core" / "__init__.py")

    for path in FILES:
        normalize_file(path)

    write_start_script_backend()
    write_start_script_bot()

    print("")
    print(f"[DONE] Backups saved to: {BACKUP_DIR}")
    print("[DONE] Created:")
    print("       scripts/start_backend_safe.ps1")
    print("       scripts/start_telegram_bot_safe.ps1")

if __name__ == "__main__":
    main()
