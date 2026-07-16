from __future__ import annotations
import io
import sys
import threading
from chatter.transport.base import Transport
from chatter.transport.fake import FakeConsoleTransport


def test_fake_is_transport():
    assert isinstance(FakeConsoleTransport(preload=[]), Transport)


def test_preloaded_receive_returns_in_order():
    t = FakeConsoleTransport(preload=["привет", "сколько стоит?"])
    assert t.receive(timeout=0.01) == "привет"
    assert t.receive(timeout=0.01) == "сколько стоит?"
    assert t.receive(timeout=0.01) is None  # queue drained


def test_send_and_typing_are_recorded():
    t = FakeConsoleTransport(preload=[], echo=False)
    t.send_typing(True)
    t.send("здравствуйте")
    t.send_typing(False)
    assert t.sent == ["здравствуйте"]
    assert t.typing_events == [True, False]


def test_real_stdin_eof_does_not_hang_a_later_blocking_receive(monkeypatch):
    """Regression: run.py's outer loop calls receive(timeout=None) (BLOCKING).
    A real EOF must stay observable forever after it happens, not just once.
    A one-shot `None` sentinel placed in the queue on EOF would be consumed
    by whichever caller dequeues it first (e.g. gather_batch's inner burst
    -collection loop) -- leaving the outer loop's later blocking call with
    nothing left to wake it, hanging forever."""
    monkeypatch.setattr(sys, "stdin", io.StringIO("hi\n"))
    t = FakeConsoleTransport()  # no preload -> real reader thread on the patched stdin
    assert t.receive(timeout=2.0) == "hi"
    # Two prior callers each already observe "no more input" -- simulating
    # gather_batch's inner loop consuming whatever EOF signal exists first.
    assert t.receive(timeout=2.0) is None
    assert t.receive(timeout=2.0) is None

    result: dict = {}

    def _blocking_call() -> None:
        result["v"] = t.receive(timeout=None)

    th = threading.Thread(target=_blocking_call, daemon=True)
    th.start()
    th.join(timeout=2.0)
    assert not th.is_alive(), "receive(timeout=None) hung after EOF was already observed twice"
    assert result.get("v") is None
