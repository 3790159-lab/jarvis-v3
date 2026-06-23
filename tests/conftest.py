"""Shared pytest fixtures / test-environment hardening.

Several application modules (e.g. ``app.services.llm_client``,
``app.core.env_bootstrap``) call ``load_dotenv()`` at import time. During a
combined test run the first such import populates ``os.environ`` from the
project ``.env`` — including the *production* flag ``JARVIS_ROUTER_ENABLED=1``.
That value then leaks into router-naive tests (e.g. the whitelist dispatch
tests), which assume the bot's default behaviour where the unified LLM router
is **off** (it is opt-in: ``os.getenv("JARVIS_ROUTER_ENABLED", "0")``). The
result is order-dependent failures: plain text gets routed instead of reaching
``handle``.

The autouse fixture below pins the router to its documented default (off) for
every test, making the suite deterministic regardless of import order. Tests
that genuinely exercise the router still opt in explicitly via
``monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")``; because that call runs
after this autouse fixture, it correctly overrides the default for those tests.
"""

import pytest


@pytest.fixture(autouse=True)
def _router_disabled_by_default(monkeypatch):
    """Default the unified LLM router to OFF so .env's prod flag can't leak in.

    Router-exercising tests override this with their own
    ``monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "1")``, which wins.
    """
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")


@pytest.fixture(autouse=True)
def _isolate_users_file(monkeypatch, tmp_path):
    """Point the multi-user store at a per-test tmp file so no test can write
    to the live ``state/users.json``.

    ``users_store._state_file()`` falls back to the *relative* default
    ``state/users.json`` when ``JARVIS_USERS_FILE`` is unset. Any test that
    drives ``process_update`` from a non-whitelisted user reaches
    ``add_pending`` and writes through to that prod file (proven: a stray
    ``pending`` entry id=999 accumulated request_count=11 from test runs).
    Cost state (``JARVIS_COST_FILE``) was already isolated per-file; this gives
    the users store the same guarantee globally. Tests that set their own
    ``JARVIS_USERS_FILE`` run after this fixture, so their path still wins.
    """
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
