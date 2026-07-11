# -*- coding: utf-8 -*-
"""/ig_stats bot wiring (control module). $0, mocks only, read-only.

No real Graph API call (InstagramAPI mocked). Covers: free (not paid) + admin-
only registry, command dispatch, the happy card path, and fail-closed error
handling for profile/media-list/insights failures.
"""
import importlib

from app.services.instagram_api import InstagramAPIError

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


# ---- registry / role -------------------------------------------------------


def test_ig_stats_is_free_not_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/ig_stats") is False
    assert _ir.auto_exec_ok("/ig_stats") is True


def test_ig_stats_admin_only():
    assert "/ig_stats" not in mod.FRIEND_ALLOWED_COMMANDS


def test_ig_stats_command_dispatches(monkeypatch):
    fired = {}
    monkeypatch.setattr(mod, "_ig_stats_dispatch", lambda cid: fired.update(cid=cid))
    mod.handle_command(ADMIN, "/ig_stats", "", {})
    assert fired == {"cid": ADMIN}


# ---- dispatch ---------------------------------------------------------------


class _FakeIG:
    def __init__(self, profile=None, media=None, insights=None, profile_exc=None,
                 media_exc=None, insights_exc_ids=frozenset()):
        self._profile = profile or {}
        self._media = media if media is not None else []
        self._insights = insights or {}
        self._profile_exc = profile_exc
        self._media_exc = media_exc
        self._insights_exc_ids = insights_exc_ids

    def get_profile(self, fields=None):
        if self._profile_exc:
            raise self._profile_exc
        return self._profile

    def list_recent_media(self, limit=5, fields=None):
        if self._media_exc:
            raise self._media_exc
        return self._media

    def get_media_insights(self, media_id, metrics=None):
        if media_id in self._insights_exc_ids:
            raise InstagramAPIError("no insights permission")
        return self._insights.get(media_id, {})


def test_dispatch_happy_sends_card(monkeypatch):
    fake = _FakeIG(
        profile={"username": "jtest_lab_", "followers_count": 7, "media_count": 1},
        media=[{"id": "m1", "media_type": "IMAGE", "timestamp": "2026-07-10T00:00:00+0000",
               "permalink": "https://www.instagram.com/p/AAA/",
               "like_count": 3, "comments_count": 1}],
        insights={"m1": {"reach": 42, "total_interactions": 4}},
    )
    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", lambda *a, **k: fake)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._ig_stats_dispatch(ADMIN)

    assert len(sent) == 1
    assert "jtest_lab_" in sent[0]
    assert "Подписчики: 7" in sent[0]
    assert "reach 42" in sent[0]
    assert "instagram.com/p/AAA" in sent[0]


def test_dispatch_profile_failure_is_honest_and_stops(monkeypatch):
    calls = {"media": 0}

    class _Fake(_FakeIG):
        def list_recent_media(self, limit=5, fields=None):
            calls["media"] += 1
            return []

    fake = _Fake(profile_exc=InstagramAPIError("token expired"))
    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", lambda *a, **k: fake)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._ig_stats_dispatch(ADMIN)

    assert calls["media"] == 0                 # aborted before hitting media list
    assert "token expired" in sent[0]
    assert "🚫" in sent[0]


def test_dispatch_media_list_failure_is_honest(monkeypatch):
    fake = _FakeIG(profile={"username": "u", "followers_count": 1, "media_count": 1},
                   media_exc=InstagramAPIError("rate limited"))
    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", lambda *a, **k: fake)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._ig_stats_dispatch(ADMIN)

    assert "rate limited" in sent[0]
    assert "🚫" in sent[0]


def test_dispatch_single_media_insights_failure_degrades_not_crashes(monkeypatch):
    """One post's insights failing (e.g. missing permission) must not drop the
    whole card — that post's line degrades to 'н/д', the rest still shows."""
    fake = _FakeIG(
        profile={"username": "u", "followers_count": 5, "media_count": 2},
        media=[
            {"id": "m1", "media_type": "IMAGE", "timestamp": "2026-07-10T00:00:00+0000",
             "permalink": "", "like_count": 3, "comments_count": 1},
            {"id": "m2", "media_type": "IMAGE", "timestamp": "2026-07-05T00:00:00+0000",
             "permalink": "", "like_count": 1, "comments_count": 0},
        ],
        insights={"m1": {"reach": 20}},
        insights_exc_ids=frozenset({"m2"}),
    )
    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", lambda *a, **k: fake)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._ig_stats_dispatch(ADMIN)

    assert len(sent) == 1
    assert "reach 20" in sent[0]
    assert "reach н/д" in sent[0]


def test_dispatch_no_posts_is_honest(monkeypatch):
    fake = _FakeIG(profile={"username": "u", "followers_count": 0, "media_count": 0}, media=[])
    monkeypatch.setattr("app.services.instagram_api.InstagramAPI", lambda *a, **k: fake)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._ig_stats_dispatch(ADMIN)

    assert "Постов пока нет" in sent[0]
