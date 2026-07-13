# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.costs_summary` — pure aggregation + formatting.

No I/O, no money: inputs are plain dicts/lists constructed in-test, mirroring
what ``get_costs_range`` (audit ledger, authoritative) and
``CostTracker.get_all_entries`` (block_m ledger, best-effort category data)
would hand the caller.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.costs_summary import (
    build_summary,
    categorize_operation,
    format_costs_message,
)


# ── categorize_operation ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "operation,expected",
    [
        ("lora_training", "Тренинг"),
        ("kling_video", "Видео"),
        ("me_swap_video", "Видео"),
        ("flux_lora_inference", "Фото"),
        ("flux_pro_seed", "Фото"),
        ("me_swap_photo", "Фото"),
        ("llm_router", "LLM"),
        ("grok_vision", "LLM"),
        ("voice_transcribe", "LLM"),
        ("something_unknown", "Другое"),
        (None, "Другое"),
        ("", "Другое"),
    ],
)
def test_categorize_operation(operation, expected):
    assert categorize_operation(operation) == expected


# ── build_summary ────────────────────────────────────────────────────────────


def _op_entry(day: str, operation: str, cost: float) -> dict:
    return {"ts": f"{day}T12:00:00+00:00", "operation": operation, "cost_usd": cost}


def test_build_summary_period_total_from_audit_daily_totals():
    audit = {"2026-07-10": 1.0, "2026-07-11": 0.5}
    summary = build_summary(audit, [], days=7, today=date(2026, 7, 13))
    assert summary["period_total"] == pytest.approx(1.5)
    assert summary["period_start"] == "2026-07-07"
    assert summary["period_end"] == "2026-07-13"


def test_build_summary_missing_days_treated_as_zero():
    summary = build_summary({}, [], days=3, today=date(2026, 7, 13))
    assert summary["period_total"] == 0.0
    dates = [row["date"] for row in summary["by_day"]]
    assert dates == ["2026-07-11", "2026-07-12", "2026-07-13"]
    assert all(row["cost_usd"] == 0.0 for row in summary["by_day"])


def test_build_summary_previous_period_comparison():
    audit = {
        "2026-07-12": 2.0,  # current period
        "2026-07-05": 1.0,  # previous period (7 days before current start)
    }
    summary = build_summary(audit, [], days=7, today=date(2026, 7, 13))
    assert summary["period_total"] == pytest.approx(2.0)
    assert summary["prev_total"] == pytest.approx(1.0)
    assert summary["delta"] == pytest.approx(1.0)
    assert summary["delta_pct"] == pytest.approx(100.0)


def test_build_summary_delta_pct_none_when_no_previous_spend():
    summary = build_summary({"2026-07-12": 2.0}, [], days=7, today=date(2026, 7, 13))
    assert summary["prev_total"] == 0.0
    assert summary["delta_pct"] is None


def test_build_summary_by_category_from_operation_entries():
    entries = [
        _op_entry("2026-07-12", "kling_video", 1.0),
        _op_entry("2026-07-12", "flux_lora_inference", 2.0),
        _op_entry("2026-07-12", "lora_training", 3.0),
    ]
    summary = build_summary({}, entries, days=7, today=date(2026, 7, 13))
    assert summary["by_category"]["Видео"] == pytest.approx(1.0)
    assert summary["by_category"]["Фото"] == pytest.approx(2.0)
    assert summary["by_category"]["Тренинг"] == pytest.approx(3.0)
    assert summary["by_category"]["LLM"] == pytest.approx(0.0)


def test_build_summary_operation_entries_outside_period_excluded():
    entries = [_op_entry("2026-06-01", "kling_video", 5.0)]
    summary = build_summary({}, entries, days=7, today=date(2026, 7, 13))
    assert summary["by_category"]["Видео"] == 0.0
    assert summary["top_operations"] == []


def test_build_summary_top_operations_sorted_and_limited_to_5():
    entries = [
        _op_entry("2026-07-12", "op_a", 1.0),
        _op_entry("2026-07-12", "op_b", 5.0),
        _op_entry("2026-07-12", "op_c", 3.0),
        _op_entry("2026-07-12", "op_d", 2.0),
        _op_entry("2026-07-12", "op_e", 4.0),
        _op_entry("2026-07-12", "op_f", 0.5),
    ]
    summary = build_summary({}, entries, days=7, today=date(2026, 7, 13))
    ops = [row["operation"] for row in summary["top_operations"]]
    assert ops == ["op_b", "op_e", "op_c", "op_d", "op_a"]
    assert len(summary["top_operations"]) == 5


def test_build_summary_aggregates_same_operation_across_days():
    entries = [
        _op_entry("2026-07-10", "kling_video", 1.0),
        _op_entry("2026-07-12", "kling_video", 2.0),
    ]
    summary = build_summary({}, entries, days=7, today=date(2026, 7, 13))
    assert summary["top_operations"][0] == {"operation": "kling_video", "cost_usd": 3.0}


def test_build_summary_ignores_entries_with_bad_timestamp():
    entries = [{"ts": "not-a-date", "operation": "op", "cost_usd": 1.0}]
    summary = build_summary({}, entries, days=7, today=date(2026, 7, 13))
    assert summary["top_operations"] == []


def test_build_summary_empty_inputs():
    summary = build_summary({}, [], days=7, today=date(2026, 7, 13))
    assert summary["period_total"] == 0.0
    assert summary["top_operations"] == []
    assert len(summary["by_day"]) == 7


def test_build_summary_days_clamped_to_at_least_one():
    summary = build_summary({}, [], days=0, today=date(2026, 7, 13))
    assert summary["days"] == 1
    assert len(summary["by_day"]) == 1


# ── format_costs_message ─────────────────────────────────────────────────────


def test_format_costs_message_shows_total_and_days():
    audit = {"2026-07-12": 2.0, "2026-07-13": 0.5}
    summary = build_summary(audit, [], days=7, today=date(2026, 7, 13))
    msg = format_costs_message(summary)
    assert "Траты за 7д" in msg
    assert "$2.50" in msg  # total
    assert "2026-07-12: $2.00" in msg


def test_format_costs_message_shows_categories_when_available():
    entries = [_op_entry("2026-07-12", "kling_video", 1.5)]
    summary = build_summary({"2026-07-12": 1.5}, entries, days=7, today=date(2026, 7, 13))
    msg = format_costs_message(summary)
    assert "Видео" in msg
    assert "$1.50" in msg


def test_format_costs_message_no_category_data_says_so():
    summary = build_summary({"2026-07-12": 1.0}, [], days=7, today=date(2026, 7, 13))
    msg = format_costs_message(summary)
    assert "нет данных" in msg


def test_format_costs_message_shows_top_operations():
    entries = [_op_entry("2026-07-12", "kling_video", 1.5)]
    summary = build_summary({}, entries, days=7, today=date(2026, 7, 13))
    msg = format_costs_message(summary)
    assert "Топ-5" in msg
    assert "kling_video" in msg


def test_format_costs_message_shows_comparison_with_percent():
    audit = {"2026-07-12": 2.0, "2026-07-05": 1.0}
    summary = build_summary(audit, [], days=7, today=date(2026, 7, 13))
    msg = format_costs_message(summary)
    assert "пред. период" in msg
    assert "+100%" in msg


def test_format_costs_message_empty_period_still_renders():
    summary = build_summary({}, [], days=7, today=date(2026, 7, 13))
    msg = format_costs_message(summary)
    assert "Траты за 7д" in msg
    assert "$0.00" in msg
