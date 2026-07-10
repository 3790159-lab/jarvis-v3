"""Этап 3 / 3b: Instagram Graph API client — Content Publishing (two-step flow).

All tests are mock-only. No live Graph API calls (money-safe: publishing is free
but IRREVERSIBLE outward — never fired live from here).
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
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


def _http_error(payload, code=400):
    return urllib.error.HTTPError(
        "https://graph.facebook.com/x",
        code,
        "Bad Request",
        {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


# ---- token / config guards ------------------------------------------------


def test_missing_token_raises_honest_error():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="", ig_user_id="123")
    try:
        api.get_ig_user_id()
        assert False, "should raise"
    except InstagramAPIError as e:
        assert "IG_ACCESS_TOKEN" in str(e)


def test_reads_env_when_args_omitted(monkeypatch):
    from app.services.instagram_api import InstagramAPI

    monkeypatch.setenv("IG_ACCESS_TOKEN", "envtoken")
    monkeypatch.setenv("IG_USER_ID", "envuser")
    api = InstagramAPI()
    assert api.access_token == "envtoken"
    assert api.ig_user_id == "envuser"


# ---- get_ig_user_id -------------------------------------------------------


def test_get_ig_user_id_from_env_no_network():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="17841400000000000")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen") as up:
        assert api.get_ig_user_id() == "17841400000000000"
        up.assert_not_called()


def test_get_ig_user_id_discovers_via_pages():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="")
    payload = {"data": [
        {"id": "pageA"},
        {"id": "pageB", "instagram_business_account": {"id": "17841409999999999"}},
    ]}
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp(payload)):
        assert api.get_ig_user_id() == "17841409999999999"


def test_get_ig_user_id_none_linked_raises():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp({"data": [{"id": "pageA"}]})):
        try:
            api.get_ig_user_id()
            assert False, "should raise"
        except InstagramAPIError as e:
            assert "Instagram" in str(e)


# ---- create_media_container ----------------------------------------------


def test_create_media_container_returns_id():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["method"] = req.get_method()
        return _FakeResp({"id": "17999888777"})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        cid = api.create_media_container("https://cdn/img.jpg", "Привет 🌮")
    assert cid == "17999888777"
    assert "IGID/media" in captured["url"]
    assert captured["method"] == "POST"
    body = captured["data"].decode("utf-8")
    assert "image_url=" in body
    assert "caption=" in body


def test_create_media_container_no_id_raises():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp({"noid": True})):
        try:
            api.create_media_container("https://cdn/img.jpg", "cap")
            assert False, "should raise"
        except InstagramAPIError:
            pass


# ---- publish_container (mock-only, never live) ----------------------------


def test_publish_container_returns_media_id():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        return _FakeResp({"id": "media_55"})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        mid = api.publish_container("container_99")
    assert mid == "media_55"
    assert "IGID/media_publish" in captured["url"]
    assert "creation_id=container_99" in captured["data"].decode("utf-8")


def test_publish_container_no_id_raises():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp({})):
        try:
            api.publish_container("c1")
            assert False, "should raise"
        except InstagramAPIError:
            pass


# ---- Graph error envelope -------------------------------------------------


def test_graph_error_envelope_parsed():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    err_payload = {"error": {
        "message": "Invalid OAuth access token.",
        "type": "OAuthException",
        "code": 190,
        "error_subcode": 460,
        "fbtrace_id": "AbCdEf",
    }}
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    side_effect=_http_error(err_payload, code=400)):
        try:
            api.create_media_container("https://cdn/x.jpg", "c")
            assert False, "should raise"
        except InstagramAPIError as e:
            assert e.code == 190
            assert e.subcode == 460
            assert e.fbtrace_id == "AbCdEf"
            assert "Invalid OAuth" in str(e)


# ---- exchange_to_long_lived ----------------------------------------------


def test_exchange_to_long_lived_returns_token():
    from app.services.instagram_api import exchange_to_long_lived

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"access_token": "LONGLIVED", "token_type": "bearer",
                          "expires_in": 5184000})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        token = exchange_to_long_lived("SHORT", app_id="APPID", app_secret="SECRET")
    assert token == "LONGLIVED"
    assert "grant_type=fb_exchange_token" in captured["url"]
    assert "fb_exchange_token=SHORT" in captured["url"]
    assert "client_id=APPID" in captured["url"]


def test_exchange_missing_app_creds_raises(monkeypatch):
    from app.services.instagram_api import exchange_to_long_lived, InstagramAPIError

    monkeypatch.delenv("IG_APP_ID", raising=False)
    monkeypatch.delenv("IG_APP_SECRET", raising=False)
    monkeypatch.delenv("FB_APP_ID", raising=False)
    monkeypatch.delenv("FB_APP_SECRET", raising=False)
    try:
        exchange_to_long_lived("SHORT")
        assert False, "should raise"
    except InstagramAPIError as e:
        assert "APP" in str(e).upper()


def test_exchange_empty_short_token_raises():
    from app.services.instagram_api import exchange_to_long_lived, InstagramAPIError

    try:
        exchange_to_long_lived("", app_id="A", app_secret="S")
        assert False, "should raise"
    except InstagramAPIError:
        pass
