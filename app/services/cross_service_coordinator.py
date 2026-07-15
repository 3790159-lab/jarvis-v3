"""Cross-Service Coordinator — Block H5.7.

Chains multiple services into intelligent workflows.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent


def _add_root() -> None:
    import sys
    r = str(_ROOT)
    if r not in sys.path:
        sys.path.insert(0, r)


class WorkflowChain:
    """Orchestrates cross-service workflows."""

    def __init__(self, notify_fn: Optional[Callable[[str], None]] = None) -> None:
        self._notify = notify_fn or (lambda msg: logger.info("[coordinator] %s", msg))

    def notify(self, msg: str) -> None:
        try:
            self._notify(msg)
        except Exception as exc:
            logger.warning("[coordinator notify] %s", exc)

    # ── Workflow: new dish added ───────────────────────────────────────────────

    async def new_dish_added(self, dish_name: str) -> Dict[str, Any]:
        """Full pipeline for a new menu item.

        1. Photo Studio: 4 photos in different styles
        2. Restaurant Mode: caption + hashtags
        3. Obsidian: save to menu/
        4. n8n: prepare for autopost (NOT publish)
        5. Schedule: suggest post time
        6. Telegram: send ready package
        """
        _add_root()
        results: Dict[str, Any] = {"dish": dish_name}

        # Step 1: Generate photos in 4 styles
        photos: List[Dict[str, Any]] = []
        try:
            from app.services.restaurant_mode import generate_dish_photo, FOOD_TEMPLATES
            for style in list(FOOD_TEMPLATES.keys())[:4]:
                try:
                    photo = generate_dish_photo(dish_name, style)
                    photos.append({"style": style, **photo})
                except Exception as exc:
                    logger.warning("[new_dish] style %s error: %s", style, exc)
            results["photos"] = len(photos)
        except Exception as exc:
            results["photos_error"] = str(exc)

        # Step 2: Caption + hashtags
        caption = ""
        hashtags = ""
        try:
            from app.services.restaurant_mode import generate_social_post
            post_data = generate_social_post(dish_name)
            caption = post_data.get("caption") or dish_name
            hashtags = post_data.get("hashtags") or ""
            results["caption_generated"] = True
        except Exception as exc:
            results["caption_error"] = str(exc)

        # Step 3: Save to Obsidian
        try:
            _save_dish_to_obsidian(dish_name, photos, caption, hashtags)
            results["obsidian_saved"] = True
        except Exception as exc:
            results["obsidian_error"] = str(exc)

        # Step 4: Prepare n8n payload (not publishing)
        try:
            best_photo = photos[0] if photos else {}
            n8n_payload = {
                "dish": dish_name,
                "photo_url": best_photo.get("url") or best_photo.get("image_url") or "",
                "caption": caption,
                "hashtags": hashtags,
                "action": "prepare",  # NOT autopost
            }
            _save_n8n_draft(n8n_payload)
            results["n8n_draft_saved"] = True
        except Exception as exc:
            results["n8n_error"] = str(exc)

        # Step 5: Suggest optimal post time
        try:
            from app.services.auto_content import get_optimal_post_time
            post_time = get_optimal_post_time()
            results["suggested_post_time"] = post_time
        except Exception as exc:
            results["schedule_error"] = str(exc)

        # Step 6: Build notification
        post_time = results.get("suggested_post_time", "14:00")
        photo_count = results.get("photos", 0)
        lines = [
            f"✅ Новое блюдо «{dish_name}» готово к публикации!",
            f"📸 Фото: {photo_count} (4 стиля)",
            f"📝 Caption: {caption[:80]}...",
            f"🕐 Рекомендуемое время поста: {post_time}",
            "",
            "Чтобы опубликовать: /social_post " + dish_name,
        ]
        self.notify("\n".join(lines))
        return results

    # ── Workflow: morning routine ──────────────────────────────────────────────

    async def morning_routine(self) -> Dict[str, Any]:
        """Send morning briefing package."""
        _add_root()
        results: Dict[str, Any] = {}

        # Fetch brief
        brief_parts: List[str] = []
        try:
            from app.services.daily_recap import get_recap, format_recap_for_telegram
            recap = get_recap()
            if recap:
                brief_parts.append(format_recap_for_telegram(recap))
                results["recap_included"] = True
        except Exception as exc:
            results["recap_error"] = str(exc)

        # Add trends
        try:
            from app.services.trend_analyzer import get_latest_insights, format_for_morning_brief
            insights = get_latest_insights()
            if insights:
                brief_parts.append(format_for_morning_brief(insights))
                results["trends_included"] = True
        except Exception as exc:
            results["trends_error"] = str(exc)

        # Add scheduled posts info
        try:
            from app.services.auto_content import get_scheduled_posts, format_scheduled_posts_summary
            posts = get_scheduled_posts()
            if posts:
                brief_parts.append(format_scheduled_posts_summary(posts))
                results["posts_included"] = True
        except Exception as exc:
            results["posts_error"] = str(exc)

        brief = "\n\n".join(brief_parts) if brief_parts else "☀️ Доброе утро!"
        self.notify(brief)
        results["brief_sent"] = True
        return results

    # ── Workflow: negative feedback received ──────────────────────────────────

    async def negative_feedback_received(self, decision_id: str) -> Dict[str, Any]:
        """Handle 👎 feedback — queue for nightly self-improvement."""
        _add_root()
        results: Dict[str, Any] = {"decision_id": decision_id}

        # Mark for self-improvement analysis (already in decisions.jsonl)
        # Just add a flag file to trigger priority analysis
        flag_dir = _ROOT / "state" / "improvement_queue"
        flag_dir.mkdir(parents=True, exist_ok=True)
        flag_file = flag_dir / f"{decision_id}.json"
        flag_file.write_text(
            json.dumps({"decision_id": decision_id, "queued_at": datetime.now().isoformat()}),
            encoding="utf-8",
        )
        results["queued_for_analysis"] = True

        # Silent acknowledgement (no Telegram message per spec)
        logger.info("[coordinator] 👎 queued for analysis: %s", decision_id)
        return results

    # ── Workflow: party event planned ─────────────────────────────────────────

    async def party_event_planned(self, theme: str, event_date: str) -> Dict[str, Any]:
        """Full party planning workflow."""
        _add_root()
        results: Dict[str, Any] = {"theme": theme, "event_date": event_date}

        # Step 1: Party promo poster
        try:
            from app.services.party_mode import generate_party_promo
            promo = generate_party_promo(theme)
            results["poster_url"] = promo.get("url") or promo.get("image_url") or ""
            results["promo_text"] = promo.get("promo_text") or ""
        except Exception as exc:
            results["poster_error"] = str(exc)

        # Step 2: Event menu suggestions (restaurant mode)
        try:
            from app.services.restaurant_mode import generate_dish_photo
            # Suggest 2 themed dishes
            themed_dishes = _get_themed_dishes(theme)
            menu_photos = []
            for dish in themed_dishes[:2]:
                try:
                    p = generate_dish_photo(dish, "dark")
                    menu_photos.append({"dish": dish, **p})
                except Exception:
                    pass
            results["menu_photos"] = len(menu_photos)
        except Exception as exc:
            results["menu_error"] = str(exc)

        # Step 3 & 4: Reminders (7 days and 1 day before)
        reminders_created = []
        try:
            from app.services.scheduler import parse_remind_text, JarvisScheduler
            sched = JarvisScheduler()
            event_dt = datetime.fromisoformat(event_date)
            remind_7 = event_dt - timedelta(days=7)
            remind_1 = event_dt - timedelta(days=1)
            for remind_dt, label in [(remind_7, "7 дней"), (remind_1, "1 день")]:
                if remind_dt > datetime.now():
                    try:
                        tid = sched.add_task(
                            action="remind",
                            params={"text": f"🎉 До мероприятия «{theme}» осталось {label}!"},
                            run_at=remind_dt,
                            chat_id=None,
                        )
                        reminders_created.append(tid)
                    except Exception:
                        pass
        except Exception as exc:
            results["reminder_error"] = str(exc)
        results["reminders_created"] = len(reminders_created)

        # Send notification with package
        poster_url = results.get("poster_url", "")
        lines = [
            f"🎉 Вечеринка «{theme}» запланирована на {event_date}!",
            f"📸 Постер готов{'.' if poster_url else ' (ошибка).'}",
            f"🍽 Меню: {results.get('menu_photos', 0)} фото",
            f"⏰ Напоминания: {len(reminders_created)} установлено",
        ]
        self.notify("\n".join(lines))
        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_dish_to_obsidian(
    dish: str, photos: List[Dict], caption: str, hashtags: str
) -> None:
    import urllib.request
    import os
    from app.services.internal_api_client import backend_headers
    backend = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
    content_lines = [
        f"# {dish}",
        "",
        f"**Added:** {date.today().isoformat()}",
        "",
        "## Photos",
        "",
    ]
    for p in photos:
        url = p.get("url") or p.get("image_url") or ""
        style = p.get("style") or "?"
        content_lines.append(f"- [{style}]({url})")
    content_lines.extend(["", "## Caption", "", caption, "", "## Hashtags", "", hashtags])
    payload = json.dumps({
        "content": "\n".join(content_lines),
        "title": f"Menu — {dish}",
        "path": f"menu/{dish}.md",
    }).encode("utf-8")
    _url = backend + "/api/jarvis/tools/obsidian/save"
    req = urllib.request.Request(
        _url,
        data=payload,
        headers=backend_headers(_url, {"Content-Type": "application/json"}),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as _:
        pass


def _save_n8n_draft(payload: Dict[str, Any]) -> None:
    drafts_dir = _ROOT / "state" / "n8n_drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{payload['dish'][:20]}.json"
    (drafts_dir / fname).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_themed_dishes(theme: str) -> List[str]:
    theme_dishes = {
        "halloween": ["тыквенный суп", "чёрная паста"],
        "nye": ["шампанское желе", "икра на тостах"],
        "birthday": ["торт", "пирожные"],
        "wedding": ["свадебный торт", "канапе"],
        "summer": ["окрошка", "шашлык"],
        "corporate": ["бизнес-ланч", "роллы"],
        "masquerade": ["устрицы", "фуа-гра"],
        "pool": ["фруктовый коктейль", "лёгкие закуски"],
    }
    return theme_dishes.get(theme.lower(), ["борщ", "шашлык"])


# Re-export for convenience
from datetime import timedelta  # noqa: E402
