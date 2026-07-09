# -*- coding: utf-8 -*-
"""Canary preflight credit check (Этап 1, Фаза 8.1). $0, mocks only.

The real Anthropic API is NEVER touched under pytest — ``post`` is injected with
a fake for every classification branch. A copek-cheap canary
(``POST /v1/messages``, haiku, ``max_tokens=1``) classifies balance/key health
BEFORE a doomed (expensive) CC is spawned; any transport/other failure fails
OPEN because the on-the-fly ``cc_error`` (commit b658603) already insures a
mid-run dead balance.
"""
from app.services.devtask import preflight


class _Resp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _capture_post(resp):
    """Return a fake ``post`` that records its call and yields ``resp``."""
    calls = []

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return resp

    _post.calls = calls
    return _post


# ── classification branches ────────────────────────────────────────────────
def test_http_200_is_ok():
    res = preflight.preflight_credit_check(post=_capture_post(_Resp(200, '{"id":"x"}')))
    assert res == {"ok": True, "reason": None}


def test_http_400_credit_balance_low_blocks():
    body = '{"type":"error","error":{"message":"Your credit balance is too low"}}'
    res = preflight.preflight_credit_check(post=_capture_post(_Resp(400, body)))
    assert res == {"ok": False, "reason": "credit balance too low"}


def test_credit_balance_match_is_case_insensitive():
    body = "CREDIT BALANCE IS TOO LOW to run this request"
    res = preflight.preflight_credit_check(post=_capture_post(_Resp(400, body)))
    assert res["ok"] is False and res["reason"] == "credit balance too low"


def test_http_401_invalid_api_key_blocks():
    body = '{"type":"error","error":{"message":"invalid x-api-key"}}'
    res = preflight.preflight_credit_check(post=_capture_post(_Resp(401, body)))
    assert res["ok"] is False
    assert "невалиден" in res["reason"]


# ── fail-OPEN branches (canary is a cheap early cut, not a hard gate) ────────
def test_network_error_fails_open():
    def _boom(url, **kwargs):
        raise ConnectionError("dns down")

    res = preflight.preflight_credit_check(post=_boom)
    assert res == {"ok": True, "reason": None}


def test_timeout_fails_open():
    def _timeout(url, **kwargs):
        raise TimeoutError("read timed out")

    res = preflight.preflight_credit_check(post=_timeout)
    assert res == {"ok": True, "reason": None}


def test_http_500_fails_open():
    res = preflight.preflight_credit_check(post=_capture_post(_Resp(500, "server error")))
    assert res == {"ok": True, "reason": None}


def test_http_400_without_credit_phrase_fails_open():
    body = '{"type":"error","error":{"message":"max_tokens: must be >= 1"}}'
    res = preflight.preflight_credit_check(post=_capture_post(_Resp(400, body)))
    assert res == {"ok": True, "reason": None}


def test_http_401_without_invalid_key_phrase_fails_open():
    res = preflight.preflight_credit_check(post=_capture_post(_Resp(401, "some other 401")))
    assert res == {"ok": True, "reason": None}


# ── canary request shape (copek-cheap: haiku, max_tokens=1, one user msg) ────
def test_canary_request_is_cheap_and_correct(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    post = _capture_post(_Resp(200))
    preflight.preflight_credit_check(post=post)
    call = post.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    payload = call["json"]
    assert payload["model"] == "claude-haiku-4-5"
    assert payload["max_tokens"] == 1
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
    headers = call["headers"]
    assert headers["x-api-key"] == "sk-ant-api03-test"
    assert "anthropic-version" in headers


def test_canary_passes_a_timeout():
    post = _capture_post(_Resp(200))
    preflight.preflight_credit_check(post=post)
    assert post.calls[0].get("timeout")  # a positive, finite timeout is set
