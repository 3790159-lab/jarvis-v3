# -*- coding: utf-8 -*-
"""Resilience tests for the long-poll loop ``_main_inner`` (Phase-4 P0).

The ``while True`` poll loop in ``_main_inner`` is the one production path with
**zero** coverage. ``process_update`` is well tested (tests/test_process_update_port.py)
but the I/O loop that feeds it — getUpdates errors, offset recovery, empty /
malformed responses, and exceptions bubbling out of ``process_update`` — is not.

These tests drive the **real** loop with every network / Telegram / thread seam
mocked: no real HTTP, no bot token, no pods, no background threads doing I/O.
They are characterization + regression guards — they lock the resilience
contract of the existing loop without touching production logic.

Loop contract under test (tools/jarvis_smart_telegram_control.py, _main_inner)::

    offset = 0
    while True:
        try:
            ... flush stale media groups ...
            updates = http_json("GET", url, timeout=45).get("result", [])
            for upd in updates:
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                process_update(upd, media_group_buffer)
        except Exception as e:
            print("ERR:", repr(e), flush=True)
            time.sleep(3)

Same fresh-module-load harness as tests/test_process_update_port.py.
"""
from __future__ import annotations

import importlib.util
import sys
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _LoopBreak(BaseException):
    """Sentinel that terminates the otherwise-infinite poll loop.

    Subclasses ``BaseException`` (NOT ``Exception``) on purpose: the loop's
    ``except Exception`` must NOT swallow it, so it escapes ``_main_inner``
    cleanly and ends the test. If a *real* error ever escaped the loop instead,
    the ``pytest.raises(_LoopBreak)`` guard would see the wrong exception type
    and the test would fail — that is what gives these tests teeth.
    """


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # BOT_TOKEN / ALLOWED_CHAT_ID are read from env at module import; set both so
    # _main_inner clears its `Missing token` guard. No real Telegram call is made
    # because http_json is mocked.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "12345")
    # Empty WEBHOOK_URL → the poll-loop branch (not the webhook keep-alive branch).
    monkeypatch.delenv("WEBHOOK_URL", raising=False)


def _get_mod():
    """Load a fresh, isolated copy of the bot module (same idiom as the other
    bot tests) so module-level globals don't leak between tests."""
    mod_name = f"_test_pollloop_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _neutralize_startup(mod, monkeypatch):
    """Stub everything ``_main_inner`` does *before* the poll loop, so the loop
    runs in isolation: heartbeat/backend startup, cowork + backend-monitor
    threads, and the 3-second error-backoff sleep."""
    monkeypatch.setattr(mod, "_heartbeat_thread", lambda: None)
    monkeypatch.setattr(mod, "_check_backend_startup", lambda: None)
    # Native ☰ registration (setMyCommands) is a pre-loop startup step too; stub
    # it so its Telegram calls don't land in the loop's captured getUpdates URLs.
    monkeypatch.setattr(mod, "register_native_commands", lambda: None)
    # No real backoff sleeps — keeps the test instant and prevents a slow spin.
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)
    # Cowork watcher + backend monitor are imported *inside* _main_inner; stub at
    # source so the `from ... import` inside the function picks up the no-op.
    monkeypatch.setattr(
        "app.services.cowork_watcher.start_watcher", lambda *a, **k: None, raising=False
    )
    monkeypatch.setattr(
        "app.services.backend_monitor.start_monitor", lambda *a, **k: None, raising=False
    )
    # Persisted-offset file (Этап 1): isolate to a fresh temp path so these
    # characterization tests start from the -1 sentinel (offset 0) and never read
    # or write the real prod/worktree state file.
    import tempfile
    monkeypatch.setattr(
        mod, "_OFFSET_PATH", Path(tempfile.mkdtemp()) / "telegram_offset.json"
    )


def _offset_of(url: str) -> int:
    """Extract the ``offset=`` query param the loop sent to getUpdates."""
    q = urllib.parse.urlparse(url).query
    return int(urllib.parse.parse_qs(q)["offset"][0])


def _drive(mod, monkeypatch, responses, process_update=None):
    """Run the real ``_main_inner`` poll loop, feeding ``getUpdates`` from
    ``responses`` (a list where a ``dict`` is returned and an exception instance
    is raised). When the list is exhausted, ``_LoopBreak`` is raised to end the
    loop. Returns ``(urls, process_update_mock)`` where ``urls`` is every
    getUpdates URL the loop issued (captured *before* the response is yielded, so
    the terminating poll's URL is included)."""
    _neutralize_startup(mod, monkeypatch)
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


# ── getUpdates error → retry, no crash ──────────────────────────────────────


def test_getupdates_error_is_retried_without_crashing(monkeypatch):
    """A transient getUpdates failure (e.g. read timeout) must be swallowed and
    the loop must poll again on the next pass — not propagate out of the loop."""
    mod = _get_mod()
    responses = [
        TimeoutError("getUpdates read timed out"),  # poll 1 blows up
        {"result": []},                              # poll 2 recovers cleanly
    ]
    urls, pu = _drive(mod, monkeypatch, responses)

    # poll 1 (error) + poll 2 (recovery) + terminating poll  →  >= 3 getUpdates.
    assert len(urls) >= 3
    pu.assert_not_called()  # nothing valid to dispatch


def test_repeated_getupdates_errors_keep_looping(monkeypatch):
    """Several consecutive failures in a row still don't kill the loop."""
    mod = _get_mod()
    responses = [
        ConnectionError("conn reset"),
        TimeoutError("timed out"),
        OSError("network unreachable"),
        {"result": []},
    ]
    urls, _pu = _drive(mod, monkeypatch, responses)

    # 3 errors + 1 recovery + terminating poll.
    assert len(urls) >= 5


# ── offset recovery — update_id never lost ──────────────────────────────────


def test_offset_preserved_across_getupdates_error(monkeypatch):
    """The acknowledged ``offset`` lives *outside* the loop body, so a getUpdates
    error between two successful polls must NOT reset it — otherwise already-seen
    updates would be re-delivered (or lost)."""
    mod = _get_mod()
    responses = [
        {"result": [{"update_id": 100, "message": {}}]},  # poll 1 → offset 101
        RuntimeError("network blip"),                      # poll 2 errors out
        {"result": [{"update_id": 105, "message": {}}]},  # poll 3 → offset 106
        {"result": []},                                    # poll 4
    ]
    urls, pu = _drive(mod, monkeypatch, responses)
    offsets = [_offset_of(u) for u in urls]

    assert offsets[0] == 0     # first poll starts at 0
    assert offsets[1] == 101   # advanced past update 100
    assert offsets[2] == 101   # error did NOT reset the offset
    assert offsets[3] == 106   # advanced past update 105
    assert pu.call_count == 2


def test_offset_advances_monotonically_within_a_batch(monkeypatch):
    """Within one getUpdates batch the offset tracks the highest update_id+1."""
    mod = _get_mod()
    responses = [
        {"result": [
            {"update_id": 10, "message": {}},
            {"update_id": 11, "message": {}},
            {"update_id": 12, "message": {}},
        ]},
        {"result": []},
    ]
    urls, pu = _drive(mod, monkeypatch, responses)
    offsets = [_offset_of(u) for u in urls]

    assert offsets[0] == 0
    assert offsets[1] == 13           # max(update_id)+1
    assert pu.call_count == 3


# ── empty / missing-result responses ────────────────────────────────────────


def test_empty_updates_list_is_a_noop_poll(monkeypatch):
    """An empty ``result`` dispatches nothing and keeps polling."""
    mod = _get_mod()
    responses = [{"result": []}, {"result": []}]
    urls, pu = _drive(mod, monkeypatch, responses)

    pu.assert_not_called()
    assert len(urls) >= 2  # kept polling


def test_missing_result_key_is_tolerated(monkeypatch):
    """A response with no ``result`` key falls back to ``[]`` (``.get`` default)
    and must not crash the loop."""
    mod = _get_mod()
    responses = [{}, {"ok": True}]  # neither has "result"
    urls, pu = _drive(mod, monkeypatch, responses)

    pu.assert_not_called()
    assert len(urls) >= 2


# ── malformed updates ───────────────────────────────────────────────────────


def test_update_without_update_id_does_not_crash(monkeypatch):
    """A well-formed-but-id-less update uses the ``0`` default; the loop advances
    offset to 1 and still dispatches the update."""
    mod = _get_mod()
    responses = [
        {"result": [{"message": {"text": "hi"}}]},  # no update_id key
        {"result": []},
    ]
    urls, pu = _drive(mod, monkeypatch, responses)
    offsets = [_offset_of(u) for u in urls]

    pu.assert_called_once()
    assert offsets[1] == 1  # max(0, 0 + 1)


def test_malformed_non_dict_update_does_not_kill_loop(monkeypatch):
    """A non-dict item in ``result`` raises inside the try block (``.get`` on a
    str), which is caught — the loop recovers and processes the next good poll."""
    mod = _get_mod()
    responses = [
        {"result": ["garbage-not-a-dict"]},               # raises AttributeError
        {"result": [{"update_id": 7, "message": {}}]},    # loop recovers
        {"result": []},
    ]
    urls, pu = _drive(mod, monkeypatch, responses)

    # The malformed batch was skipped (its item never dispatched), but the loop
    # survived and dispatched the good update on a later poll.
    assert pu.call_count == 1
    assert pu.call_args[0][0]["update_id"] == 7


# ── exception inside process_update ─────────────────────────────────────────


def test_exception_in_process_update_does_not_kill_loop(monkeypatch):
    """An exception raised by ``process_update`` for one update must not stop the
    loop — the next poll's updates are still dispatched."""
    mod = _get_mod()
    seen: list[int] = []

    def flaky(upd, media_group_buffer=None):
        seen.append(upd["update_id"])
        if upd["update_id"] == 1:
            raise ValueError("boom inside dispatch")

    responses = [
        {"result": [{"update_id": 1, "message": {}}]},  # process_update raises
        {"result": [{"update_id": 2, "message": {}}]},  # loop must keep going
        {"result": []},
    ]
    urls, _pu = _drive(mod, monkeypatch, responses, process_update=flaky)
    offsets = [_offset_of(u) for u in urls]

    assert seen == [1, 2]      # both reached the dispatcher; the loop survived #1
    assert offsets[1] == 2     # offset advanced past the failed update (at-most-once)


def test_offset_advances_before_dispatch_so_failed_update_is_not_retried(monkeypatch):
    """Characterizes the current at-most-once contract: offset is bumped *before*
    process_update runs, so an update that makes process_update throw is NOT
    redelivered on the next poll (it has effectively been acked)."""
    mod = _get_mod()
    dispatched: list[int] = []

    def always_raise(upd, media_group_buffer=None):
        dispatched.append(upd["update_id"])
        raise RuntimeError("dispatch always fails")

    responses = [
        {"result": [{"update_id": 50, "message": {}}]},
        {"result": []},
    ]
    urls, _pu = _drive(mod, monkeypatch, responses, process_update=always_raise)
    offsets = [_offset_of(u) for u in urls]

    assert dispatched == [50]   # tried exactly once
    assert offsets[1] == 51     # next poll moved past it — not retried
