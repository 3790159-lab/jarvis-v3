# -*- coding: utf-8 -*-
"""Root-level pytest safety net: never let file logging touch production
logs (logs/jarvis.log, logs/jarvis_bot.log), and never let a pytest run
send a real Telegram alert to the production admin chat.

app.core.logging_setup.setup_app_logging() already redirects to
logs/test_run.log whenever PYTEST_CURRENT_TEST is set. This fixture covers
the one gap that guard can't close on its own: a production FileHandler can
be attached during collection, before pytest sets PYTEST_CURRENT_TEST for
any test. It runs once at session start (right after collection) and once
more at session end, stripping anything that slipped through.

``JARVIS_ENV`` is set to ``"test"`` at import time — before ``pytest`` or any
``app.*`` module is imported by this file or by test collection — so every
send-path gated on ``app.core.notify_isolation.telegram_send_blocked()``
(including standalone jobs like ``scripts/ig_token_refresh.py`` that a test
may call directly as a function, outside the pytest-process auto-detection)
is fail-closed from the very first line of the test session. ``setdefault``
so an operator who deliberately exports ``JARVIS_ENV=production`` before
running pytest (e.g. to test the production path itself) is not overridden.
"""
from __future__ import annotations

import os

os.environ.setdefault("JARVIS_ENV", "test")

import pytest

from app.core.logging_setup import strip_production_file_handlers


@pytest.fixture(autouse=True, scope="session")
def _no_production_file_handlers_in_tests():
    strip_production_file_handlers()
    yield
    strip_production_file_handlers()
