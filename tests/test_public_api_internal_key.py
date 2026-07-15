"""Prerequisite for enforce: public_api_v1._run_query must send the internal key.

``_run_query`` makes an in-process HTTP call to /api/jarvis/tools/internet/research.
Once the default-deny middleware enforces, that call needs a valid X-API-Key or it
would 401 itself. Thread JARVIS_INTERNAL_API_KEY into the request headers.
"""
from __future__ import annotations

import asyncio
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEY = "test-internal-key-runquery"


def test_run_query_sends_internal_api_key(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    import importlib
    import app.api.public_api_v1 as mod
    importlib.reload(mod)

    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"answer": "ok"}'

    def _fake_urlopen(req, timeout=None):
        # req is a urllib.request.Request — headers are title-cased internally.
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    result = asyncio.run(mod._run_query("hi", timeout=5))
    assert result == "ok"
    assert captured["headers"].get("X-api-key".lower()) == KEY
