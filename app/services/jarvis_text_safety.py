from __future__ import annotations

def ascii_safe_summary(text: str) -> str:
    """Return stable UTF-8/console-safe summary fallback."""
    if text is None:
        return ""
    return str(text).encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def compact_status(status: str, workflow_id: str | None = None) -> str:
    workflow = workflow_id or "none"
    return f"status={status}; workflow_id={workflow}"
