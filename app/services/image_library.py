"""Image Library — persistent store for generated image metadata."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_LIBRARY_PATH = Path("state/image_library/index.jsonl")


def save_image_metadata(
    url: str,
    prompt: str,
    mode: str,
    cost: float,
    user_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Persist metadata about a generated image; return its new image_id."""
    _LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    image_id = f"img_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
    entry: Dict[str, Any] = {
        "id": image_id,
        "url": url,
        "prompt": prompt,
        "mode": mode,
        "cost": cost,
        "user_id": user_id,
        "created_at": datetime.utcnow().isoformat(),
        "metadata": metadata or {},
    }
    with _LIBRARY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return image_id


def list_images(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return the most recent *limit* images for *user_id* (newest first)."""
    if not _LIBRARY_PATH.exists():
        return []
    images: List[Dict[str, Any]] = []
    with _LIBRARY_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("user_id") == user_id:
                    images.append(entry)
            except Exception:
                pass
    return list(reversed(images))[:limit]


def get_image(image_id: str) -> Optional[Dict[str, Any]]:
    """Return a single image entry by ID, or None."""
    if not _LIBRARY_PATH.exists():
        return None
    with _LIBRARY_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("id") == image_id:
                    return entry
            except Exception:
                pass
    return None


def list_images_by_mode(user_id: str, mode: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent images filtered by generation mode."""
    all_images = list_images(user_id, limit=1000)
    filtered = [img for img in all_images if img.get("mode") == mode]
    return filtered[:limit]


def total_cost_for_user(user_id: str, days: int = 30) -> float:
    """Sum of image costs for *user_id* over the last *days* days."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    total = 0.0
    if not _LIBRARY_PATH.exists():
        return 0.0
    with _LIBRARY_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if (
                    entry.get("user_id") == user_id
                    and entry.get("created_at", "") > cutoff
                ):
                    total += entry.get("cost", 0.0)
            except Exception:
                pass
    return round(total, 4)


def delete_image(image_id: str) -> bool:
    """Remove an image entry by ID. Returns True if found and removed."""
    if not _LIBRARY_PATH.exists():
        return False
    lines: List[str] = []
    found = False
    with _LIBRARY_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("id") == image_id:
                    found = True
                    continue
            except Exception:
                pass
            lines.append(line)
    if found:
        with _LIBRARY_PATH.open("w", encoding="utf-8") as f:
            f.writelines(lines)
    return found


def count_images_for_user(user_id: str) -> int:
    """Return total image count for user."""
    if not _LIBRARY_PATH.exists():
        return 0
    count = 0
    with _LIBRARY_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get("user_id") == user_id:
                    count += 1
            except Exception:
                pass
    return count
