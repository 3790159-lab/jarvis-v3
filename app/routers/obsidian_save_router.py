"""Obsidian vault save endpoint — Phase H1.3.

POST /api/jarvis/tools/obsidian/save
  body: {content, title, folder}
  requires: JARVIS_OBSIDIAN_VAULT_PATH in .env

GET  /api/jarvis/tools/obsidian/health
  returns: {configured, vault_exists}
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/jarvis/tools/obsidian", tags=["obsidian"])


class ObsidianSaveRequest(BaseModel):
    content: str
    title: str = ""
    folder: str = "Jarvis"


def _vault_path() -> str:
    return os.getenv("JARVIS_OBSIDIAN_VAULT_PATH", "").strip()


@router.get("/health")
def obsidian_health() -> dict:
    vp = _vault_path()
    return {
        "configured": bool(vp),
        "vault_exists": bool(vp and Path(vp).exists()),
        "vault_path": vp or "(not set)",
    }


@router.post("/save")
def obsidian_save(req: ObsidianSaveRequest) -> dict:
    vault_path = _vault_path()
    if not vault_path:
        return {"ok": False, "error": "JARVIS_OBSIDIAN_VAULT_PATH not set in .env"}

    vault = Path(vault_path)
    if not vault.exists():
        return {"ok": False, "error": f"Vault not found: {vault_path}"}

    # Sanitize title
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", req.title or "Note")[:80].strip()
    if not safe_title:
        safe_title = "Note"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{safe_title}.md"

    folder = vault / req.folder
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": f"Could not create folder: {e}"}

    file_path = folder / filename
    try:
        file_path.write_text(req.content, encoding="utf-8")
    except Exception as e:
        return {"ok": False, "error": f"Could not write file: {e}"}

    return {
        "ok": True,
        "path": str(file_path.relative_to(vault)),
        "absolute_path": str(file_path),
        "filename": filename,
    }
