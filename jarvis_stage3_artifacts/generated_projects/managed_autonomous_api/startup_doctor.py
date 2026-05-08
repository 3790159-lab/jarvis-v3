from __future__ import annotations

import json
import py_compile
from pathlib import Path

SCAN_DIRS = ["app", "tests"]
SCAN_FILES = ["mission_regression.py", "bootstrap_runtime.py", "repair_state.py", "ensure_agents.py"]

IGNORE_PARTS = {"__pycache__", ".venv", "venv"}


def iter_python_files():
    for dirname in SCAN_DIRS:
        base = Path(dirname)
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in IGNORE_PARTS for part in path.parts):
                continue
            yield path

    for filename in SCAN_FILES:
        path = Path(filename)
        if path.exists() and path.suffix == ".py":
            yield path


def main() -> int:
    checked = []
    errors = []

    seen = set()

    for path in iter_python_files():
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)

        try:
            py_compile.compile(str(path), doraise=True)
            checked.append(str(path))
        except Exception as exc:
            errors.append({
                "file": str(path),
                "error": str(exc),
            })

    result = {
        "status": "ok" if not errors else "error",
        "checked_count": len(checked),
        "errors": errors,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
