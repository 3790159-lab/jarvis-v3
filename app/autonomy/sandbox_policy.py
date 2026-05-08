from __future__ import annotations

import re
from pathlib import Path


SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\- ]+")


def slugify(value: str) -> str:
    value = (value or "").strip()
    value = SAFE_NAME_RE.sub("", value)
    value = re.sub(r"\s+", "-", value)
    value = value.strip("-_ ").lower()
    if not value:
        value = "artifact"
    return value[:80]


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def resolve_under(base_dir: Path, *parts: str) -> Path:
    candidate = base_dir.joinpath(*parts).resolve()
    base_resolved = base_dir.resolve()
    if base_resolved not in [candidate, *candidate.parents]:
        raise ValueError(f"Path escapes sandbox: {candidate}")
    return candidate


def assert_safe_name(name: str) -> None:
    if not name or len(name.strip()) < 3:
        raise ValueError("Artifact name is too short")
    if len(name) > 120:
        raise ValueError("Artifact name is too long")
