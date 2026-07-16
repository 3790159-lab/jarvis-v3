from __future__ import annotations
from abc import ABC, abstractmethod


class Transport(ABC):
    @abstractmethod
    def receive(self, timeout: float | None = None) -> str | None:
        """Return the next inbound message, or None if none arrives within timeout."""

    @abstractmethod
    def send(self, text: str) -> None:
        ...

    @abstractmethod
    def send_typing(self, on: bool) -> None:
        ...
