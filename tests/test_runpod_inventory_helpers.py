# -*- coding: utf-8 -*-
"""Tests for ``scripts/runpod_inventory.py`` helper functions."""
from __future__ import annotations

import time

from scripts.runpod_inventory import _input_with_timeout


def test_input_with_timeout_returns_none_on_timeout(monkeypatch):
    """If input() blocks longer than timeout, function returns None."""

    def slow_input(prompt):  # noqa: ARG001
        time.sleep(10)
        return "should not see this"

    monkeypatch.setattr("builtins.input", slow_input)

    start = time.monotonic()
    result = _input_with_timeout("test: ", timeout_sec=0.5)
    elapsed = time.monotonic() - start

    assert result is None
    assert 0.4 < elapsed < 1.5  # roughly the timeout, with some slack


def test_input_with_timeout_returns_value_when_ready(monkeypatch):
    """If input() returns within timeout, the value is returned."""
    monkeypatch.setattr("builtins.input", lambda p: "hello")  # noqa: ARG005

    result = _input_with_timeout("test: ", timeout_sec=2.0)

    assert result == "hello"
