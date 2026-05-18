# -*- coding: utf-8 -*-
"""Per-chat in-memory generation lock for Block M.2 video generation.

Prevents a single Telegram chat from queuing overlapping ``/persona_video``
calls. In-memory only — single-process bot. If the bot restarts mid-generation,
the lock is dropped (acceptable: the orphaned generation thread will either
finish or fail without a Telegram observer).
"""
from __future__ import annotations

import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass


class GenerationLockBusy(Exception):
    """Raised when a chat already has an in-flight generation."""


@dataclass(frozen=True)
class LockToken:
    """Opaque token returned by :meth:`GenerationLock.acquire`."""

    chat_id: int
    nonce: str


class GenerationLock:
    """Thread-safe per-chat lock.

    ``acquire(chat_id)`` either returns a fresh :class:`LockToken` or raises
    :class:`GenerationLockBusy`. ``release(token)`` clears the lock; passing a
    stale or unknown token is a silent no-op so cleanup is always safe.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._held: dict[int, str] = {}

    def acquire(self, chat_id: int) -> LockToken:
        with self._guard:
            if chat_id in self._held:
                raise GenerationLockBusy(
                    f"Generation already in progress for chat {chat_id}"
                )
            nonce = secrets.token_hex(8)
            self._held[chat_id] = nonce
            return LockToken(chat_id=chat_id, nonce=nonce)

    def release(self, token: LockToken) -> None:
        with self._guard:
            held = self._held.get(token.chat_id)
            if held is None or held != token.nonce:
                return
            del self._held[token.chat_id]

    def is_busy(self, chat_id: int) -> bool:
        with self._guard:
            return chat_id in self._held

    @contextmanager
    def session(self, chat_id: int):
        token = self.acquire(chat_id)
        try:
            yield token
        finally:
            self.release(token)
