"""Auto Content Generator — Block H5.3.

Generates scheduled posts every night for the next day.
Uses image history to find top dishes; falls back to defaults.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent
_IMAGE_LIB = _ROOT / "state" / "image_library.jsonl"
_SCHEDULED_DIR = _ROOT / "state" / "scheduled_posts"
_SCHEDULED_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_DISHES = ["борщ", "шашлык", "пельмени"]
_DEFAULT_POST_HOUR = 14  # 14:00 optimal food post time


def get_top_dishes(days: int = 30) -> List[str]:
    """Find most frequently requested dishes from image history."""
    if not _IMAGE_LIB.exists():
        return list(_DEFAULT_DISHES)

    cutoff = datetime.now() - timedelta(days=days)
    dish_counts: Dict[str, int] = {}

    try:
        for line in _IMAGE_LIB.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                # Only restaurant mode records
                if rec.get("mode") not in ("restaurant", "social_post"):
                    continue
                ts_str = rec.get("created_at") or rec.get("timestamp") or ""
                try:
                    ts = datetime.fromisoformat(ts_str[:19])
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
                dish = rec.get("dish") or rec.get("subject") or ""
                if dish:
                    dish_counts[dish.lower()] = dish_counts.get(dish.lower(), 0) + 1
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("[auto_content] image lib read error: %s", exc)

    if not dish_counts:
        return list(_DEFAULT_DISHES)

    return sorted(dish_counts, key=lambda k: -dish_counts[k])[:5]


def get_optimal_post_time() -> str:
    """Return optimal post time based on engagement history (default 14:00)."""
    # Future: analyze when 👍 feedback arrives most often
    return f"{_DEFAULT_POST_HOUR:02d}:00"


def schedule_post(photo_url: str, caption: str, post_time: str, date_str: Optional[str] = None) -> Dict[str, Any]:
    """Save a post to scheduled_posts/<date>.json."""
    target_date = date_str or (date.today() + timedelta(days=1)).isoformat()
    path = _SCHEDULED_DIR / f"{target_date}.json"

    posts: List[Dict[str, Any]] = []
    if path.exists():
        try:
            posts = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            posts = []

    entry = {
        "photo_url": photo_url,
        "caption": caption,
        "post_time": post_time,
        "created_at": datetime.now().isoformat(),
        "status": "scheduled",
    }
    posts.append(entry)
    path.write_text(json.dumps(posts, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def generate_tomorrow_content(num_posts: int = 3) -> List[Dict[str, Any]]:
    """Generate and schedule posts for tomorrow night.

    1. Find top dishes from history (or use defaults).
    2. For each: generate dish photo + caption.
    3. Save to state/scheduled_posts/<tomorrow>.json.
    """
    import sys
    root = str(_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)

    top_dishes = get_top_dishes()[:num_posts]
    if not top_dishes:
        top_dishes = list(_DEFAULT_DISHES[:num_posts])

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    post_time = get_optimal_post_time()
    results: List[Dict[str, Any]] = []

    for i, dish in enumerate(top_dishes):
        try:
            from app.services.restaurant_mode import generate_social_post
            post_data = generate_social_post(dish, style="instagram")
            url = post_data.get("url") or post_data.get("image_url") or ""
            caption = post_data.get("caption") or dish
            hashtags = post_data.get("hashtags") or ""
            full_caption = f"{caption}\n\n{hashtags}" if hashtags else caption

            # Stagger post times by 30 min
            hour = _DEFAULT_POST_HOUR + (i * 2)
            staggered_time = f"{hour % 24:02d}:00"

            entry = schedule_post(url, full_caption, staggered_time, tomorrow)
            entry["dish"] = dish
            results.append(entry)
        except Exception as exc:
            logger.warning("[auto_content] error for %s: %s", dish, exc)
            results.append({"dish": dish, "error": str(exc), "status": "failed"})

    return results


def get_scheduled_posts(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load scheduled posts for given date (default: tomorrow)."""
    target = date_str or (date.today() + timedelta(days=1)).isoformat()
    path = _SCHEDULED_DIR / f"{target}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def format_scheduled_posts_summary(posts: List[Dict[str, Any]]) -> str:
    """Format scheduled posts list for Telegram."""
    if not posts:
        return "Нет запланированных постов."
    lines = [f"📅 Запланировано постов: {len(posts)}\n"]
    for p in posts:
        status = "✅" if p.get("status") == "scheduled" else "❌"
        dish = p.get("dish") or "?"
        pt = p.get("post_time") or "?"
        lines.append(f"{status} {dish} — {pt}")
    return "\n".join(lines)
