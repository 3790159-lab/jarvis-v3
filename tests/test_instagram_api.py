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


# ---- multi-account (account_key -> app.services.ig_accounts) --------------


def test_account_key_selects_correct_account(monkeypatch, tmp_path):
    import json as _json
    from app.services.instagram_api import InstagramAPI

    f = tmp_path / "ig_accounts.json"
    f.write_text(_json.dumps({"accounts": {
        "jtest_lab_": {"account_key": "jtest_lab_", "ig_user_id": "111",
                       "username": "jtest_lab_", "access_token": "TOK_JTEST",
                       "token_refreshed_at": 1000.0},
        "vera_ai_ua": {"account_key": "vera_ai_ua", "ig_user_id": "222",
                       "username": "vera.ai.ua", "access_token": "TOK_VERA",
                       "token_refreshed_at": 2000.0},
    }}), encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))

    api = InstagramAPI(account_key="vera_ai_ua")
    assert api.access_token == "TOK_VERA"
    assert api.ig_user_id == "222"

    api2 = InstagramAPI(account_key="jtest_lab_")
    assert api2.access_token == "TOK_JTEST"
    assert api2.ig_user_id == "111"


def test_account_key_omitted_uses_default_account(monkeypatch, tmp_path):
    import json as _json
    from app.services.instagram_api import InstagramAPI

    f = tmp_path / "ig_accounts.json"
    f.write_text(_json.dumps({"accounts": {
        "jtest_lab_": {"account_key": "jtest_lab_", "ig_user_id": "111",
                       "username": "jtest_lab_", "access_token": "TOK_JTEST",
                       "token_refreshed_at": 1000.0},
    }}), encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))
    monkeypatch.delenv("IG_DEFAULT_ACCOUNT", raising=False)

    api = InstagramAPI()
    assert api.access_token == "TOK_JTEST"


def test_unknown_account_key_raises(monkeypatch, tmp_path):
    import json as _json

    from app.services.ig_accounts import IGAccountError
    from app.services.instagram_api import InstagramAPI

    f = tmp_path / "ig_accounts.json"
    f.write_text(_json.dumps({"accounts": {
        "jtest_lab_": {"account_key": "jtest_lab_", "ig_user_id": "111",
                       "username": "jtest_lab_", "access_token": "TOK_JTEST",
                       "token_refreshed_at": 1000.0},
    }}), encoding="utf-8")
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(f))

    try:
        InstagramAPI(account_key="nope")
        assert False, "should raise"
    except IGAccountError as exc:
        assert "nope" in str(exc)


def test_explicit_access_token_bypasses_account_store(monkeypatch, tmp_path):
    """Explicit access_token/ig_user_id args must still win outright — the
    account store must not even be touched (no accidental FS read)."""
    from app.services.instagram_api import InstagramAPI

    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "does_not_exist.json"))
    api = InstagramAPI(access_token="explicit_tok", ig_user_id="explicit_uid",
                       account_key="whatever_unused")
    assert api.access_token == "explicit_tok"
    assert api.ig_user_id == "explicit_uid"
    assert not (tmp_path / "does_not_exist.json").exists()


# ---- get_ig_user_id -------------------------------------------------------


def test_get_ig_user_id_from_env_no_network():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="17841400000000000")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen") as up:
        assert api.get_ig_user_id() == "17841400000000000"
        up.assert_not_called()


def test_get_ig_user_id_discovers_via_pages(monkeypatch):
    from app.services.instagram_api import InstagramAPI

    # Path A (Facebook Login) discovery — pin login_type so ambient
    # IG_LOGIN_TYPE/IG_GRAPH_BASE from a loaded .env can't flip the branch.
    monkeypatch.delenv("IG_LOGIN_TYPE", raising=False)
    monkeypatch.delenv("IG_GRAPH_BASE", raising=False)
    api = InstagramAPI(access_token="t", ig_user_id="", login_type="facebook")
    payload = {"data": [
        {"id": "pageA"},
        {"id": "pageB", "instagram_business_account": {"id": "17841409999999999"}},
    ]}
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp(payload)):
        assert api.get_ig_user_id() == "17841409999999999"


def test_get_ig_user_id_none_linked_raises(monkeypatch):
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    monkeypatch.delenv("IG_LOGIN_TYPE", raising=False)
    monkeypatch.delenv("IG_GRAPH_BASE", raising=False)
    api = InstagramAPI(access_token="t", ig_user_id="", login_type="facebook")
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


# ---- get_permalink --------------------------------------------------------


def test_get_permalink_returns_url():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"permalink": "https://www.instagram.com/p/AbC/", "id": "media_5"})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        link = api.get_permalink("media_5")
    assert link == "https://www.instagram.com/p/AbC/"
    assert "media_5" in captured["url"]
    assert "fields=permalink" in captured["url"]


def test_get_permalink_missing_raises():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    return_value=_FakeResp({"id": "media_5"})):
        try:
            api.get_permalink("media_5")
            assert False, "should raise"
        except InstagramAPIError:
            pass


# ---- publish_photo (two-step container->publish, mock-only, never live) ----


def _routed_urlopen(routes, log):
    """Build a fake urlopen dispatching by substring match on the request URL.

    ``routes`` maps a URL substring -> either a payload dict (returned as
    _FakeResp) or an Exception instance (raised). ``log`` collects hit URLs so a
    test can assert the two-step order (media -> media_publish)."""
    def fake_urlopen(req, timeout=None):
        url = req.full_url
        log.append(url)
        for needle, outcome in routes.items():
            if needle in url:
                if isinstance(outcome, Exception):
                    raise outcome
                return _FakeResp(outcome)
        raise AssertionError(f"unexpected URL: {url}")
    return fake_urlopen


def test_publish_photo_two_step_returns_id_and_permalink():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    log = []
    routes = {
        "IGID/media_publish": {"id": "media_77"},
        "IGID/media": {"id": "cont_1"},          # checked after media_publish
        "cont_1?fields=status_code": {"status_code": "FINISHED"},
        "media_77": {"permalink": "https://www.instagram.com/p/ZZZ/"},
    }
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    _routed_urlopen(routes, log)):
        result = api.publish_photo("https://pub/x.jpg", "Смачно 🌮 #їжа")
    assert result["id"] == "media_77"
    assert result["permalink"] == "https://www.instagram.com/p/ZZZ/"
    # three-step: container creation -> status poll (FINISHED) -> publish
    idx_container = next(i for i, u in enumerate(log) if "IGID/media" in u and "publish" not in u and "status_code" not in u)
    idx_status = next(i for i, u in enumerate(log) if "status_code" in u)
    idx_publish = next(i for i, u in enumerate(log) if "media_publish" in u)
    assert idx_container < idx_status < idx_publish


def test_publish_photo_quota_error_not_published():
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    log = []
    quota = _http_error({"error": {
        "message": "The media posting limit has been reached.",
        "code": 9, "error_subcode": 2207042, "fbtrace_id": "Q1",
    }}, code=400)
    routes = {
        "IGID/media_publish": quota,
        "IGID/media": {"id": "cont_1"},
        "cont_1?fields=status_code": {"status_code": "FINISHED"},
    }
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    _routed_urlopen(routes, log)):
        try:
            api.publish_photo("https://pub/x.jpg", "cap")
            assert False, "should raise (fail-closed)"
        except InstagramAPIError as e:
            assert e.code == 9
            assert e.subcode == 2207042
    # never reached permalink lookup — nothing was published
    assert not any("permalink" in u for u in log)


def test_publish_photo_permalink_failure_still_returns_media_id():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    log = []
    routes = {
        "IGID/media_publish": {"id": "media_88"},
        "IGID/media": {"id": "cont_2"},
        "cont_2?fields=status_code": {"status_code": "FINISHED"},
        "media_88": _http_error({"error": {"message": "transient", "code": 1}}, code=500),
    }
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    _routed_urlopen(routes, log)):
        result = api.publish_photo("https://pub/x.jpg", "cap")
    # published (irreversible done) but permalink unavailable — honest None
    assert result["id"] == "media_88"
    assert result["permalink"] is None


# ---- container status polling (fix: "Media ID is not available") ----------


def test_get_container_status_returns_code():
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp({"status_code": "FINISHED", "id": "cont_1"})

    with mock.patch("app.services.instagram_api.urllib.request.urlopen", fake_urlopen):
        assert api.get_container_status("cont_1") == "FINISHED"
    assert "cont_1" in captured["url"]
    assert "fields=status_code" in captured["url"]


def _status_sequence_urlopen(statuses, log):
    """Container GET returns the next status each call; create/publish fixed."""
    seq = list(statuses)

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        log.append(url)
        if "status_code" in url:
            return _FakeResp({"status_code": seq.pop(0) if seq else "FINISHED"})
        if "media_publish" in url:
            return _FakeResp({"id": "media_99"})
        if "IGID/media" in url:
            return _FakeResp({"id": "cont_1"})
        if "media_99" in url:
            return _FakeResp({"permalink": "https://www.instagram.com/p/OK/"})
        raise AssertionError(f"unexpected URL: {url}")
    return fake_urlopen


def test_publish_photo_polls_until_finished(monkeypatch):
    from app.services.instagram_api import InstagramAPI

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    slept = []
    monkeypatch.setattr("app.services.instagram_api.time.sleep", lambda s: slept.append(s))
    log = []
    # IN_PROGRESS twice, then FINISHED -> publishes
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    _status_sequence_urlopen(["IN_PROGRESS", "IN_PROGRESS", "FINISHED"], log)):
        result = api.publish_photo("https://pub/x.jpg", "cap")
    assert result["id"] == "media_99"
    assert len(slept) == 2                      # waited between the two IN_PROGRESS polls
    assert any("media_publish" in u for u in log)


def test_publish_photo_container_error_fail_closed(monkeypatch):
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    monkeypatch.setattr("app.services.instagram_api.time.sleep", lambda s: None)
    log = []
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    _status_sequence_urlopen(["ERROR"], log)):
        try:
            api.publish_photo("https://pub/x.jpg", "cap")
            assert False, "should raise (fail-closed on ERROR)"
        except InstagramAPIError as e:
            assert "error" in str(e).lower()
    assert not any("media_publish" in u for u in log)   # never published


def test_publish_photo_container_timeout_fail_closed(monkeypatch):
    from app.services.instagram_api import InstagramAPI, InstagramAPIError

    api = InstagramAPI(access_token="t", ig_user_id="IGID")
    monkeypatch.setattr("app.services.instagram_api.time.sleep", lambda s: None)
    log = []
    # always IN_PROGRESS -> times out, never publishes
    with mock.patch("app.services.instagram_api.urllib.request.urlopen",
                    _status_sequence_urlopen(["IN_PROGRESS"] * 50, log)):
        try:
            api.publish_photo("https://pub/x.jpg", "cap", max_status_checks=4)
            assert False, "should raise (fail-closed on timeout)"
        except InstagramAPIError as e:
            assert "not ready" in str(e).lower() or "process" in str(e).lower()
    assert not any("media_publish" in u for u in log)


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
