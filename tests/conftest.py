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


@pytest.fixture(autouse=True)
def _isolate_ig_accounts_file(monkeypatch, tmp_path):
    """Point the IG multi-account store at a per-test tmp file so no test can
    read/write the live ``state/ig_accounts.json`` (same rationale as
    ``_isolate_users_file`` above — ``ig_accounts._state_file()`` falls back to
    the *relative* default when ``IG_ACCOUNTS_FILE`` is unset, and credential
    resolution can auto-migrate/write on first touch)."""
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))


@pytest.fixture(autouse=True)
def _isolate_ig_schedule_file(monkeypatch, tmp_path):
    """Point the IG schedule queue at a per-test tmp file so no test can
    read/write the live ``state/ig_scheduled_posts.json`` (same rationale as
    ``_isolate_ig_accounts_file`` above — ``ig_schedule._state_file()`` falls
    back to the *relative* default when ``IG_SCHEDULE_FILE`` is unset)."""
    monkeypatch.setenv("IG_SCHEDULE_FILE", str(tmp_path / "ig_scheduled_posts.json"))


@pytest.fixture(autouse=True)
def _silence_telegram_sends(monkeypatch):
    """Suppress every outbound Telegram send during tests so no phantom
    message (``petya (555) New user``, ``RunPod guardian pod_old`` …) leaks
    into the live admin chat.

    Mirrors :func:`_isolate_users_file`: pin a safe default via env. All
    network send-paths consult ``app.core.notify_isolation.telegram_send_blocked``,
    which honours this flag. Tests that genuinely drive the (mocked) transport
    opt back in with ``JARVIS_ALLOW_TELEGRAM_SEND=1``; that runs after this
    fixture and wins. This env layer is belt-and-suspenders to the send-path's
    own ``running_under_pytest()`` auto-detection.
    """
    monkeypatch.setenv("JARVIS_DISABLE_TELEGRAM_SEND", "1")
