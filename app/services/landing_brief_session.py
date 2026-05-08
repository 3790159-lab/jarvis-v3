# -*- coding: utf-8 -*-
"""Landing Brief 2.0 — multi-step session state machine for gathering landing page briefs."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent.parent
_BRIEFS_DIR = _ROOT / "state" / "landing_briefs"

STEPS = [
    ("business_name", "Шаг 1/8: Название бизнеса (1-3 слова)?"),
    ("target_audience", "Шаг 2/8: Целевая аудитория (возраст, интересы)?"),
    ("main_product", "Шаг 3/8: Главный продукт или услуга?"),
    ("key_advantages", "Шаг 4/8: 3 ключевых преимущества (через запятую)?"),
    ("cta", "Шаг 5/8: Основной призыв к действию (купить/записаться/узнать/заказать)?"),
    ("color_scheme", "Шаг 6/8: Цветовая схема (тёплая/холодная/нейтральная/luxury)?"),
    ("style", "Шаг 7/8: Стиль (современный/luxury/игривый/деловой/health)?"),
    ("contacts", "Шаг 8/8: Контакты (email, телефон, сайт или 'нет')?"),
]

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"


def _briefs_dir(user_id: str) -> Path:
    d = _BRIEFS_DIR / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_path(user_id: str, session_id: str) -> Path:
    return _briefs_dir(user_id) / f"{session_id}.json"


def _save_session(session: Dict[str, Any]) -> None:
    from app.services.block_l_common import save_json_safe
    p = _session_path(session["user_id"], session["session_id"])
    save_json_safe(p, session)


def _load_session(user_id: str, session_id: str) -> Optional[Dict[str, Any]]:
    from app.services.block_l_common import load_json_safe
    return load_json_safe(_session_path(user_id, session_id))


class LandingBriefSession:
    """State machine for collecting landing page brief over 8 conversation steps."""

    def __init__(self, user_id: str, chat_id: str, session_id: Optional[str] = None):
        self.user_id = str(user_id)
        self.chat_id = str(chat_id)
        if session_id:
            self.session_id = session_id
            data = _load_session(self.user_id, self.session_id) or {}
            self.current_step = data.get("current_step", 0)
            self.data: Dict[str, Any] = data.get("data", {})
            self.status = data.get("status", STATUS_ACTIVE)
        else:
            self.session_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")[:19]
            self.current_step = 0
            self.data = {}
            self.status = STATUS_ACTIVE
            self._persist()

    def _persist(self) -> None:
        _save_session({
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "session_id": self.session_id,
            "current_step": self.current_step,
            "data": self.data,
            "status": self.status,
            "created_at": datetime.utcnow().isoformat(),
        })

    def get_current_question(self) -> str:
        if self.current_step >= len(STEPS):
            return ""
        return STEPS[self.current_step][1]

    def advance(self, answer: str) -> Tuple[bool, str]:
        """
        Process the user's answer and advance to next step.
        Returns (completed, next_question_or_empty).
        completed=True means all 8 steps done.
        """
        if self.status != STATUS_ACTIVE:
            return True, ""

        if self.current_step < len(STEPS):
            key = STEPS[self.current_step][0]
            self.data[key] = answer.strip()
            self.current_step += 1

        if self.current_step >= len(STEPS):
            self.status = STATUS_COMPLETED
            self._persist()
            return True, ""

        next_q = STEPS[self.current_step][1]
        self._persist()
        return False, next_q

    def cancel(self) -> None:
        self.status = STATUS_CANCELLED
        self._persist()

    def is_complete(self) -> bool:
        return self.status == STATUS_COMPLETED

    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    def get_brief_data(self) -> Dict[str, Any]:
        return dict(self.data)

    @classmethod
    def find_active(cls, user_id: str) -> Optional["LandingBriefSession"]:
        """Find the most recent active session for this user."""
        d = _BRIEFS_DIR / str(user_id)
        if not d.exists():
            return None
        from app.services.block_l_common import load_json_safe
        best = None
        for f in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            data = load_json_safe(f)
            if data and data.get("status") == STATUS_ACTIVE:
                sess = cls(user_id, data.get("chat_id", str(user_id)),
                           session_id=data["session_id"])
                return sess
        return None
