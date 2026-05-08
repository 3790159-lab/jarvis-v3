from __future__ import annotations

from pathlib import Path
from typing import Any
import json

UTF8 = "utf-8"


def ensure_parent_dir(path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def try_fix_mojibake(text: str) -> str:
    if not text:
        return text

    suspicious = ("РЎ", "Р°", "С‚", "Рё", "Ñ", "Ð")
    if any(token in text for token in suspicious):
        for enc in ("latin1", "cp1251"):
            try:
                repaired = text.encode(enc, errors="ignore").decode("utf-8", errors="ignore")
                if repaired:
                    return repaired
            except Exception:
                pass
    return text


def clean_text(value: Any) -> str:
    return try_fix_mojibake(normalize_text(value))


def write_text_utf8(path: str | Path, content: str) -> Path:
    p = ensure_parent_dir(path)
    p.write_text(content, encoding=UTF8, newline="\n")
    return p


def append_text_utf8(path: str | Path, content: str) -> Path:
    p = ensure_parent_dir(path)
    with p.open("a", encoding=UTF8, newline="\n") as f:
        f.write(content)
    return p


def read_text_utf8(path: str | Path, default: str = "") -> str:
    p = Path(path)
    if not p.exists():
        return default
    return p.read_text(encoding=UTF8)


def write_json_utf8(path: str | Path, data: Any) -> Path:
    p = ensure_parent_dir(path)
    with p.open("w", encoding=UTF8, newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def read_json_utf8(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding=UTF8) as f:
        return json.load(f)
