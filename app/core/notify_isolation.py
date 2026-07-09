# -*- coding: utf-8 -*-
"""Runtime guard that stops outbound Telegram sends during pytest runs.

Two cooperating layers keep the production admin chat free of test
"phantom" messages (e.g. ``petya (555) New user``, ``RunPod guardian
pod_old``) — see ``docs/MASTER-PLAN.md`` Этап 1:

1. **Defensive flag in every send-path.** Each network egress point calls
   :func:`telegram_send_blocked` before touching the wire. When the process
   is running under pytest the send is suppressed at the source, so a test
   that forgets to mock the transport still cannot reach the real chat.
2. **Autouse fixture** (``tests/conftest.py``) that pins the disable flag on
   for every test, mirroring the ``JARVIS_USERS_FILE`` isolation pattern.

Tests that legitimately exercise the transport (with a mocked HTTP client)
opt back in via ``JARVIS_ALLOW_TELEGRAM_SEND=1``; that explicit opt-in wins
over both the disable flag and the pytest auto-detection.
"""
from __future__ import annotations

import os
import sys

#: Explicit opt-in for tests that drive the (mocked) transport on purpose.
ALLOW_ENV = "JARVIS_ALLOW_TELEGRAM_SEND"
#: Explicit disable, set by the autouse fixture (belt to the auto-detection).
DISABLE_ENV = "JARVIS_DISABLE_TELEGRAM_SEND"


def running_under_pytest() -> bool:
    """Return ``True`` when the current process is a pytest run.

    ``pytest`` is imported into ``sys.modules`` for the whole lifetime of a
    test session (collection included), while ``PYTEST_CURRENT_TEST`` is set
    only while a test executes. Checking both makes the guard true throughout
    the run without depending on either alone.
    """
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def telegram_send_blocked() -> bool:
    """Return ``True`` when an outbound Telegram send must be suppressed.

    Precedence: explicit opt-in (``JARVIS_ALLOW_TELEGRAM_SEND=1``) always
    wins, so a test can still drive a mocked transport. Otherwise the send is
    blocked when explicitly disabled (autouse fixture) or when auto-detected
    under pytest. In production (no pytest, no disable flag) it returns
    ``False`` and sending proceeds normally.
    """
    if os.getenv(ALLOW_ENV, "").strip() == "1":
        return False
    if os.getenv(DISABLE_ENV, "").strip() == "1":
        return True
    return running_under_pytest()
