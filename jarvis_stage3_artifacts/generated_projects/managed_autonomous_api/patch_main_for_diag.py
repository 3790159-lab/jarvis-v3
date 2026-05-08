from pathlib import Path

main_path = Path("app/main.py")
if not main_path.exists():
    raise SystemExit("app/main.py not found")

text = main_path.read_text(encoding="utf-8")

import_line = "from app.diag_router import router as diag_router"
include_line = "app.include_router(diag_router)"

changed = False

if import_line not in text:
    text = import_line + "\n" + text
    changed = True

if include_line not in text:
    text += "\n\ntry:\n    app.include_router(diag_router)\nexcept Exception as exc:\n    print(f\"[diag_router] include skipped: {exc}\")\n"
    changed = True

if changed:
    main_path.write_text(text, encoding="utf-8")
    print("main.py patched successfully")
else:
    print("main.py already patched")
