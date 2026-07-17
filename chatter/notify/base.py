"""Notifier — интерфейс «сообщи владельцу и предложи действия» (арка 3B).

Две реализации: `SavedMessagesNotifier` (арка 3A: текстовые карточки в Saved
Messages, БЕЗ кнопок — юзербот не умеет инлайн-клавиатуры) и
`ControlBotNotifier` (отдельный BotFather-бот со СВОИМ токеном → настоящие
инлайн-кнопки). `FakeNotifier` — для тестов.

Интерфейс СИНХРОННЫЙ (как `Transport`): вызовы из worker-потока (process_batch)
идут напрямую; вызовы с event-loop оборачивают в `asyncio.to_thread`.

Этот модуль НЕ импортирует `console` — зависимость односторонняя (console
импортирует Button/Action отсюда), чтобы не было цикла.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    """Действие кнопки. Значение — стабильный токен для callback_data
    (`"<action>:<contact_id>"`)."""
    RESUME = "resume"   # ▶️ Вернуть Аню
    SNOOZE = "snooze"   # ⏸ Ещё 1ч
    OPEN = "open"       # 💬 Открыть диалог
    STOP = "stop"       # 🔴 Стоп везде
    KEEP = "keep"       # ✅ Оставить Ане


@dataclass(frozen=True)
class Button:
    action: Action
    label: str          # уже локализованная подпись


@dataclass(frozen=True)
class Card:
    kind: str                    # "pause" | "escalation"
    contact_id: str
    text_html: str               # полностью отрендеренное экранированное тело
    buttons: list[Button]
    reply_hints: list[str]       # копипаст-строки фоллбека (Saved Messages)
    link: str | None = None      # t.me/... для кнопки OPEN


@dataclass(frozen=True)
class CardHandle:
    ref: str                     # непрозрачный: "me:<msg_id>" или "bot:<chat_id>:<msg_id>"


class Notifier(ABC):
    @abstractmethod
    def notify(self, card: Card) -> CardHandle | None:
        """Доставить карточку. None = доставка не удалась (никогда не бросает)."""

    @abstractmethod
    def edit(self, handle: CardHandle, text_html: str) -> None:
        """Мгновенная обратная связь на тап (правка сообщения карточки)."""

    @property
    @abstractmethod
    def has_buttons(self) -> bool:
        """True только у контрол-бота — тогда карточки идут с инлайн-кнопками."""


class FakeNotifier(Notifier):
    """Тестовый Notifier: пишет вызовы в .cards / .edits."""

    def __init__(self, *, has_buttons: bool = True):
        self.cards: list[Card] = []
        self.edits: list[tuple[CardHandle, str]] = []
        self._has_buttons = has_buttons

    def notify(self, card: Card) -> CardHandle | None:
        self.cards.append(card)
        return CardHandle(ref=f"fake:{len(self.cards)}")

    def edit(self, handle: CardHandle, text_html: str) -> None:
        self.edits.append((handle, text_html))

    @property
    def has_buttons(self) -> bool:
        return self._has_buttons
