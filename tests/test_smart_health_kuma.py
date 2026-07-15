# -*- coding: utf-8 -*-
"""DEV-17: /smart_health's human_health() folds in the Uptime Kuma summary
(app.services.kuma_status.kuma_summary). Kuma is never live in tests here —
kuma_summary itself degrades honestly when unconfigured, and this suite
additionally proves human_health() never lets a Kuma-side exception break
/smart_health.
"""
from __future__ import annotations

import tools.jarvis_smart_telegram_control as bot


def test_human_health_includes_kuma_block(monkeypatch):
    monkeypatch.setattr(bot._kuma_status, "kuma_summary", lambda: "📡 Uptime Kuma:\n  ✅ Backend /health")
    text = bot.human_health({"root": {"status": "healthy"}})
    assert "📡 Uptime Kuma:" in text
    assert "✅ Backend /health" in text


def test_human_health_survives_kuma_summary_exception(monkeypatch):
    def _boom():
        raise RuntimeError("kuma unreachable")
    monkeypatch.setattr(bot._kuma_status, "kuma_summary", _boom)
    text = bot.human_health({"root": {"status": "healthy"}})
    assert "Kuma" in text
    assert "Backend" in text  # rest of the health report still renders
