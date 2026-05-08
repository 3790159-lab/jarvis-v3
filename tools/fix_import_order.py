from __future__ import annotations

from pathlib import Path
import shutil
import re

PROJECT_ROOT = Path.cwd()
BACKUP_DIR = PROJECT_ROOT / ("backup_import_fix")
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

FILES = [
    PROJECT_ROOT / "app" / "main.py",
    PROJECT_ROOT / "app" / "telegram_bot.py",
]

BOOTSTRAP = "import app.core.bootstrap_utf8  # noqa: F401"
FUTURE = "from __future__ import annotations"


def fix_file(path: Path) -> None:
    if not path.exists():
        print(f"[SKIP] not found: {path}")
        return

    shutil.copy2(path, BACKUP_DIR / path.name)

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # удалить все bootstrap-строки, чтобы потом вставить ровно одну в правильное место
    lines = [line for line in lines if line.strip() != BOOTSTRAP]

    shebang = None
    coding = None

    idx = 0
    if idx < len(lines) and lines[idx].startswith("#!"):
        shebang = lines[idx]
        idx += 1

    if idx < len(lines) and re.match(r"^#.*coding[:=]\s*[-\w.]+", lines[idx]):
        coding = lines[idx]
        idx += 1

    rest = lines[idx:]

    future_idx = None
    for i, line in enumerate(rest):
        if line.strip() == FUTURE:
            future_idx = i
            break

    new_lines = []
    if shebang is not None:
        new_lines.append(shebang)
    if coding is not None:
        new_lines.append(coding)

    if future_idx is not None:
        new_lines.extend(rest[:future_idx + 1])
        new_lines.append(BOOTSTRAP)
        new_lines.extend(rest[future_idx + 1:])
    else:
        new_lines.append(BOOTSTRAP)
        new_lines.extend(rest)

    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8", newline="\n")
    print(f"[OK] fixed: {path}")


for file_path in FILES:
    fix_file(file_path)

print("")
print(f"Backups saved in: {BACKUP_DIR}")
