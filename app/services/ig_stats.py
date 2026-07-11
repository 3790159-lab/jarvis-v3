# -*- coding: utf-8 -*-
"""Этап 3 — /ig_stats: базовая статистика аккаунта, read-only, $0.

Флоу: InstagramAPI.get_profile() (followers_count/media_count) +
list_recent_media() (последние 5 постов) + get_media_insights() (reach/
total_interactions на каждый пост) → одна компактная карточка в Telegram.

Path B (Instagram Login, graph.instagram.com). followers_count/media_count
нужны только instagram_business_basic; per-media insights (reach/
total_interactions) — instagram_business_manage_insights (см.
references/ig-api.md скилла smm-instagram).

Этот модуль — ЧИСТАЯ логика форматирования (ноль сети, $0). Сеть/токен живут
в :mod:`app.services.instagram_api`. Fail-closed по-честному: если insights
для конкретного поста недоступны (нет разрешения/пост-Story истекла/API-сбой),
строка поста показывает "н/д" вместо выдуманной цифры — like_count/
comments_count с самого media-узла остаются надёжными независимо от insights.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "RECENT_MEDIA_LIMIT",
    "PROFILE_FIELDS",
    "MEDIA_FIELDS",
    "INSIGHTS_METRICS",
    "format_media_line",
    "build_stats_text",
]

RECENT_MEDIA_LIMIT = 5

# followers_count/media_count — instagram_business_basic (не insights).
PROFILE_FIELDS = "username,followers_count,media_count"

# like_count/comments_count живут на самом media-узле — не требуют insights.
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,timestamp,"
    "permalink,like_count,comments_count"
)

# impressions задеприкейчен для медиа, созданных после 2024-07-02 (живая дока
# Graph API insights) — не запрашиваем его вовсе, чтобы не ловить постоянный
# честный сбой. reach доступен для FEED/REELS/STORY; total_interactions — сводный.
INSIGHTS_METRICS = "reach,total_interactions"

_MEDIA_TYPE_ICON = {
    "IMAGE": "🖼",
    "VIDEO": "🎬",
    "CAROUSEL_ALBUM": "🎠",
}


def _icon_for(media: Dict[str, Any]) -> str:
    if (media.get("media_product_type") or "").upper() == "REELS":
        return "🎬"
    return _MEDIA_TYPE_ICON.get((media.get("media_type") or "").upper(), "📄")


def format_media_line(media: Dict[str, Any], insights: Optional[Dict[str, Any]]) -> str:
    """Одна строка карточки: иконка, дата, лайки/комменты, reach (или "н/д")."""
    icon = _icon_for(media)
    date = str(media.get("timestamp") or "")[:10]
    likes = media.get("like_count")
    comments = media.get("comments_count")
    likes_s = str(likes) if likes is not None else "н/д"
    comments_s = str(comments) if comments is not None else "н/д"
    reach = insights.get("reach") if insights else None
    reach_s = str(reach) if reach is not None else "н/д"
    permalink = media.get("permalink") or ""
    line = f"{icon} {date} — ❤️ {likes_s} 💬 {comments_s} 👁 reach {reach_s}"
    if permalink:
        line += f"\n{permalink}"
    return line


def build_stats_text(profile: Dict[str, Any], media_list: List[Dict[str, Any]],
                     insights_by_id: Dict[str, Optional[Dict[str, Any]]]) -> str:
    """Компактная карточка: профиль + последние посты с метриками."""
    username = profile.get("username") or "?"
    followers = profile.get("followers_count")
    media_count = profile.get("media_count")
    followers_s = str(followers) if followers is not None else "н/д"
    media_count_s = str(media_count) if media_count is not None else "н/д"

    header = [
        f"📊 IG-статистика @{username}",
        "",
        f"👥 Подписчики: {followers_s}",
        f"🖼 Постов всего: {media_count_s}",
    ]
    if not media_list:
        header.append("")
        header.append("Постов пока нет.")
        return "\n".join(header)

    parts = ["\n".join(header), f"Последние {len(media_list)} постов:"]
    for media in media_list:
        parts.append(format_media_line(media, insights_by_id.get(media.get("id"))))
    return "\n\n".join(parts)
