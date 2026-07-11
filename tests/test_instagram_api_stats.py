"""/ig_stats read-only client methods: list_recent_media / get_media_insights.

Mock-only — no live Graph calls. Money-safe: both are free GETs, but still
$0-tested like the rest of the client.
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---- list_recent_media -----------------------------------------------------


def test_list_recent_media_returns_items():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    captured = {}
    payload = {"data": [
        {"id": "m1", "like_count": 3, "comments_count": 1},
        {"id": "m2", "like_count": 0, "comments_count": 0},
    ]}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp(payload)

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        items = api.list_recent_media(limit=5)

    assert items == payload["data"]
    assert "IGID/media" in captured["url"]
    assert "limit=5" in captured["url"]
    assert "like_count" in captured["url"]


def test_list_recent_media_empty_is_honest():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp({"data": []})):
        assert api.list_recent_media() == []


def test_list_recent_media_missing_token_raises():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="", ig_user_id="IGID")
    try:
        api.list_recent_media()
        assert False, "should raise"
    except InstagramAPIError as e:
        assert "IG_ACCESS_TOKEN" in str(e)


# ---- get_media_insights -----------------------------------------------------


def test_get_media_insights_parses_values():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    captured = {}
    payload = {"data": [
        {"name": "reach", "values": [{"value": 42}]},
        {"name": "total_interactions", "values": [{"value": 4}]},
    ]}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp(payload)

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        result = api.get_media_insights("media_1")

    assert result == {"reach": 42, "total_interactions": 4}
    assert "media_1/insights" in captured["url"]
    assert "metric=reach%2Ctotal_interactions" in captured["url"]


def test_get_media_insights_error_propagates_fail_closed():
    """Missing permission / API error -> honest InstagramAPIError, no fake numbers."""
    import io
    import urllib.error

    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    err = urllib.error.HTTPError(
        "https://graph.instagram.com/x", 400, "Bad Request", {},
        io.BytesIO(json.dumps({"error": {"message": "insights not available",
                                          "code": 100}}).encode("utf-8")),
    )
    with mock.patch("app.services.instagram_api.urllib.request.urlopen", side_effect=err):
        try:
            api.get_media_insights("media_1")
            assert False, "should raise"
        except InstagramAPIError as e:
            assert "insights not available" in str(e)


def test_get_media_insights_empty_data_returns_empty_dict():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp({"data": []})):
        assert api.get_media_insights("media_1") == {}
