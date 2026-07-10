# -*- coding: utf-8 -*-
"""Root-level pytest safety net: never let file logging touch production
logs (logs/jarvis.log, logs/jarvis_bot.log).

app.core.logging_setup.setup_app_logging() already redirects to
logs/test_run.log whenever PYTEST_CURRENT_TEST is set. This fixture covers
the one gap that guard can't close on its own: a production FileHandler can
be attached during collection, before pytest sets PYTEST_CURRENT_TEST for
any test. It runs once at session start (right after collection) and once
more at session end, stripping anything that slipped through.
"""
from __future__ import annotations

import pytest

from app.core.logging_setup import strip_production_file_handlers


@pytest.fixture(autouse=True, scope="session")
def _no_production_file_handlers_in_tests():
    strip_production_file_handlers()
    yield
    strip_production_file_handlers()
