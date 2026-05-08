from __future__ import annotations

from pathlib import Path

TARGET_DIRS = ["app", "tests", "."]
TARGET_FILES = {"mission_regression.py", "bootstrap_runtime.py", "repair_state.py", "ensure_agents.py"}


def fix_file(path: Path) -> bool:
    raw = path.read_bytes()
    changed = False

    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
        changed = True

    text = raw.decode("utf-8", errors="replace")
    cleaned = text.replace("\ufeff", "")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")

    if cleaned != text:
        changed = True

    if changed:
        path.write_text(cleaned, encoding="utf-8", newline="\n")

    return changed


def should_process(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    if path.parent.name in {"__pycache__", ".venv", "venv"}:
        return False
    return True


def main() -> int:
    changed_files = []

    for base in TARGET_DIRS:
        base_path = Path(base)
        if not base_path.exists():
            continue

        if base_path.is_file():
            if should_process(base_path):
                if fix_file(base_path):
                    changed_files.append(str(base_path))
            continue

        for path in base_path.rglob("*.py"):
            if should_process(path):
                if fix_file(path):
                    changed_files.append(str(path))

    for filename in TARGET_FILES:
        p = Path(filename)
        if p.exists() and should_process(p):
            if fix_file(p) and str(p) not in changed_files:
                changed_files.append(str(p))

    print("ENCODING_FIX_OK")
    if changed_files:
        print("Changed files:")
        for item in changed_files:
            print(f" - {item}")
    else:
        print("No encoding changes required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
