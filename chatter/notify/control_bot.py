"""Контрол-бот (арка 3B): ОТДЕЛЬНЫЙ BotFather-бот со своим токеном.

Владелец рулит со своего телефона ТАПАМИ по инлайн-кнопкам — не переключая
аккаунт и не печатая команды (эргономический принцип арки). Токен отдельный →
никакого 409 Conflict с основным Jarvis-ботом.

Три части:
- `route_callback` — ЧИСТАЯ маршрутизация тапа в действие над Store (юнит-тест
  без сети);
- `ControlBotNotifier` — отправка карточек с кнопками через Bot API (sync httpx);
- `ControlBotPoller` — изолированный long-poll цикл getUpdates (async IO-оболочка).

Fail-safe (DEV-18): битый/неизвестный callback → без мутаций; ошибки сети в
notifier/poller не бросаются наружу.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from chatter.core.console import console_text, contact_link
from chatter.notify.base import Action, Card, CardHandle, Notifier

log = logging.getLogger("chatter.notify.control_bot")

_API_TIMEOUT = 30.0
_BUTTONS_PER_ROW = 2


@dataclass(frozen=True)
class CallbackResult:
    feedback_html: str      # новый текст карточки после тапа (мгновенная обратная связь)
    answer: str             # короткий тост answerCallbackQuery


def _peer_of(contact_id: str) -> str:
    return contact_id.split(":", 1)[0]


def route_callback(data: str, *, store, now: float, language: str, snooze_seconds: float) -> CallbackResult:
    """Тап кнопки → действие над Store + текст обратной связи. ЧИСТАЯ: трогает
    только store. callback_data = "<action>:<contact_id>", где contact_id сам
    содержит двоеточие ("<peer>:<slug>"), поэтому режем ПО ПЕРВОМУ двоеточию.

    Битый/неизвестный тап → без мутаций (DEV-18: не притворяемся, что сделали)."""
    action_raw, sep, contact_id = (data or "").partition(":")
    if not sep or not contact_id:
        return CallbackResult(
            feedback_html=console_text("fb_unknown", language),
            answer=console_text("fb_unknown", language))
    try:
        action = Action(action_raw)
    except ValueError:
        log.warning("route_callback: неизвестное действие %r", action_raw)
        return CallbackResult(
            feedback_html=console_text("fb_unknown", language),
            answer=console_text("fb_unknown", language))

    if action is Action.RESUME:
        store.unmute(contact_id)
        store.add_event("resume", contact_id=contact_id, ts=now)
        fb = console_text("fb_resumed", language)
    elif action is Action.SNOOZE:
        store.get_or_create_contact(contact_id)
        store.mute(contact_id, source="command", until=now + snooze_seconds, now=now)
        fb = console_text("fb_snoozed", language)
    elif action is Action.STOP:
        store.set_runtime_flag("kill_switch", "1", ts=now)
        store.add_event("kill_on", ts=now)
        fb = console_text("fb_stopped", language)
    elif action is Action.KEEP:
        store.add_event("escalation_kept", contact_id=contact_id, ts=now)
        fb = console_text("fb_kept", language)
    elif action is Action.OPEN:
        link = contact_link(user_id=_peer_of(contact_id))
        fb = console_text("fb_open", language, link=link)
    else:  # pragma: no cover - Action исчерпан выше
        fb = console_text("fb_unknown", language)
    return CallbackResult(feedback_html=fb, answer=fb)


def _default_http_post(token: str):
    """Дефолтный синхронный вызов Bot API через httpx. Возвращает функцию
    (method, payload) -> dict. Sync — вызывается из worker-потока/to_thread,
    поэтому не связан с event loop Telethon (отдельный сервис, отдельный токен)."""
    import httpx

    base = f"https://api.telegram.org/bot{token}"

    def _post(method: str, payload: dict) -> dict:
        resp = httpx.post(f"{base}/{method}", json=payload, timeout=_API_TIMEOUT)
        return resp.json()

    return _post


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


class ControlBotNotifier(Notifier):
    """Отправляет карточки С ИНЛАЙН-КНОПКАМИ через Bot API (sync httpx).
    Никогда не бросает (DEV-18): на сбое логирует и возвращает None/no-op."""

    def __init__(self, token: str, chat_id, *, http_post=None):
        # chat_id: int ЛИБО callable() -> int|None. Callable нужен, когда
        # владелец не задан в настройках и привязывается TOFU по первому /start
        # (контрол-бот-поллер пишет chat_id в runtime_flags, notifier читает).
        self._token = token
        self._chat_id = chat_id
        self._post = http_post or _default_http_post(token)

    def _resolve_chat_id(self):
        return self._chat_id() if callable(self._chat_id) else self._chat_id

    def _keyboard(self, card: Card) -> dict:
        buttons = [
            {"text": b.label, "callback_data": f"{b.action.value}:{card.contact_id}"}
            for b in card.buttons
        ]
        return {"inline_keyboard": _chunk(buttons, _BUTTONS_PER_ROW)}

    def notify(self, card: Card) -> CardHandle | None:
        chat_id = self._resolve_chat_id()
        if chat_id is None:
            # Владелец ещё не нажал /start — доставлять некуда. Не ошибка, но и
            # не молчание: логируем, чтобы это было видно, если /start забыли.
            log.warning("ControlBotNotifier: владелец не привязан (нет /start) — карточка не отправлена")
            return None
        payload = {
            "chat_id": chat_id,
            "text": card.text_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if card.buttons:
            payload["reply_markup"] = self._keyboard(card)
        try:
            data = self._post("sendMessage", payload)
        except Exception:
            log.exception("ControlBotNotifier: sendMessage упал")
            return None
        if not isinstance(data, dict) or not data.get("ok"):
            log.error("ControlBotNotifier: Bot API вернул не-ok: %r", data)
            return None
        msg_id = data.get("result", {}).get("message_id")
        if msg_id is None:
            return None
        return CardHandle(ref=f"bot:{chat_id}:{msg_id}")

    def edit(self, handle: CardHandle, text_html: str) -> None:
        try:
            _, chat_id, msg_id = handle.ref.split(":")
            payload = {
                "chat_id": int(chat_id),
                "message_id": int(msg_id),
                "text": text_html,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            self._post("editMessageText", payload)
        except Exception:
            log.exception("ControlBotNotifier: editMessageText упал для %s", handle.ref)

    @property
    def has_buttons(self) -> bool:
        return True


# --- ControlBotPoller: изолированный long-poll цикл (async IO-оболочка) ------
_OWNER_FLAG = "control_owner_chat_id"   # runtime_flag с chat_id владельца (TOFU-bind)
_LONG_POLL_TIMEOUT = 25
_POLL_ERROR_BACKOFF = 3.0


def _default_async_api(token: str):
    """(http_get, http_post) поверх httpx.AsyncClient. Свой токен, свой сокет —
    ноль связи с getUpdates основного Jarvis-бота (никакого 409 Conflict)."""
    import httpx

    base = f"https://api.telegram.org/bot{token}"

    async def _call(method: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=_LONG_POLL_TIMEOUT + 10) as client:
            resp = await client.post(f"{base}/{method}", json=payload)
            return resp.json()

    return _call, _call


class ControlBotPoller:
    """Крутит getUpdates у ОТДЕЛЬНОГО контрол-бота и превращает тапы кнопок в
    действия над Store через чистый `route_callback`. Живёт на том же event
    loop, что Telethon, но со своим токеном.

    Только владелец (owner_chat_id из настроек ИЛИ TOFU-bind на первый /start,
    сохранённый в runtime_flags) может жать кнопки; чужой тап отклоняется без
    мутаций. Никогда не умирает: ошибка в цикле проглатывается и логируется
    (DEV-18) — иначе пульт владельца молча отвалится."""

    def __init__(
        self, token: str, *, store, language: str, snooze_seconds: float,
        owner_chat_id: int | None = None, notifier=None,
        http_get=None, http_post=None, clock=None, async_sleep=None,
        on_bind=None, long_poll_timeout: int = _LONG_POLL_TIMEOUT,
    ):
        self._store = store
        self._language = language
        self._snooze = snooze_seconds
        self._owner_chat_id = owner_chat_id
        self._on_bind = on_bind
        self._timeout = long_poll_timeout
        import time as _time
        self._clock = clock or _time.time
        self._sleep = async_sleep or asyncio.sleep
        if http_get is None or http_post is None:
            get, post = _default_async_api(token)
            http_get = http_get or get
            http_post = http_post or post
        self._get = http_get
        self._post = http_post
        self._offset = 0

    def _effective_owner(self) -> int | None:
        if self._owner_chat_id is not None:
            return self._owner_chat_id
        flag = self._store.get_runtime_flag(_OWNER_FLAG)
        return int(flag) if flag else None

    async def poll_once(self) -> None:
        resp = await self._get("getUpdates", {"offset": self._offset, "timeout": self._timeout})
        for u in resp.get("result", []):
            self._offset = max(self._offset, int(u["update_id"]) + 1)
            try:
                await self._handle_update(u)
            except Exception:
                # Один битый апдейт не должен ронять цикл или блокировать
                # продвижение offset (оно уже выше). DEV-18: логируем.
                log.exception("control-bot: сбой на апдейте %s", u.get("update_id"))

    async def _handle_update(self, u: dict) -> None:
        if "callback_query" in u:
            await self._on_callback(u["callback_query"])
        elif "message" in u:
            await self._on_message(u["message"])

    async def _on_callback(self, cq: dict) -> None:
        from_id = cq.get("from", {}).get("id")
        if from_id != self._effective_owner():
            await self._post("answerCallbackQuery", {
                "callback_query_id": cq.get("id"),
                "text": console_text("fb_not_owner", self._language),
            })
            return
        result = route_callback(
            cq.get("data", ""), store=self._store, now=self._clock(),
            language=self._language, snooze_seconds=self._snooze)
        msg = cq.get("message", {})
        await self._post("editMessageText", {
            "chat_id": msg.get("chat", {}).get("id"),
            "message_id": msg.get("message_id"),
            "text": result.feedback_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        await self._post("answerCallbackQuery", {
            "callback_query_id": cq.get("id"), "text": result.answer})

    async def _on_message(self, m: dict) -> None:
        text = (m.get("text") or "").strip()
        if not text.split() or text.split()[0] != "/start":
            return
        chat_id = m.get("chat", {}).get("id")
        if chat_id is None:
            return
        # TOFU: если владелец в настройках не задан — привязываемся к первому
        # /start и запоминаем навсегда (клиент только жмёт /start).
        if self._owner_chat_id is None and self._store.get_runtime_flag(_OWNER_FLAG) is None:
            self._store.set_runtime_flag(_OWNER_FLAG, str(chat_id), ts=self._clock())
            if self._on_bind:
                self._on_bind(chat_id)
        await self._post("sendMessage", {
            "chat_id": chat_id,
            "text": "✅ Пульт подключён. Карточки пауз и эскалаций будут приходить сюда.",
        })

    async def run_forever(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("control-bot poll_once упал; продолжаю цикл")
                await self._sleep(_POLL_ERROR_BACKOFF)
