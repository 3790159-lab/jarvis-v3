# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.morning_digest` — pure text-formatting logic.

Zero network, zero filesystem — every section is fail-closed to an
"⚠️ N/A" line when its input signals "unavailable" (``None``/``error``),
so the digest as a whole always sends even when some sources are down.
"""
from __future__ import annotations

from app.services.morning_digest import (
    build_digest_text,
    format_balance_section,
    format_costs_section,
    format_health_section,
    format_ig_section,
)


# ── IG accounts section ─────────────────────────────────────────────────────


def test_ig_section_no_accounts():
    lines = format_ig_section([])
    assert any("нет аккаунтов" in l for l in lines)


def test_ig_section_happy_with_deltas():
    accounts = [{
        "account_key": "jtest_lab_", "username": "jtest_lab_",
        "followers": 105, "media_count": 22,
        "followers_delta": 5, "media_delta": 2,
    }]
    lines = format_ig_section(accounts)
    text = "\n".join(lines)
    assert "@jtest_lab_" in text
    assert "105" in text and "+5" in text
    assert "22" in text and "+2" in text


def test_ig_section_negative_delta_shown_honestly():
    accounts = [{
        "account_key": "a", "username": "a", "followers": 97, "media_count": 20,
        "followers_delta": -3, "media_delta": 0,
    }]
    text = "\n".join(format_ig_section(accounts))
    assert "-3" in text


def test_ig_section_no_baseline_yet_shows_value_without_delta():
    accounts = [{
        "account_key": "a", "username": "a", "followers": 10, "media_count": 1,
        "followers_delta": None, "media_delta": None,
    }]
    text = "\n".join(format_ig_section(accounts))
    assert "10" in text
    assert "+" not in text.split("👥")[1].split("🖼")[0]


def test_ig_section_fetch_error_is_na_not_crash():
    accounts = [{"account_key": "vera_ai_ua", "username": "vera_ai_ua", "error": "timeout"}]
    text = "\n".join(format_ig_section(accounts))
    assert "@vera_ai_ua" in text
    assert "N/A" in text


# ── health section ──────────────────────────────────────────────────────────


def test_health_section_bot_alive_and_backend_ok():
    lines = format_health_section(
        bot_alive=True, bot_heartbeat_age_sec=42, backend_ok=True,
        token_ages=[{"account_key": "jtest_lab_", "days_left": 45.0}],
    )
    text = "\n".join(lines)
    assert "✅" in text
    assert "42" in text
    assert "45" in text


def test_health_section_bot_heartbeat_missing_is_na():
    lines = format_health_section(
        bot_alive=None, bot_heartbeat_age_sec=None, backend_ok=None, token_ages=[],
    )
    text = "\n".join(lines)
    assert "N/A" in text


def test_health_section_bot_stale_flagged():
    lines = format_health_section(
        bot_alive=False, bot_heartbeat_age_sec=999, backend_ok=True, token_ages=[],
    )
    text = "\n".join(lines)
    assert "⚠️" in text
    assert "999" in text


def test_health_section_backend_down():
    lines = format_health_section(
        bot_alive=True, bot_heartbeat_age_sec=1, backend_ok=False, token_ages=[],
    )
    text = "\n".join(lines)
    assert "⚠️" in text


def test_health_section_token_error_is_na():
    lines = format_health_section(
        bot_alive=True, bot_heartbeat_age_sec=1, backend_ok=True,
        token_ages=[{"account_key": "vera_ai_ua", "days_left": None, "error": "no timestamp"}],
    )
    text = "\n".join(lines)
    assert "vera_ai_ua" in text
    assert "N/A" in text


# ── costs section ────────────────────────────────────────────────────────────


def test_costs_section_unavailable_is_na():
    lines = format_costs_section(None)
    assert any("N/A" in l for l in lines)


def test_costs_section_empty_is_honest():
    lines = format_costs_section({})
    assert any("нет трат" in l for l in lines)


def test_costs_section_lists_categories_and_total():
    lines = format_costs_section({"kling_video": 0.30, "flux_inference": 0.02})
    text = "\n".join(lines)
    assert "kling_video" in text and "$0.30" in text
    assert "flux_inference" in text and "$0.02" in text
    assert "$0.32" in text  # total


# ── balance section ──────────────────────────────────────────────────────────


def test_balance_section_ok():
    line = format_balance_section(True)
    assert "✅" in line


def test_balance_section_depleted():
    line = format_balance_section(False)
    assert "⚠️" in line


def test_balance_section_unknown_is_na():
    line = format_balance_section(None)
    assert "N/A" in line


# ── full digest assembly ─────────────────────────────────────────────────────


def test_build_digest_text_assembles_all_sections():
    text = build_digest_text(
        date_str="2026-07-13",
        accounts=[{
            "account_key": "jtest_lab_", "username": "jtest_lab_",
            "followers": 105, "media_count": 22,
            "followers_delta": 5, "media_delta": 2,
        }],
        bot_alive=True, bot_heartbeat_age_sec=10, backend_ok=True,
        token_ages=[{"account_key": "jtest_lab_", "days_left": 45.0}],
        costs_by_operation={"kling_video": 0.30},
        balance_ok=True,
    )
    assert "2026-07-13" in text
    assert "@jtest_lab_" in text
    assert "45" in text
    assert "kling_video" in text
    assert "✅" in text


def test_build_digest_text_all_sources_down_still_sends():
    """Fail-closed contract: every section unavailable -> still one honest message."""
    text = build_digest_text(
        date_str="2026-07-13", accounts=[], bot_alive=None,
        bot_heartbeat_age_sec=None, backend_ok=None, token_ages=[],
        costs_by_operation=None, balance_ok=None,
    )
    assert isinstance(text, str) and text.strip()
    assert text.count("N/A") >= 3
