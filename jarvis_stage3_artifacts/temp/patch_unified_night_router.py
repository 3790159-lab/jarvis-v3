from __future__ import annotations

from pathlib import Path
from datetime import datetime

project_root = Path.cwd()
main_path = project_root / "app" / "main.py"

if not main_path.exists():
    raise SystemExit(f"main.py not found: {main_path}")

raw = main_path.read_text(encoding="utf-8")

import_line = "from app.routers.jarvis_unified_night_router import router as unified_night_router\n"
include_line = "app.include_router(unified_night_router)\n"
marker = "unified_night_router"

if marker in raw:
    print("Router already integrated.")
    raise SystemExit(0)

backup_path = main_path.with_suffix(".py.bak_unified_night_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
backup_path.write_text(raw, encoding="utf-8")

lines = raw.splitlines(keepends=True)

# Insert import after the last import/from line near the top.
insert_import_at = 0
for i, line in enumerate(lines[:120]):
    stripped = line.strip()
    if stripped.startswith("import ") or stripped.startswith("from "):
        insert_import_at = i + 1

lines.insert(insert_import_at, import_line)

raw2 = "".join(lines)

# Insert include_router after app = FastAPI(...) block, or after last existing include_router.
lines2 = raw2.splitlines(keepends=True)

insert_include_at = None
for i, line in enumerate(lines2):
    if "include_router(" in line:
        insert_include_at = i + 1

if insert_include_at is None:
    for i, line in enumerate(lines2):
        if "FastAPI(" in line and "app" in line:
            insert_include_at = i + 1
            break

if insert_include_at is None:
    raise SystemExit("Could not find FastAPI app or include_router location. Backup created, no patch applied.")

lines2.insert(insert_include_at, include_line)
patched = "".join(lines2)

main_path.write_text(patched, encoding="utf-8")
print(f"Patched app/main.py")
print(f"Backup: {backup_path}")