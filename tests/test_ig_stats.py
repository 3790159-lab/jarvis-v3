"""Этап 3 — /ig_stats formatting logic. Pure, $0, no network."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_format_media_line_with_insights():
    from app.services.ig_stats import format_media_line

    media = {
        "id": "m1", "media_type": "IMAGE", "media_product_type": "FEED",
        "timestamp": "2026-07-10T12:00:00+0000",
        "permalink": "https://www.instagram.com/p/AAA/",
        "like_count": 3, "comments_count": 1,
    }
    line = format_media_line(media, {"reach": 42, "total_interactions": 4})
    assert "🖼" in line
    assert "2026-07-10" in line
    assert "❤️ 3" in line
    assert "💬 1" in line
    assert "reach 42" in line
    assert "https://www.instagram.com/p/AAA/" in line


def test_format_media_line_reel_icon():
    from app.services.ig_stats import format_media_line

    media = {"id": "m2", "media_type": "VIDEO", "media_product_type": "REELS",
             "timestamp": "2026-07-01T00:00:00+0000", "permalink": "",
             "like_count": 0, "comments_count": 0}
    line = format_media_line(media, {"reach": 10})
    assert "🎬" in line


def test_format_media_line_missing_insights_is_honest():
    """No insights dict (permission missing / API error) -> 'н/д', not 0 or a guess."""
    from app.services.ig_stats import format_media_line

    media = {"id": "m3", "media_type": "IMAGE", "timestamp": "2026-07-05T00:00:00+0000",
             "permalink": "", "like_count": 5, "comments_count": 2}
    line = format_media_line(media, None)
    assert "reach н/д" in line
    assert "❤️ 5" in line


def test_format_media_line_missing_counts_is_honest():
    from app.services.ig_stats import format_media_line

    media = {"id": "m4", "media_type": "IMAGE", "timestamp": "2026-07-05T00:00:00+0000",
             "permalink": ""}
    line = format_media_line(media, {})
    assert "❤️ н/д" in line
    assert "💬 н/д" in line
    assert "reach н/д" in line


def test_build_stats_text_happy_card():
    from app.services.ig_stats import build_stats_text

    profile = {"username": "jtest_lab_", "followers_count": 7, "media_count": 1}
    media_list = [{
        "id": "m1", "media_type": "IMAGE", "media_product_type": "FEED",
        "timestamp": "2026-07-10T12:00:00+0000",
        "permalink": "https://www.instagram.com/p/AAA/",
        "like_count": 3, "comments_count": 1,
    }]
    insights_by_id = {"m1": {"reach": 42, "total_interactions": 4}}

    text = build_stats_text(profile, media_list, insights_by_id)

    assert "@jtest_lab_" in text
    assert "Подписчики: 7" in text
    assert "Постов всего: 1" in text
    assert "reach 42" in text
    assert "https://www.instagram.com/p/AAA/" in text


def test_build_stats_text_no_posts_is_honest():
    from app.services.ig_stats import build_stats_text

    text = build_stats_text({"username": "u", "followers_count": 0, "media_count": 0}, [], {})
    assert "Постов пока нет" in text


def test_build_stats_text_missing_profile_fields_is_honest():
    from app.services.ig_stats import build_stats_text

    text = build_stats_text({"username": "u"}, [], {})
    assert "Подписчики: н/д" in text
    assert "Постов всего: н/д" in text


def test_build_stats_text_orders_multiple_posts():
    from app.services.ig_stats import build_stats_text

    profile = {"username": "u", "followers_count": 2, "media_count": 2}
    media_list = [
        {"id": "m1", "media_type": "IMAGE", "timestamp": "2026-07-10T00:00:00+0000",
         "permalink": "", "like_count": 1, "comments_count": 0},
        {"id": "m2", "media_type": "IMAGE", "timestamp": "2026-07-05T00:00:00+0000",
         "permalink": "", "like_count": 2, "comments_count": 1},
    ]
    insights_by_id = {"m1": {"reach": 5}, "m2": None}

    text = build_stats_text(profile, media_list, insights_by_id)
    idx_m1 = text.index("2026-07-10")
    idx_m2 = text.index("2026-07-05")
    assert idx_m1 < idx_m2
    assert "reach 5" in text
    assert "reach н/д" in text
