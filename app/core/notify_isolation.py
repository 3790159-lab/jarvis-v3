# -*- coding: utf-8 -*-
"""Runtime guard that stops outbound Telegram sends during pytest runs.

Three cooperating layers keep the production admin chat free of test
"phantom" messages (e.g. ``petya (555) New user``, ``RunPod guardian
pod_old``) — see ``docs/MASTER-PLAN.md`` Этап 1:

1. **Defensive flag in every send-path.** Each network egress point calls
   :func:`telegram_send_blocked` before touching the wire. When the process
   is running under pytest the send is suppressed at the source, so a test
   that forgets to mock the transport still cannot reach the real chat.
2. **Autouse fixture** (``tests/conftest.py``) that pins the disable flag on
   for every test, mirroring the ``JARVIS_USERS_FILE`` isolation pattern.
3. **Explicit environment flag** ``JARVIS_ENV=test`` (root ``conftest.py``
   sets it before any application module is imported). This is the
   authoritative gate for send-paths that run outside a pytest process
   proper — e.g. ``scripts/ig_token_refresh.py``, a standalone scheduled
   job that a test may still invoke as an ordinary function call. Default
   (unset, or any value other than ``"test"``) is ``"production"`` — prod
   behaviour is unchanged unless the env is explicitly set.

Tests that legitimately exercise the transport (with a mocked HTTP client)
opt back in via ``JARVIS_ALLOW_TELEGRAM_SEND=1``; that explicit opt-in wins
over the disable flag, ``JARVIS_ENV``, and the pytest auto-detection.
"""
from __future__ import annotations

import os
import sys

#: Explicit opt-in for tests that drive the (mocked) transport on purpose.
ALLOW_ENV = "JARVIS_ALLOW_TELEGRAM_SEND"
#: Explicit disable, set by the autouse fixture (belt to the auto-detection).
DISABLE_ENV = "JARVIS_DISABLE_TELEGRAM_SEND"
#: Explicit environment marker. "test" suppresses sends; anything else
#: (including unset) means production — unchanged default behaviour.
ENV_VAR = "JARVIS_ENV"


def running_under_pytest() -> bool:
    """Return ``True`` when the current process is a pytest run.

    ``pytest`` is imported into ``sys.modules`` for the whole lifetime of a
    test session (collection included), while ``PYTEST_CURRENT_TEST`` is set
    only while a test executes. Checking both makes the guard true throughout
    the run without depending on either alone.
    """
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def jarvis_env() -> str:
    """Return the normalized ``JARVIS_ENV`` value; ``"production"`` if unset."""
    value = os.getenv(ENV_VAR, "").strip().lower()
    return value or "production"


def is_test_env() -> bool:
    """Return ``True`` when ``JARVIS_ENV=test`` is explicitly set."""
    return jarvis_env() == "test"


def telegram_send_blocked() -> bool:
    """Return ``True`` when an outbound Telegram send must be suppressed.

    Precedence: explicit opt-in (``JARVIS_ALLOW_TELEGRAM_SEND=1``) always
    wins, so a test can still drive a mocked transport. Otherwise the send is
    blocked when explicitly disabled (autouse fixture), when ``JARVIS_ENV=test``,
    or when auto-detected under pytest. In production (no pytest, no disable
    flag, ``JARVIS_ENV`` unset or ``"production"``) it returns ``False`` and
    sending proceeds normally.
    """
    if os.getenv(ALLOW_ENV, "").strip() == "1":
        return False
    if os.getenv(DISABLE_ENV, "").strip() == "1":
        return True
    if is_test_env():
        return True
    return running_under_pytest()
