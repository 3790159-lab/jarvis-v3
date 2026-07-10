# -*- coding: utf-8 -*-
"""Pytest runs must never write to the production log files.

Covers app/core/logging_setup.py's PYTEST_CURRENT_TEST guard (redirects any
file logging to logs/test_run.log while under pytest) and the root
conftest.py session fixture that strips any production FileHandler that
slipped through anyway (e.g. attached during collection, before
PYTEST_CURRENT_TEST is set for any specific test).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import logging_setup

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_root_handlers():
    """Isolate the root logger's handler list around each test in this file
    so setup_app_logging()'s idempotency check can't skip re-adding a
    handler because a prior test already attached one."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    for h in saved_handlers:
        root.removeHandler(h)
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
        if isinstance(h, logging.handlers.RotatingFileHandler):
            h.close()
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def test_running_under_pytest_env_is_set():
    # Sanity check for the fixture below: pytest sets this during test calls.
    assert os.environ.get("PYTEST_CURRENT_TEST")


def test_setup_app_logging_redirects_named_prod_file_under_pytest(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))

    log_path = logging_setup.setup_app_logging("jarvis_bot.log")

    assert log_path.name == "test_run.log"
    assert not (tmp_path / "jarvis_bot.log").exists()


def test_setup_app_logging_redirects_default_filename_under_pytest(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_LOG_DIR", str(tmp_path))

    log_path = logging_setup.setup_app_logging()

    assert log_path.name == "test_run.log"
    assert not (tmp_path / "jarvis.log").exists()


def test_strip_production_file_handlers_removes_stray_handlers(tmp_path):
    root = logging.getLogger()
    stray = logging.handlers.RotatingFileHandler(tmp_path / "jarvis_bot.log", delay=True)
    root.addHandler(stray)

    removed = logging_setup.strip_production_file_handlers()

    assert "jarvis_bot.log" in removed
    assert stray not in root.handlers


def test_strip_production_file_handlers_leaves_other_handlers_alone(tmp_path):
    root = logging.getLogger()
    safe = logging.handlers.RotatingFileHandler(tmp_path / "test_run.log", delay=True)
    root.addHandler(safe)

    logging_setup.strip_production_file_handlers()

    assert safe in root.handlers


def test_probe_writes_via_setup_app_logging():
    """Exercised directly by this test session, and again in isolation by
    the subprocess test below (selected via -k)."""
    logging_setup.setup_app_logging("jarvis_bot.log")
    logging.getLogger(__name__).info("probe line that must never reach jarvis_bot.log")


def test_subprocess_single_file_run_leaves_jarvis_bot_log_untouched():
    """End-to-end guard: a real `pytest <one file>` subprocess invocation
    (mirroring how the dev-task pipeline runs targeted tests) must add zero
    new lines to the real logs/jarvis_bot.log."""
    prod_log = _REPO_ROOT / "logs" / "jarvis_bot.log"
    prod_log.parent.mkdir(parents=True, exist_ok=True)
    if not prod_log.exists():
        prod_log.write_text("", encoding="utf-8")
    before = prod_log.read_text(encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_logging_setup_pytest_isolation.py::test_probe_writes_via_setup_app_logging",
            "-q",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    after = prod_log.read_text(encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert after == before, f"jarvis_bot.log grew during pytest run:\n{after[len(before):]}"
