from __future__ import annotations
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
