from __future__ import annotations

import re
import shutil
from pathlib import Path

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / f"backup_fix_future_imports"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

FILES_TO_PATCH = [
    PROJECT_ROOT / "app" / "main.py",
    PROJECT_ROOT / "app" / "telegram_bot.py",
]

BOOTSTRAP_IMPORT = "import app.core.bootstrap_utf8  # noqa: F401"
FUTURE_IMPORT = "from __future__ import annotations"


def backup_file(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, BACKUP_DIR / path.name)


def patch_python_file(path: Path) -> None:
    if not path.exists():
        print(f"[SKIP] File not found: {path}")
        return

    backup_file(path)
    text = path.read_text(encoding="utf-8")

    # убрать все ранее вставленные bootstrap import
    lines = text.splitlines()
    cleaned_lines = [line for line in lines if line.strip() != BOOTSTRAP_IMPORT]

    # найти shebang / coding / future import
    shebang = []
    coding = []
    body = cleaned_lines[:]

    if body and body[0].startswith("#!"):
        shebang.append(body.pop(0))

    if body and re.match(r"^#.*coding[:=]\s*[-\w.]+", body[0]):
        coding.append(body.pop(0))

    future_idx = None
    for i, line in enumerate(body):
        if line.strip() == FUTURE_IMPORT:
            future_idx = i
            break

    if future_idx is not None:
        # вставляем bootstrap СРАЗУ после future import
        new_body = body[:future_idx + 1] + [BOOTSTRAP_IMPORT] + body[future_idx + 1:]
    else:
        # future import нет — вставляем bootstrap после shebang/coding
        new_body = [BOOTSTRAP_IMPORT] + body

    new_text = "\n".join(shebang + coding + new_body) + "\n"
    path.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"[OK] Patched: {path}")


def ensure_init_file(path: Path) -> None:
    if not path.exists():
        path.write_text("", encoding="utf-8", newline="\n")
        print(f"[OK] Created: {path}")
    else:
        print(f"[OK] Exists: {path}")


def rewrite_test_utf8_flow(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    content = """from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.core.bootstrap_utf8  # noqa: F401
from app.core.text_utils import write_text_utf8, read_text_utf8, clean_text

base = PROJECT_ROOT / "jarvis_stage3_artifacts" / "generated_projects" / "managed_autonomous_api" / "artifacts" / "output"
base.mkdir(parents=True, exist_ok=True)

source_text = "Создай файл artifacts/output/telegram_test.txt и запиши туда: Jarvis Telegram test successful."
fixed_text = clean_text(source_text)

log_file = base / "telegram_task_output.txt"
target_file = base / "telegram_test.txt"

write_text_utf8(log_file, f"Telegram task received: {fixed_text}\\n")
write_text_utf8(target_file, "Jarvis Telegram test successful.\\n")

print(read_text_utf8(log_file))
print(read_text_utf8(target_file))
"""
    if path.exists():
        backup_file(path)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"[OK] Rewritten: {path}")


def main() -> None:
    ensure_init_file(PROJECT_ROOT / "app" / "__init__.py")
    ensure_init_file(PROJECT_ROOT / "app" / "core" / "__init__.py")

    for file_path in FILES_TO_PATCH:
        patch_python_file(file_path)

    rewrite_test_utf8_flow(PROJECT_ROOT / "tools" / "test_utf8_flow.py")

    print("")
    print("PATCH COMPLETED")
    print(f"Backups: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
