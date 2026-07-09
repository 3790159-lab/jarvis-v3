# -*- coding: utf-8 -*-
"""Poll loop resumes from a persisted offset across restarts (Этап 1).

Companion to tests/test_polling_loop_resilience.py — same fresh-module + mocked
``http_json`` harness, but focused on the new behaviour: the loop loads the last
processed ``update_id`` at start (so the first getUpdates continues from
``last+1`` instead of ``0``), persists each processed update atomically, and
drops any ``update_id <= last`` as a duplicate.

Every test drives the REAL ``_main_inner`` loop. No network, no token, no
threads doing I/O, and the offset file lives on ``tmp_path`` — never a prod
state file. $0.
"""
from __future__ import annotations

import importlib.util
import sys
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import telegram_offset as off

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _LoopBreak(BaseException):
    """Terminates the otherwise-infinite poll loop without being swallowed by the
    loop's ``except Exception``."""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    monkeypatch.delenv("WEBHOOK_URL", raising=False)


def _get_mod():
    mod_name = f"_test_offpersist_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _neutralize_startup(mod, monkeypatch, offset_path):
    monkeypatch.setattr(mod, "_heartbeat_thread", lambda: None)
    monkeypatch.setattr(mod, "_check_backend_startup", lambda: None)
    monkeypatch.setattr(mod, "register_native_commands", lambda: None)
    monkeypatch.setattr(mod, "_devtask_boot_reconcile", lambda: None, raising=False)
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.cowork_watcher.start_watcher", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "app.services.backend_monitor.start_monitor", lambda *a, **k: None, raising=False
    )
    # Point the persisted-offset file at the test's tmp file (never prod state).
    monkeypatch.setattr(mod, "_OFFSET_PATH", offset_path)


def _offset_of(url: str) -> int:
    q = urllib.parse.urlparse(url).query
    return int(urllib.parse.parse_qs(q)["offset"][0])


def _drive(mod, monkeypatch, offset_path, responses, process_update=None):
    _neutralize_startup(mod, monkeypatch, offset_path)
    urls: list[str] = []
    seq = iter(responses)

    def fake_http_json(method, url, payload=None, timeout=180):
        urls.append(url)
        try:
            item = next(seq)
        except StopIteration:
            raise _LoopBreak
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(mod, "http_json", fake_http_json)
    pu = process_update if process_update is not None else MagicMock()
    monkeypatch.setattr(mod, "process_update", pu)

    with pytest.raises(_LoopBreak):
        mod._main_inner()
    return urls, pu


# ── resume from persisted offset ────────────────────────────────────────────


def test_first_poll_resumes_from_persisted_offset(monkeypatch, tmp_path):
    """A persisted last_update_id=500 means the very first getUpdates must ask
    for offset=501, not 0 — the whole point of persistence."""
    mod = _get_mod()
    op = tmp_path / "telegram_offset.json"
    off.save_last_update_id(op, 500)
    urls, _pu = _drive(mod, monkeypatch, op, [{"result": []}])
    assert _offset_of(urls[0]) == 501


def test_fresh_bot_with_no_persisted_offset_starts_at_zero(monkeypatch, tmp_path):
    """No offset file → sentinel -1 → first poll starts at 0 (fetch all pending),
    preserving the pre-persistence behaviour for a brand-new bot."""
    mod = _get_mod()
    op = tmp_path / "telegram_offset.json"  # absent
    urls, _pu = _drive(mod, monkeypatch, op, [{"result": []}])
    assert _offset_of(urls[0]) == 0


# ── dedupe already-processed updates ────────────────────────────────────────


def test_redelivered_update_at_or_below_last_is_skipped(monkeypatch, tmp_path):
    """If Telegram re-delivers update_id <= last processed (e.g. a confirm
    callback the old poller handled but never confirmed before being killed),
    it must NOT be dispatched again — critical for not double-firing paid
    confirm callbacks across a restart."""
    mod = _get_mod()
    op = tmp_path / "telegram_offset.json"
    off.save_last_update_id(op, 500)
    responses = [
        {"result": [
            {"update_id": 500, "message": {}},  # == last → duplicate, skip
            {"update_id": 501, "message": {}},  # > last → fresh, dispatch
        ]},
        {"result": []},
    ]
    urls, pu = _drive(mod, monkeypatch, op, responses)
    dispatched = [c.args[0]["update_id"] for c in pu.call_args_list]
    assert dispatched == [501]


# ── persist each processed update ───────────────────────────────────────────


def test_processed_update_id_is_persisted(monkeypatch, tmp_path):
    """After the loop dispatches update 700, the offset file must carry 700 so a
    restart resumes at 701."""
    mod = _get_mod()
    op = tmp_path / "telegram_offset.json"
    responses = [
        {"result": [{"update_id": 700, "message": {}}]},
        {"result": []},
    ]
    _drive(mod, monkeypatch, op, responses)
    assert off.load_last_update_id(op) == 700


def test_offset_survives_a_simulated_restart(monkeypatch, tmp_path):
    """End-to-end restart: run 1 processes 800, dies; run 2 (fresh module) must
    resume at 801 from the shared offset file."""
    op = tmp_path / "telegram_offset.json"

    mod1 = _get_mod()
    _drive(mod1, monkeypatch, op,
           [{"result": [{"update_id": 800, "message": {}}]}, {"result": []}])

    mod2 = _get_mod()
    urls2, _pu = _drive(mod2, monkeypatch, op, [{"result": []}])
    assert _offset_of(urls2[0]) == 801
