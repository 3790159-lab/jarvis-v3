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
