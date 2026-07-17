"""SavedMessagesNotifier — фоллбек арки 3A (арка 3B).

Кладёт карточку в Saved Messages юзербота (`client.send_message("me", ...)`),
без инлайн-кнопок (юзербот их не умеет) — вместо кнопок снизу приклеиваются
копипаст-подсказки (`card.reply_hints`), ровно как в арке 3A. Работает всегда,
когда контрол-бот не настроен: инвариант «без токена = поведение как 3A».

Маршалинг на event loop — тот же паттерн, что `send_alert`: из worker-потока
(или `asyncio.to_thread`) через `run_coroutine_threadsafe(...).result()`. НЕ
вызывать напрямую с самого loop (заблокирует его). Никогда не бросает (DEV-18):
на сбое логирует и возвращает None / no-op.
"""
from __future__ import annotations

import asyncio
import logging

from chatter.notify.base import Card, CardHandle, Notifier

log = logging.getLogger("chatter.notify.saved_messages")


class SavedMessagesNotifier(Notifier):
    def __init__(self, client, loop, *, run_coro=None, timeout: float = 30.0):
        self._client = client
        self._loop = loop
        self._timeout = timeout
        # Инъекция маршалера ради тестов без реального loop/сети.
        self._run = run_coro or self._marshal

    def _marshal(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=self._timeout)

    def notify(self, card: Card) -> CardHandle | None:
        body = card.text_html
        if card.reply_hints:
            body = body + "\n\n" + "\n".join(card.reply_hints)
        try:
            msg = self._run(self._client.send_message("me", body, parse_mode="html"))
        except Exception:
            log.exception("SavedMessagesNotifier: не смог отправить карточку в Saved Messages")
            return None
        msg_id = getattr(msg, "id", None)
        if msg_id is None:
            return None
        return CardHandle(ref=f"me:{msg_id}")

    def edit(self, handle: CardHandle, text_html: str) -> None:
        try:
            msg_id = int(handle.ref.split(":", 1)[1])
            self._run(self._client.edit_message("me", msg_id, text_html, parse_mode="html"))
        except Exception:
            log.exception("SavedMessagesNotifier: не смог отредактировать карточку %s", handle.ref)

    def update_card(self, handle: CardHandle, card: Card) -> None:
        # У Saved Messages кнопок нет — обновляем текст + копипаст-подсказки,
        # ровно как при первичной отправке (дедуп Fix 2).
        body = card.text_html
        if card.reply_hints:
            body = body + "\n\n" + "\n".join(card.reply_hints)
        self.edit(handle, body)

    @property
    def has_buttons(self) -> bool:
        return False
