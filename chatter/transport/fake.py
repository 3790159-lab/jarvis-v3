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
        self._q: "queue.Queue[str | None]" = queue.Queue()
        self._echo = echo
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
        self._q.put(None)  # EOF sentinel

    def receive(self, timeout: float | None = None) -> str | None:
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
