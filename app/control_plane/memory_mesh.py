from __future__ import annotations

import re
from pathlib import Path


def _slug(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9а-яіїєґ_]+", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "note"


class MemoryMeshVault:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.vault_dir = self.base_dir / "obsidian_memory_mesh"
        self.vault_dir.mkdir(parents=True, exist_ok=True)

    def _domain_dir(self, domain_name: str) -> Path:
        path = self.vault_dir / _slug(domain_name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _block_dir(self, domain_name: str, block_name: str) -> Path:
        path = self._domain_dir(domain_name) / _slug(block_name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ensure_index(self, path: Path, title: str, body: str) -> None:
        if not path.exists():
            path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")

    def upsert_note(
        self,
        domain_name: str,
        block_name: str,
        title: str,
        summary: str,
        tags: list[str],
        links: list[str],
        source_type: str,
        record_id: str,
        extra: dict | None = None,
    ) -> str:
        domain_dir = self._domain_dir(domain_name)
        block_dir = self._block_dir(domain_name, block_name)

        self._ensure_index(
            domain_dir / "_domain_index.md",
            f"{domain_name} Domain",
            f"Domain for {domain_name}.",
        )
        self._ensure_index(
            block_dir / "_block_index.md",
            f"{block_name} Block",
            f"Block inside {domain_name}.",
        )

        note_name = _slug(title) + ".md"
        note_path = block_dir / note_name

        tag_line = " ".join(f"#{_slug(x)}" for x in (tags or []))
        link_lines = "\n".join(f"- {x}" for x in (links or []))
        extra_lines = ""
        for k, v in (extra or {}).items():
            extra_lines += f"- **{k}**: {v}\n"

        content = f"""---
title: "{title}"
domain: "{domain_name}"
block: "{block_name}"
source_type: "{source_type}"
record_id: "{record_id}"
tags: [{", ".join(repr(x) for x in (tags or []))}]
---

# {title}

## Summary
{summary}

## Tags
{tag_line if tag_line else "none"}

## Logical Links
{link_lines if link_lines else "- none"}

## Extra
{extra_lines if extra_lines else "- none"}
"""
        note_path.write_text(content, encoding="utf-8")
        return str(note_path)