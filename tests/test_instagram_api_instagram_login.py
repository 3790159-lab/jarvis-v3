"""Path B — Instagram API with Instagram Login (graph.instagram.com).

The original client targets graph.facebook.com (Facebook Login / Page-linked
discovery). These tests cover the additive Instagram-Login mode: base host is
graph.instagram.com, the IG user id is discovered via ``me?fields=user_id``
(no Facebook Page), and a read-only ``get_profile()`` exists for verification.

Mock-only — no live Graph calls.
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


def test_instagram_login_uses_graph_instagram_base():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="", login_type="instagram")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"user_id": "17841400000000000", "username": "jtest_lab_"})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        api.get_profile()
    assert captured["url"].startswith("https://graph.instagram.com/")
    assert "/me" in captured["url"]


def test_base_url_arg_overrides_default():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="X",
                       base_url="https://graph.instagram.com")
    assert api.base_url == "https://graph.instagram.com"


def test_login_type_from_env(monkeypatch):
    from app.services.instagram_api import InstagramAPI

    monkeypatch.setenv("IG_LOGIN_TYPE", "instagram")
    monkeypatch.delenv("IG_GRAPH_BASE", raising=False)
    api = InstagramAPI(access_token="t", ig_user_id="X")
    assert api.base_url == "https://graph.instagram.com"


def test_ig_graph_base_env_wins(monkeypatch):
    from app.services.instagram_api import InstagramAPI

    monkeypatch.setenv("IG_GRAPH_BASE", "https://graph.instagram.com")
    api = InstagramAPI(access_token="t", ig_user_id="X")
    assert api.base_url == "https://graph.instagram.com"


def test_default_stays_facebook_when_unset(monkeypatch):
    """Back-compat: with no env/args, base remains the Facebook Graph host."""
    from app.services.instagram_api import InstagramAPI, GRAPH_API_BASE

    monkeypatch.delenv("IG_LOGIN_TYPE", raising=False)
    monkeypatch.delenv("IG_GRAPH_BASE", raising=False)
    api = InstagramAPI(access_token="t", ig_user_id="X")
    assert api.base_url == GRAPH_API_BASE
    assert "graph.facebook.com" in api.base_url


def test_get_ig_user_id_instagram_login_via_me():
    """Instagram Login discovers the id from me?fields=user_id (no Pages)."""
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="", login_type="instagram")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"user_id": "17841412939799614", "username": "jtest_lab_",
                          "id": "36685978187713069"})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        assert api.get_ig_user_id() == "17841412939799614"
    assert "fields=user_id" in captured["url"]
    assert "me" in captured["url"]


def test_get_profile_returns_fields_and_uses_token():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="LONG", ig_user_id="X", login_type="instagram")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"user_id": "17841412939799614", "username": "jtest_lab_",
                          "account_type": "BUSINESS", "media_count": 0})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        prof = api.get_profile()
    assert prof["username"] == "jtest_lab_"
    assert prof["account_type"] == "BUSINESS"
    assert "access_token=LONG" in captured["url"]


def test_refresh_long_lived_token_returns_new_token():
    from app.services.instagram_api import refresh_long_lived_token

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"access_token": "REFRESHED", "token_type": "bearer",
                          "expires_in": 5184000})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        tok = refresh_long_lived_token("OLD_LONG")
    assert tok == "REFRESHED"
    assert captured["url"].startswith("https://graph.instagram.com/")
    assert "refresh_access_token" in captured["url"]
    assert "grant_type=ig_refresh_token" in captured["url"]
    assert "access_token=OLD_LONG" in captured["url"]


def test_refresh_empty_token_raises():
    from app.services.instagram_api import refresh_long_lived_token, InstagramAPIError

    try:
        refresh_long_lived_token("")
        assert False, "should raise"
    except InstagramAPIError:
        pass


def test_refresh_no_token_in_response_raises_fail_closed():
    from app.services.instagram_api import refresh_long_lived_token, InstagramAPIError

    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp({"error": "nope"})):
        try:
            refresh_long_lived_token("OLD_LONG")
            assert False, "should raise (fail-closed: no token means no update)"
        except InstagramAPIError:
            pass


def test_instagram_login_container_hits_instagram_host():
    """Publishing container also routes to graph.instagram.com in IG-login mode."""
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID", login_type="instagram")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"id": "cont_1"})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        api.create_media_container("https://cdn/x.jpg", "cap")
    assert captured["url"].startswith("https://graph.instagram.com/")
    assert "IGID/media" in captured["url"]
