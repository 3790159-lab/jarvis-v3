# -*- coding: utf-8 -*-
"""FSM dialog for collecting persona data over multiple Telegram messages."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DIALOGS_DIR = _ROOT / "state" / "personas" / "dialogs"

STEPS = [
    ("name", "Шаг 1/3: Введите имя персоны (например, Sofia, Anna, Maya):"),
    ("description", "Шаг 2/3: Опишите внешность (возраст, черты лица, цвет волос и глаз):"),
    ("style", "Шаг 3/3: Стиль контента (fashion / lifestyle / beauty / business):"),
]

STATUS_COLLECTING = "collecting"
STATUS_CONFIRMING = "confirming"
STATUS_GENERATING = "generating"
STATUS_WAITING_SELECTION = "waiting_selection"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

_TERMINAL = {STATUS_DONE, STATUS_CANCELLED}


class PersonaDialog:
    """Single-file FSM dialog persisted to state/personas/dialogs/{user_id}.json.

    One active dialog per user at a time (latest write wins).

    Args:
        user_id: Telegram user / chat ID.
        chat_id: Telegram chat ID for sending replies.
        session_id: If provided, loads an existing session from disk.
    """

    def __init__(
        self,
        user_id: str,
        chat_id: str,
        session_id: str | None = None,
    ) -> None:
        self.user_id = str(user_id)
        self.chat_id = str(chat_id)

        if session_id:
            self.session_id = session_id
            data = self._load() or {}
            self.status: str = data.get("status", STATUS_COLLECTING)
            self.step: int = data.get("step", 0)
            self.name: str = data.get("name", "")
            self.description: str = data.get("description", "")
            self.style: str = data.get("style", "")
            self.persona_id: str | None = data.get("persona_id")
            self.photo_urls: list[str] = data.get("photo_urls", [])
        else:
            self.session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")[:19]
            self.status = STATUS_COLLECTING
            self.step = 0
            self.name = ""
            self.description = ""
            self.style = ""
            self.persona_id = None
            self.photo_urls = []
            self._persist()

    # ── public API ─────────────────────────────────────────────────────────────

    def get_current_question(self) -> str:
        """Return the question to send to the user for the current state."""
        if self.status == STATUS_COLLECTING and self.step < len(STEPS):
            return STEPS[self.step][1]
        if self.status == STATUS_CONFIRMING:
            return (
                f"Проверьте данные персоны:\n"
                f"• Имя: {self.name}\n"
                f"• Внешность: {self.description}\n"
                f"• Стиль: {self.style}\n\n"
                f"Начать генерацию 20 фото? (да / нет)"
            )
        return ""

    def advance(self, answer: str) -> tuple[bool, str]:
        """Store user answer and advance to next step.

        Returns:
            (collection_complete, next_question_or_empty).
            collection_complete=True when all fields are gathered (now in CONFIRMING).
        """
        if self.status != STATUS_COLLECTING:
            return True, ""

        key = STEPS[self.step][0]
        setattr(self, key, answer.strip())
        self.step += 1

        if self.step >= len(STEPS):
            self.status = STATUS_CONFIRMING
            self._persist()
            return True, self.get_current_question()

        next_q = STEPS[self.step][1]
        self._persist()
        return False, next_q

    def confirm(self) -> None:
        """Transition CONFIRMING → GENERATING."""
        self.status = STATUS_GENERATING
        self._persist()

    def cancel(self) -> None:
        """Mark dialog as cancelled (terminal)."""
        self.status = STATUS_CANCELLED
        self._persist()

    def set_persona_id(self, persona_id: str) -> None:
        """Record the storage persona_id after creation."""
        self.persona_id = persona_id
        self._persist()

    def set_generation_complete(self, photo_urls: list[str]) -> None:
        """Transition GENERATING → WAITING_SELECTION with the produced URLs."""
        self.photo_urls = photo_urls
        self.status = STATUS_WAITING_SELECTION
        self._persist()

    def select_photo(self, index: int) -> str | None:
        """Pick a photo by 1-based index.  Transitions to DONE on success.

        Returns:
            The selected URL, or None if index is out of range.
        """
        if index < 1 or index > len(self.photo_urls):
            return None
        self.status = STATUS_DONE
        self._persist()
        return self.photo_urls[index - 1]

    def is_active(self) -> bool:
        """Return True if the dialog is not in a terminal state."""
        return self.status not in _TERMINAL

    # ── class methods ──────────────────────────────────────────────────────────

    @classmethod
    def find_active(cls, user_id: str) -> "PersonaDialog | None":
        """Return the active dialog for user_id if one exists, else None."""
        path = _DIALOGS_DIR / f"{user_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if data.get("status") in _TERMINAL:
            return None
        return cls(
            user_id,
            data.get("chat_id", str(user_id)),
            session_id=data["session_id"],
        )

    # ── private helpers ────────────────────────────────────────────────────────

    def _dialog_path(self) -> Path:
        return _DIALOGS_DIR / f"{self.user_id}.json"

    def _persist(self) -> None:
        path = self._dialog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "session_id": self.session_id,
            "status": self.status,
            "step": self.step,
            "name": self.name,
            "description": self.description,
            "style": self.style,
            "persona_id": self.persona_id,
            "photo_urls": self.photo_urls,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _load(self) -> dict | None:
        path = self._dialog_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
