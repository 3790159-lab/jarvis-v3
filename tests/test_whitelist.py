# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.auth.whitelist`.

Whitelist is env-driven and stateless. Tests monkeypatch the two env vars
(``JARVIS_ADMIN_USER_ID``, ``JARVIS_ALLOWED_USER_IDS``) and call the
parsing / decision helpers directly — no bot, no Telegram traffic.
"""
from __future__ import annotations

import logging

import pytest

from app.services.auth import whitelist


# ── helpers ──────────────────────────────────────────────────────────────────


def _clear_env(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_ADMIN_USER_ID", raising=False)
    monkeypatch.delenv("JARVIS_ALLOWED_USER_IDS", raising=False)


# ── open mode ────────────────────────────────────────────────────────────────


def test_open_mode_allows_anyone(monkeypatch):
    """Both env vars unset → permissive mode; any user_id passes.

    Preserves the existing dev experience: the bot stays open until the
    operator explicitly configures access.
    """
    _clear_env(monkeypatch)
    assert whitelist.is_allowed(1) is True
    assert whitelist.is_allowed(999_999_999) is True


def test_open_mode_with_empty_strings_also_allows(monkeypatch):
    """Empty strings (var set but blank) behave the same as unset."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "")
    assert whitelist.is_allowed(42) is True


# ── admin bypass ─────────────────────────────────────────────────────────────


def test_admin_user_always_allowed(monkeypatch):
    """When only the admin var is set, admin passes; everyone else is denied."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "12345")
    assert whitelist.is_allowed(12345) is True
    assert whitelist.is_allowed(67890) is False


# ── allowed list ─────────────────────────────────────────────────────────────


def test_whitelisted_user_allowed(monkeypatch):
    """A user_id present in JARVIS_ALLOWED_USER_IDS is allowed."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "111,222,333")
    assert whitelist.is_allowed(222) is True


def test_non_whitelisted_user_rejected(monkeypatch):
    """A user_id NOT in the allowed list (and not admin) is rejected."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "111,222,333")
    assert whitelist.is_allowed(444) is False


def test_admin_in_allowed_list_works_either_way(monkeypatch):
    """When the admin id is also in the allowed list, both paths grant access."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "111,222")
    assert whitelist.is_allowed(111) is True  # admin bypass + list match
    assert whitelist.is_allowed(222) is True  # list match
    assert whitelist.is_allowed(333) is False


# ── CSV parsing ──────────────────────────────────────────────────────────────


def test_whitelist_parses_csv_with_whitespace(monkeypatch):
    """Whitespace around CSV entries is stripped: '123, 456 , 789' → {123,456,789}."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "123, 456 , 789")
    assert whitelist.load_allowed_user_ids() == {123, 456, 789}


def test_whitelist_ignores_empty_entries(monkeypatch):
    """Empty CSV slots are skipped: '123,,456,,,' → {123, 456}."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "123,,456,,,")
    assert whitelist.load_allowed_user_ids() == {123, 456}


def test_whitelist_invalid_entry_skipped_with_warning(monkeypatch, caplog):
    """Non-int entries are dropped, but a WARNING is logged so it's visible.

    A typo in JARVIS_ALLOWED_USER_IDS must not silently lock out users — ops
    need a breadcrumb in logs.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "123,notanint,456")
    with caplog.at_level(logging.WARNING, logger=whitelist.__name__):
        result = whitelist.load_allowed_user_ids()
    assert result == {123, 456}
    assert any(
        "notanint" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


def test_admin_var_invalid_is_treated_as_unset(monkeypatch, caplog):
    """A non-int JARVIS_ADMIN_USER_ID logs a warning and behaves as unset.

    Without a valid admin id and with the allowed list also empty, the bot
    stays in open mode (rather than silently denying everyone).
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "notanint")
    with caplog.at_level(logging.WARNING, logger=whitelist.__name__):
        assert whitelist.load_admin_user_id() is None


# ── REJECT_MESSAGE shape ─────────────────────────────────────────────────────


def test_reject_message_is_russian_polite_with_contact():
    """The user-facing reject text must name the contact (@daniil_lapin).

    Locks the wording into a constant so the bot and tests stay in sync.
    """
    assert "@daniil_lapin" in whitelist.REJECT_MESSAGE
    assert "🚫" in whitelist.REJECT_MESSAGE
