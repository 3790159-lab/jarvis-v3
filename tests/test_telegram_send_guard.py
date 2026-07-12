# -*- coding: utf-8 -*-
"""Tests for the shared test-isolation guard that keeps pytest runs from
sending real Telegram messages (phantom 'petya (555) New user' etc.)."""
from __future__ import annotations

import os

import app.core.notify_isolation as ti


def test_autouse_fixture_pins_disable_flag():
    # The global conftest autouse fixture must silence sends for every test
    # (JARVIS_USERS_FILE-style isolation) without any per-test opt-in.
    assert os.getenv(ti.DISABLE_ENV) == "1"


def test_running_under_pytest_detects_pytest():
    # We *are* inside a pytest process right now.
    assert ti.running_under_pytest() is True


def test_send_blocked_by_default_during_tests(monkeypatch):
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    monkeypatch.delenv("JARVIS_DISABLE_TELEGRAM_SEND", raising=False)
    assert ti.telegram_send_blocked() is True


def test_allow_env_opts_back_in(monkeypatch):
    # Tests that genuinely drive the (mocked) transport opt in explicitly.
    monkeypatch.setenv("JARVIS_ALLOW_TELEGRAM_SEND", "1")
    assert ti.telegram_send_blocked() is False


def test_not_blocked_in_production(monkeypatch):
    # Simulate a non-pytest process: no disable flag, no opt-in, JARVIS_ENV
    # unset/production => must send.
    monkeypatch.setattr(ti, "running_under_pytest", lambda: False)
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    monkeypatch.delenv("JARVIS_DISABLE_TELEGRAM_SEND", raising=False)
    monkeypatch.delenv("JARVIS_ENV", raising=False)
    assert ti.telegram_send_blocked() is False


def test_disable_env_forces_block_outside_pytest(monkeypatch):
    # The autouse fixture pins this flag; prove it blocks independently of
    # the pytest auto-detection branch.
    monkeypatch.setattr(ti, "running_under_pytest", lambda: False)
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    monkeypatch.setenv("JARVIS_DISABLE_TELEGRAM_SEND", "1")
    assert ti.telegram_send_blocked() is True


# ── JARVIS_ENV ────────────────────────────────────────────────────────────────


def test_jarvis_env_defaults_to_production_when_unset(monkeypatch):
    monkeypatch.delenv(ti.ENV_VAR, raising=False)
    assert ti.jarvis_env() == "production"
    assert ti.is_test_env() is False


def test_jarvis_env_test_reports_test():
    # The root conftest pins this for the whole session.
    assert ti.jarvis_env() == "test"
    assert ti.is_test_env() is True


def test_jarvis_env_test_blocks_send_independent_of_pytest_detection(monkeypatch):
    # JARVIS_ENV=test must gate sends on its own, not merely piggyback on
    # running_under_pytest() -- e.g. a standalone job called directly as a
    # function from a test process that isn't flagged as "under pytest".
    monkeypatch.setattr(ti, "running_under_pytest", lambda: False)
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    monkeypatch.delenv("JARVIS_DISABLE_TELEGRAM_SEND", raising=False)
    monkeypatch.setenv(ti.ENV_VAR, "test")
    assert ti.telegram_send_blocked() is True


def test_jarvis_env_production_overrides_default_test_gate(monkeypatch):
    # Explicit JARVIS_ENV=production, with no disable flag and no pytest
    # auto-detection, must send for real -- proves prod behaviour is reachable.
    monkeypatch.setattr(ti, "running_under_pytest", lambda: False)
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    monkeypatch.delenv("JARVIS_DISABLE_TELEGRAM_SEND", raising=False)
    monkeypatch.setenv(ti.ENV_VAR, "production")
    assert ti.telegram_send_blocked() is False


def test_jarvis_env_allow_wins_over_test_env(monkeypatch):
    monkeypatch.setenv(ti.ENV_VAR, "test")
    monkeypatch.setenv("JARVIS_ALLOW_TELEGRAM_SEND", "1")
    assert ti.telegram_send_blocked() is False


def test_devtask_gate_pytest_env_blanks_secrets_and_arms_guard(monkeypatch):
    # The merge gate runs pytest in the worktree as a subprocess of the BOT
    # process; without a sanitized env it inherits the real bot token and a
    # worktree test can reach the admin's real chat (the 152568 leak vector —
    # distinct from the CC dev-run, which runner.sanitized_child_env covers).
    import tools.jarvis_smart_telegram_control as c
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:REALTOKEN")
    monkeypatch.setenv("WAVESPEED_API_KEY", "ws")
    env = c._devtask_pytest_env()
    assert env["TELEGRAM_BOT_TOKEN"] == ""             # can't reach the real chat
    assert env["WAVESPEED_API_KEY"] == ""              # can't spend
    assert env["JARVIS_DISABLE_TELEGRAM_SEND"] == "1"  # app-layer guard armed too
    assert env["JARVIS_ENV"] == "test"                 # JARVIS_ENV guard armed too
    assert "PATH" in env                               # inherited env preserved
