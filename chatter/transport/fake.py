from __future__ import annotations
import queue
import sys
import threading
from chatter.transport.base import Transport


class FakeConsoleTransport(Transport):
    """Console transport. A reader thread pushes stdin lines into a queue so the
    run loop can coalesce a burst of messages within the debounce window.
    In tests, pass `preload` to seed the queue and skip stdin."""

    def __init__(self, preload: list[str] | None = None, echo: bool = True):
        self._q: "queue.Queue[str]" = queue.Queue()
        self._echo = echo
        # EOF is tracked as persistent STATE (not a one-shot sentinel value in the
        # queue). A one-shot `None` sentinel would be consumed by whichever
        # `receive()` call happens to dequeue it first -- if that's an inner burst
        # -collection loop (gather_batch) rather than the outer read loop, the
        # outer loop never learns stdin closed and blocks on `receive(timeout=None)`
        # forever. With an Event, every call after EOF+drain sees closed==True and
        # returns None immediately, however many times it's asked.
        self._eof = threading.Event()
        self.sent: list[str] = []
        self.typing_events: list[bool] = []
        if preload is not None:
            for m in preload:
                self._q.put(m)
            self._reader = None
        else:
            self._reader = threading.Thread(target=self._read_stdin, daemon=True)
            self._reader.start()

    def _read_stdin(self) -> None:
        for line in sys.stdin:
            self._q.put(line.rstrip("\n"))
        self._eof.set()

    def receive(self, timeout: float | None = None) -> str | None:
        if self._eof.is_set() and self._q.empty():
            return None
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def send(self, text: str) -> None:
        self.sent.append(text)
        if self._echo:
            print(f"  <bot> {text}")

    def send_typing(self, on: bool) -> None:
        self.typing_events.append(on)
        if self._echo:
            print("  <bot печатает...>" if on else "  <bot перестал печатать>")
