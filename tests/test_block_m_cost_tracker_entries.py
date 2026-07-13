# -*- coding: utf-8 -*-
"""Tests for ``CostTracker.get_all_entries()`` and the ``JARVIS_EXPENSES_FILE``
default-path override — the two additions made to support the ``/costs``
command's read-only report.

Isolated from ``tests/test_block_m_common.py`` (existing budget/log_expense
coverage) to keep this diff self-contained.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.block_m_common.cost_tracker import _EXPENSES_FILE, CostTracker


@pytest.fixture
def tracker(tmp_path):
    return CostTracker(expenses_file=tmp_path / "expenses.jsonl", daily_limit=1000.0)


def test_get_all_entries_empty_when_no_file(tracker):
    entries = asyncio.run(tracker.get_all_entries())
    assert entries == []


def test_get_all_entries_returns_logged_expenses(tracker):
    asyncio.run(tracker.log_expense("kling_video", 0.10, "persona_001"))
    asyncio.run(tracker.log_expense("flux_lora_inference", 0.02, "persona_002"))

    entries = asyncio.run(tracker.get_all_entries())
    assert len(entries) == 2
    ops = {e["operation"] for e in entries}
    assert ops == {"kling_video", "flux_lora_inference"}
    assert all("ts" in e and "cost_usd" in e for e in entries)


def test_get_all_entries_ignores_corrupt_lines(tracker):
    tracker._file.parent.mkdir(parents=True, exist_ok=True)
    with tracker._file.open("a", encoding="utf-8") as f:
        f.write("not json\n")
    asyncio.run(tracker.log_expense("op", 1.0, None))

    entries = asyncio.run(tracker.get_all_entries())
    assert len(entries) == 1


def test_default_expenses_file_respects_env_override(tmp_path, monkeypatch):
    override = tmp_path / "custom_expenses.jsonl"
    monkeypatch.setenv("JARVIS_EXPENSES_FILE", str(override))

    t = CostTracker()
    assert t._file == override


def test_default_expenses_file_falls_back_without_env(monkeypatch):
    monkeypatch.delenv("JARVIS_EXPENSES_FILE", raising=False)

    t = CostTracker()
    assert t._file == _EXPENSES_FILE


def test_explicit_expenses_file_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_EXPENSES_FILE", str(tmp_path / "env_wins_if_no_explicit.jsonl"))
    explicit = tmp_path / "explicit.jsonl"

    t = CostTracker(expenses_file=explicit)
    assert t._file == explicit
