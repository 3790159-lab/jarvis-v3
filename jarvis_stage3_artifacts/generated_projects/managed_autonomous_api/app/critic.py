from __future__ import annotations

from pathlib import Path


def critic_check(payload: dict) -> dict:
    mode = payload.get("mode", "generic")

    if mode == "file_exists":
        path = payload.get("path")
        if not path:
            raise ValueError("path is required for file_exists critic check")

        target = Path(path)
        return {
            "ok": target.exists(),
            "path": str(target.resolve()),
            "exists": target.exists(),
        }

    if mode == "text_contains":
        path = payload.get("path")
        needle = payload.get("needle", "")
        if not path:
            raise ValueError("path is required for text_contains critic check")

        target = Path(path)
        if not target.exists():
            return {
                "ok": False,
                "path": str(target),
                "reason": "file_not_found",
            }

        content = target.read_text(encoding="utf-8")
        return {
            "ok": needle in content,
            "path": str(target.resolve()),
            "contains": needle in content,
        }

    return {
        "ok": True,
        "mode": mode,
        "message": "generic critic pass",
    }
