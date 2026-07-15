# -*- coding: utf-8 -*-
"""DEV-17: app.services.kuma_status — /smart_health's Uptime Kuma summary.

Kuma itself is not live in this environment (no Docker daemon here — see
report.md), so this suite never makes a real HTTP call: ``fetch`` is always
an injected fake. The goal is that /smart_health degrades honestly (a clear
"not configured" / "unreachable" message) rather than crashing when Kuma
isn't set up yet or is temporarily down.
"""
from __future__ import annotations

from app.services import kuma_status as ks


def test_no_url_configured_is_honest_not_a_crash(monkeypatch):
    monkeypatch.delenv("KUMA_STATUS_PAGE_URL", raising=False)
    summary = ks.kuma_summary()
    assert "не настроен" in summary


def test_unreachable_kuma_is_honest_not_a_crash(monkeypatch):
    monkeypatch.setenv("KUMA_STATUS_PAGE_URL", "http://127.0.0.1:3001/api/status-page/jarvis")
    summary = ks.kuma_summary(fetch=lambda url: None)
    assert "недоступен" in summary


def test_summary_lists_monitors_with_up_down_icons(monkeypatch):
    monkeypatch.setenv("KUMA_STATUS_PAGE_URL", "http://127.0.0.1:3001/api/status-page/jarvis")

    page = {
        "publicGroupList": [
            {"name": "Jarvis", "monitorList": [
                {"id": 1, "name": "Backend /health"},
                {"id": 2, "name": "Bot heartbeat"},
            ]}
        ]
    }
    heartbeat = {
        "heartbeatList": {
            "1": [{"status": 1, "time": "2026-07-15T03:14:00Z"}],
            "2": [{"status": 0, "time": "2026-07-15T03:14:00Z"}],
        }
    }

    def fetch(url):
        if "/heartbeat/" in url:
            return heartbeat
        return page

    summary = ks.kuma_summary(fetch=fetch)
    assert "✅" in summary and "Backend /health" in summary
    assert "❌" in summary and "Bot heartbeat" in summary


def test_heartbeat_url_derivation_inserts_heartbeat_segment():
    assert ks._kuma_heartbeat_url("http://h:3001/api/status-page/jarvis") == \
        "http://h:3001/api/status-page/heartbeat/jarvis"
