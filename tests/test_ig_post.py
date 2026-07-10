# -*- coding: utf-8 -*-
"""Этап 3 / 3d: чистый оркестратор /ig_post (parse/resolve/preview/quota).

$0, ноль сети, mock-only. Здесь — ТОЛЬКО чистая логика склейки флоу; сам
публикующий (необратимый) вызов IG живёт в instagram_api и тестируется на моках
отдельно, а обвязка бота — в test_ig_post_wiring.py.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---- parse_ig_post_args ---------------------------------------------------


def test_parse_splits_source_and_topic():
    from app.services.ig_post import parse_ig_post_args

    assert parse_ig_post_args("photo.jpg вкусный борщ") == ("photo.jpg", "вкусный борщ")


def test_parse_single_token_no_topic():
    from app.services.ig_post import parse_ig_post_args

    assert parse_ig_post_args("last") == ("last", "")


def test_parse_empty():
    from app.services.ig_post import parse_ig_post_args

    assert parse_ig_post_args("   ") == ("", "")


def test_parse_multiword_topic_preserved():
    from app.services.ig_post import parse_ig_post_args

    src, topic = parse_ig_post_args("last новий сезонний напій у нас")
    assert src == "last"
    assert topic == "новий сезонний напій у нас"


# ---- is_last_token / resolve_source ---------------------------------------


def test_last_token_recognised():
    from app.services.ig_post import is_last_token

    assert is_last_token("last") is True
    assert is_last_token("LAST") is True
    assert is_last_token("последняя") is True
    assert is_last_token("-") is True
    assert is_last_token("photo.jpg") is False


def test_resolve_source_path_returned_verbatim():
    from app.services.ig_post import resolve_source

    assert resolve_source("C:/tmp/pic.png", last_path=None) == "C:/tmp/pic.png"


def test_resolve_source_last_uses_last_path():
    from app.services.ig_post import resolve_source

    assert resolve_source("last", last_path="C:/gen/out.jpg") == "C:/gen/out.jpg"


def test_resolve_source_last_without_history_raises():
    from app.services.ig_post import resolve_source, IGPostError

    try:
        resolve_source("last", last_path=None)
        assert False, "should raise"
    except IGPostError as e:
        assert "последн" in str(e).lower()


def test_resolve_source_empty_raises():
    from app.services.ig_post import resolve_source, IGPostError

    try:
        resolve_source("", last_path="X")
        assert False, "should raise"
    except IGPostError:
        pass


# ---- preview / published text ---------------------------------------------


def test_build_preview_contains_url_caption_topic():
    from app.services.ig_post import build_preview_text

    txt = build_preview_text("https://pub/x.jpg", "Смачна кава ☕ #кава", "кава")
    assert "https://pub/x.jpg" in txt
    assert "Смачна кава" in txt
    assert "кава" in txt


def test_build_published_prefers_permalink():
    from app.services.ig_post import build_published_text

    txt = build_published_text("https://instagram.com/p/ABC/", "media_1")
    assert "https://instagram.com/p/ABC/" in txt


def test_build_published_falls_back_to_media_id_when_no_permalink():
    from app.services.ig_post import build_published_text

    txt = build_published_text(None, "media_777")
    assert "media_777" in txt


# ---- quota / error handling -----------------------------------------------


def test_is_quota_error_by_code():
    from app.services.ig_post import is_quota_error

    assert is_quota_error(4) is True       # application rate limit
    assert is_quota_error(9) is True       # publishing limit
    assert is_quota_error(200) is False


def test_is_quota_error_by_subcode():
    from app.services.ig_post import is_quota_error

    assert is_quota_error(None, subcode=2207042) is True   # media posting limit
    assert is_quota_error(190, subcode=460) is False       # oauth, not quota


def test_format_publish_error_quota_mentions_limit():
    from app.services.instagram_api import InstagramAPIError
    from app.services.ig_post import format_publish_error

    exc = InstagramAPIError("The media posting limit has been reached.",
                            code=9, subcode=2207042)
    msg = format_publish_error(exc)
    assert "лимит" in msg.lower() or "25" in msg


def test_format_publish_error_generic_keeps_message():
    from app.services.instagram_api import InstagramAPIError
    from app.services.ig_post import format_publish_error

    exc = InstagramAPIError("Invalid OAuth access token.", code=190, subcode=460)
    msg = format_publish_error(exc)
    assert "Invalid OAuth" in msg
    assert "лимит" not in msg.lower()
