# -*- coding: utf-8 -*-
"""Tests for the standalone scheduled publisher job (scripts/ig_schedule_publisher.py).

Every network/IO seam is mocked — no real Telegram or Graph API call is ever
made under pytest. ``publish()`` and ``send_telegram()`` are exercised in
isolation, then ``main()`` end-to-end with ``ig_schedule.process_due`` doing
the real (in-memory, mocked-publish) queue bookkeeping.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ig_schedule_publisher.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ig_schedule_publisher_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ig_schedule_publisher_script"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── publish() ─────────────────────────────────────────────────────────────────


def test_publish_calls_instagram_api_with_account_key(monkeypatch):
    mod = _load_module()
    calls = {}

    class _FakeIG:
        def __init__(self, *, account_key=None):
            calls["account_key"] = account_key

        def publish_photo(self, image_url, caption):
            calls["image_url"] = image_url
            calls["caption"] = caption
            return {"id": "media_1", "permalink": "https://instagram.com/p/AAA/"}

    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", _FakeIG)

    result = mod.publish({"account_key": "vera_ai_ua", "photo_url": "https://pub/x.jpg",
                           "caption": "Смачно"})

    assert calls == {"account_key": "vera_ai_ua", "image_url": "https://pub/x.jpg",
                      "caption": "Смачно"}
    assert result == {"id": "media_1", "permalink": "https://instagram.com/p/AAA/"}


# ── send_telegram() ──────────────────────────────────────────────────────────


def test_send_telegram_suppressed_under_isolation(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr("app.core.notify_isolation.telegram_send_blocked", lambda: True)
    assert mod.send_telegram("237616472", "hi") is False


def test_send_telegram_no_token_returns_false(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr("app.core.notify_isolation.telegram_send_blocked", lambda: False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert mod.send_telegram("237616472", "hi") is False


# ── main() end-to-end (process_due wired, publish mocked) ──────────────────────


def test_main_publishes_due_post_and_notifies(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    from app.services import ig_schedule as igsc

    now = datetime(2026, 7, 13, 12, 0)
    record = igsc.enqueue(chat_id="237616472", account_key="vera_ai_ua", run_at=now,
                           pending={"photo_url": "https://pub/x.jpg", "caption": "c",
                                    "topic": "кава", "source": "s"}, now=now)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(mod, "datetime", _FixedDatetime)
    monkeypatch.setattr(mod, "publish",
                         lambda post: {"id": "media_1", "permalink": "https://instagram.com/p/AAA/"})
    notified = []
    monkeypatch.setattr(mod, "send_telegram", lambda cid, t: notified.append((cid, t)) or True)
    monkeypatch.setattr(mod, "sys", mod.sys)  # keep argv untouched

    rc = mod.main([])

    assert rc == 0
    assert notified and notified[0][0] == "237616472"
    assert "instagram.com/p/AAA" in notified[0][1]
    assert igsc.get_post(record["id"])["status"] == "published"


def test_main_no_due_posts_is_a_noop(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "sched.json"))
    notified = []
    monkeypatch.setattr(mod, "send_telegram", lambda cid, t: notified.append((cid, t)) or True)

    rc = mod.main([])

    assert rc == 0
    assert notified == []
