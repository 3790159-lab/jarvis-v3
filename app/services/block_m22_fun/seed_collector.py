# -*- coding: utf-8 -*-
"""In-memory FSM for collecting seed photos from a user during /me_seed flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.services.block_m_common.logging_setup import get_logger

logger = get_logger("seed_collector")

_DEFAULT_MAX_PHOTOS = 10


@dataclass
class SeedCollectionSession:
    """Active seed collection session for a single chat.

    Attributes:
        chat_id: Telegram chat ID.
        photo_urls: Collected photo URLs so far.
        max_photos: Target number of photos (session ends when reached).
        created_at: UTC timestamp when the session was started.
    """

    chat_id: int
    photo_urls: list[str] = field(default_factory=list)
    max_photos: int = _DEFAULT_MAX_PHOTOS
    created_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def count(self) -> int:
        return len(self.photo_urls)

    @property
    def is_complete(self) -> bool:
        return self.count >= self.max_photos

    @property
    def remaining(self) -> int:
        return max(0, self.max_photos - self.count)


class SeedCollector:
    """In-memory store for active seed collection sessions.

    One session per chat_id at a time. Starting a new session while one is
    active overwrites the old one.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, SeedCollectionSession] = {}

    def start_session(
        self, chat_id: int, max_photos: int = _DEFAULT_MAX_PHOTOS
    ) -> SeedCollectionSession:
        """Create and return a new session for chat_id, overwriting any existing."""
        session = SeedCollectionSession(chat_id=chat_id, max_photos=max_photos)
        self._sessions[chat_id] = session
        logger.info("SeedCollector: session started chat=%s max_photos=%d", chat_id, max_photos)
        return session

    def add_photo(self, chat_id: int, photo_url: str) -> SeedCollectionSession | None:
        """Add a photo to the active session.

        Returns:
            The updated session, or None if no active session exists for chat_id.
        """
        session = self._sessions.get(chat_id)
        if session is None:
            return None
        session.photo_urls.append(photo_url)
        logger.info(
            "SeedCollector: photo added chat=%s count=%d/%d",
            chat_id, session.count, session.max_photos,
        )
        return session

    def get_session(self, chat_id: int) -> SeedCollectionSession | None:
        """Return the active session for chat_id, or None."""
        return self._sessions.get(chat_id)

    def end_session(self, chat_id: int) -> SeedCollectionSession | None:
        """Remove and return the active session, or None if none exists."""
        session = self._sessions.pop(chat_id, None)
        if session is not None:
            logger.info(
                "SeedCollector: session ended chat=%s photos=%d",
                chat_id, session.count,
            )
        return session

    def has_session(self, chat_id: int) -> bool:
        """Return True if there is an active session for chat_id."""
        return chat_id in self._sessions

    def is_complete(self, chat_id: int) -> bool:
        """Return True if the session has reached its max_photos quota."""
        session = self._sessions.get(chat_id)
        return session is not None and session.is_complete
