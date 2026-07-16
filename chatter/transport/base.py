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

    @abstractmethod
    def read_acknowledge(self) -> None:
        """Mark the current chat's inbound message(s) as read (update the read
        receipt / check marks). A human opens the chat before replying."""

    @abstractmethod
    def set_online(self, on: bool) -> None:
        """Set the account's presence online (True) or release it back to
        'last seen' (False) for the duration of a reply."""
