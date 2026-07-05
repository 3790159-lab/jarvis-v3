# -*- coding: utf-8 -*-
"""/browse_* command wiring: admin-only, money-gate, daemon, PII report. $0, mocks."""
import importlib

from app.services.browser.engine import BrowserResult

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_browse_commands_are_admin_only():
    for c in ("/browse_check", "/browse_watch", "/browse_watch_stop", "/browse_status"):
        assert c not in mod.FRIEND_ALLOWED_COMMANDS


def test_browse_check_blocked_by_money_gate(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "guard_spend", lambda uid, un, est, do: (None, "лимит на сегодня исчерпан"))
    ran = {"x": False}
    monkeypatch.setattr(mod, "_browse_run_body", lambda cid, job: ran.update(x=True))
    mod._browse_check(ADMIN, "https://ex.com цена")
    assert any("лимит" in s for s in sent)          # honest refusal
    assert ran["x"] is False                        # browser never launched


def test_browse_check_proceeds_when_gate_ok(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "guard_spend", lambda uid, un, est, do: (do(), None))  # gate passes → do_spend()

    class _Immediate:
        def __init__(self, *a, **k): self._t = k.get("target"); self._args = k.get("args", ())
        def start(self): self._t(*self._args)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    ran = {"job": None}
    monkeypatch.setattr(mod, "_browse_run_body", lambda cid, job: ran.update(job=job))
    mod._browse_check(ADMIN, "https://shop.ex.com/item цена и наличие")
    assert ran["job"] is not None
    assert ran["job"].mode == "read"                        # BU-1 is read-only
    assert ran["job"].allowed_domains == ["shop.ex.com"]    # scoped to the url's domain


def test_browse_run_body_sends_pii_safe_report(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    from app.services.browser import service as bsvc
    monkeypatch.setattr(bsvc, "run_browse", lambda job, **k: BrowserResult(
        steps=5, cost_usd=0.10, extracted="Цена 1499", stopped_reason="done",
        raw_dom="Set-Cookie: s=secret", headers={"Authorization": "Bearer sk-ant-x"}))
    mod._browse_run_body(ADMIN, _mkjob())
    body = "\n".join(sent)
    assert "1499" in body                           # extracted content delivered
    assert "Set-Cookie" not in body and "sk-ant" not in body  # no PII/cookies/tokens


def test_browse_check_rejects_non_url(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod._browse_check(ADMIN, "не-url")
    assert any("Использование" in s for s in sent)


def _mkjob():
    from app.services.browser.job import BrowserJob
    return BrowserJob(urls=["https://ex.com"], task="t", allowed_domains=["ex.com"])
