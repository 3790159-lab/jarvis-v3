from __future__ import annotations
import random
from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.storage.db import Store
from chatter.transport.fake import FakeConsoleTransport
from chatter.run import Deps, gather_batch, process_batch
from tests.chatter.test_loader import SETTINGS, _make_client

# Keep the debounce window/ceiling tiny for gather_batch tests: FakeConsoleTransport's
# queue.get(timeout=...) blocks on REAL wall-clock time (it is not driven by the
# injected fake clock), so a 3s/15s window would make the test slow for no benefit.
FAST_SETTINGS = SETTINGS.replace("debounce_window: 3.0", "debounce_window: 0.05").replace(
    "debounce_max: 15.0", "debounce_max: 0.2"
)


def _deps(tmp_path, scripted, now=1000.0, settings=SETTINGS):
    _make_client(tmp_path, settings=settings)
    cfg = load_config(tmp_path, "demo")
    llm = FakeLLM(scripted=scripted)
    clock = {"t": now}
    return Deps(
        cfg=cfg,
        store=Store(tmp_path / "c.db"),
        brain=Brain(llm, cfg),
        rng=random.Random(0),
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    ), clock


def test_gather_batch_coalesces_within_window(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["ok"], settings=FAST_SETTINGS)
    t = FakeConsoleTransport(preload=["привет", "а сколько стоит?"], echo=False)
    batch = gather_batch(t, deps, first="привет-первый")
    # first + the two preloaded (arrive instantly, well within the window) => all three
    assert batch == ["привет-первый", "привет", "а сколько стоит?"]


def test_gather_batch_stops_on_no_more_messages(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["ok"], settings=FAST_SETTINGS)
    t = FakeConsoleTransport(preload=["привет"], echo=False)
    batch = gather_batch(t, deps, first="первое")
    assert batch == ["первое", "привет"]


def test_process_batch_sends_reply_and_persists(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["Здравствуйте! Что интересует?"])
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["привет"], t, deps)
    assert t.sent  # at least one outbound part
    assert "Здравствуйте" in " ".join(t.sent)
    hist = deps.store.history("u1")
    assert hist[0]["role"] == "user" and hist[0]["text"] == "привет"
    assert any(m["role"] == "assistant" for m in hist)
    assert t.typing_events[0] is True and t.typing_events[-1] is False


def test_process_batch_orders_read_pause_before_typing(tmp_path):
    """Bot-tell #1 at the orchestration level: the FIRST typing(True) must be
    preceded by at least one sleep (the read pause) — compose_reply already
    guarantees this ordering in the action list; this asserts run.py
    interprets that list faithfully rather than reordering/skipping it."""
    events: list[tuple] = []
    deps, clock = _deps(tmp_path, scripted=["Здравствуйте! Что интересует?"])

    def _sleep(seconds: float) -> None:
        clock["t"] += seconds
        events.append(("sleep", seconds))

    deps.sleep = _sleep

    class RecordingTransport(FakeConsoleTransport):
        def send_typing(self, on: bool) -> None:
            events.append(("typing", on))
            super().send_typing(on)

        def send(self, text: str) -> None:
            events.append(("say", text))
            super().send(text)

    t = RecordingTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["привет"], t, deps)

    assert t.sent
    assert t.typing_events[0] is True and t.typing_events[-1] is False
    first_typing_on_idx = next(i for i, e in enumerate(events) if e[0] == "typing" and e[1] is True)
    assert any(e[0] == "sleep" for e in events[:first_typing_on_idx])


def test_process_batch_marks_read_and_toggles_online_in_rhythm_order(tmp_path):
    """Fixes #1/#2 at the orchestration level: process_batch must interpret the
    new presence actions -- come online, mark the message read AFTER the read
    pause and BEFORE typing, and drop offline as the last thing."""
    events: list[tuple] = []
    deps, clock = _deps(tmp_path, scripted=["Здравствуйте! Что интересует?"])

    def _sleep(seconds: float) -> None:
        clock["t"] += seconds
        events.append(("sleep", seconds))

    deps.sleep = _sleep

    class RecordingTransport(FakeConsoleTransport):
        def set_online(self, on: bool) -> None:
            events.append(("online", on)); super().set_online(on)

        def read_acknowledge(self) -> None:
            events.append(("read_ack",)); super().read_acknowledge()

        def send_typing(self, on: bool) -> None:
            events.append(("typing", on)); super().send_typing(on)

        def send(self, text: str) -> None:
            events.append(("say", text)); super().send(text)

    t = RecordingTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["привет"], t, deps)

    assert t.sent
    kinds = [e[0] for e in events]
    assert kinds[0] == "sleep"  # notice/read pause happens first
    online_on = next(i for i, e in enumerate(events) if e == ("online", True))
    read_ack = kinds.index("read_ack")
    first_typing_on = next(i for i, e in enumerate(events) if e == ("typing", True))
    assert online_on < read_ack < first_typing_on
    # some sleeping (the read pause) precedes coming online
    assert any(e[0] == "sleep" for e in events[:online_on])
    assert events[-1] == ("online", False)  # offline is the very last action
    assert t.online_events == [True, False]
    assert t.read_acks == 1


def test_bot_question_gets_honest_reply_llm_not_consulted(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["НЕ ДОЛЖНО ИСПОЛЬЗОВАТЬСЯ"])
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["ты бот?"], t, deps)
    joined = " ".join(t.sent)
    assert "виртуальный ассистент" in joined
    assert "НЕ ДОЛЖНО" not in joined  # scripted brain reply not used for disclosure


def test_unbacked_price_is_suppressed_and_escalated(tmp_path):
    deps, clock = _deps(tmp_path, scripted=["Сделаю за 3000 руб только вам."])
    t = FakeConsoleTransport(preload=[], echo=False)
    deps.store.get_or_create_contact("u1")
    process_batch("u1", ["дешевле можно?"], t, deps)
    joined = " ".join(t.sent)
    assert "3000" not in joined  # invented price never sent
    assert deps.store.get_or_create_contact("u1")["state"] == "escalated"
