from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.api_auth import require_api_key
from app.services.file_parsers import parse_file, summarize_parse_result

router = APIRouter(prefix="/api/jarvis/files", tags=["jarvis-files"])


class ParseRequest(BaseModel):
    path: str
    mime_type: str = ""


class ExtractRequest(BaseModel):
    path: str
    mime_type: str = ""
    instruction: str = "Extract key structured data"


# ── Path confinement (H2) ───────────────────────────────────────────────────
# Requested paths are resolved *inside* an allow-list of project directories.
# Absolute paths, ``..`` traversal, dotfiles and symlinks that escape the
# allow-list are rejected before the file is ever opened.

def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _allowed_roots() -> List[str]:
    raw = os.getenv("JARVIS_FILES_ALLOWED_ROOTS", "state/incoming_files,uploads")
    roots: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        root = Path(part)
        if not root.is_absolute():
            root = _project_root() / root
        roots.append(os.path.realpath(root))
    return roots


def _looks_absolute(p: str) -> bool:
    # Posix absolute, Windows drive (``C:\``) or UNC (``\\host``) paths.
    if Path(p).is_absolute():
        return True
    if len(p) >= 2 and p[1] == ":":
        return True
    if p.startswith("\\\\") or p.startswith("//"):
        return True
    return False


def resolve_safe_path(raw: str) -> Path:
    """Resolve ``raw`` to a file inside the allow-list, or raise HTTPException.

    403 — absolute path, traversal, dotfile, or symlink escaping the allow-list.
    404 — well-formed and confined, but no such file exists.
    """
    if not raw or not raw.strip():
        raise HTTPException(status_code=403, detail="Invalid path")
    rel = raw.strip().replace("\\", "/")

    if _looks_absolute(rel):
        raise HTTPException(status_code=403, detail="Absolute paths are not allowed")

    parts = [seg for seg in Path(rel).parts if seg not in ("", "/")]
    if any(seg == ".." for seg in parts):
        raise HTTPException(status_code=403, detail="Path traversal is not allowed")
    if any(seg.startswith(".") for seg in parts):
        raise HTTPException(status_code=403, detail="Access to dotfiles is not allowed")

    for root in _allowed_roots():
        lexical = os.path.normpath(os.path.join(root, rel))
        # After normpath the candidate must still be lexically inside the root.
        if lexical != root and not lexical.startswith(root + os.sep):
            continue
        real = os.path.realpath(lexical)
        # After resolving symlinks it must *still* be inside the root.
        if real != root and not real.startswith(root + os.sep):
            raise HTTPException(
                status_code=403, detail="Symlink escapes allowed directory"
            )
        if os.path.isfile(real):
            return Path(real)

    raise HTTPException(status_code=404, detail="File not found in allowed directories")


@router.post("/parse")
async def parse_endpoint(
    req: ParseRequest, _key: str = Depends(require_api_key)
) -> Dict[str, Any]:
    safe = resolve_safe_path(req.path)
    result = parse_file(str(safe), req.mime_type)
    result["summary"] = summarize_parse_result(result, safe.name)
    return result


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "service": "jarvis_file_tools"}
