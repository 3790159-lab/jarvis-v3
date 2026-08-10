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

from chatter.core.console import (
    cfg_text, console_text, contact_link, parse_allow_command, parse_config_command,
)
from chatter.core.escalation import esc_active_key
from chatter.notify.base import Action, Card, CardHandle, Notifier
from chatter.payments.callbacks import InvoiceAction, PaidAction, parse_callback
from chatter.payments.model import PaymentRecord, make_dedup_key
from chatter.payments.money import format_major

log = logging.getLogger("chatter.notify.control_bot")

_API_TIMEOUT = 30.0
_BUTTONS_PER_ROW = 2


@dataclass(frozen=True)
class CallbackResult:
    feedback_html: str      # новый текст карточки после тапа (мгновенная обратная связь)
    answer: str             # короткий тост answerCallbackQuery
    # Б3(а): editMessageText БЕЗ reply_markup удаляет инлайн-клавиатуру (Bot
    # API), поэтому любой тап делал карточку неуправляемой. Для НЕмутирующих
    # действий (навигация) кнопки надо вернуть — иначе промах по кнопке нечем
    # переиграть, а откатывать при этом нечего: Store не тронут.
    keep_buttons: bool = False


def _peer_of(contact_id: str) -> str:
    return contact_id.split(":", 1)[0]


def _close_funnel_as_bought(store, contact_id: str, *, now: float) -> None:
    """Оплата закрывает воронку сигналом «bought» — через тот же advance_funnel,
    что и остальной конвейер, чтобы переход попал в funnel_transitions и был
    виден в «Динамике»."""
    from chatter.core.escalation import advance_funnel
    advance_funnel(store, contact_id, stage_signal=None, escalated=False,
                   bought=True, now=now)



def _route_payment(money, *, store, now: float, language: str,
                   card_msg_id: int | None, event_token: str | None) -> CallbackResult:
    """Денежная ветка. CLIENT_SCREENS §3: единственный источник денежных метрик.
    Самоотчёт владельца, не факт из банка — так и подписывается в дашборде.

    Личность записи приходит ИЗ САМОГО СОБЫТИЯ: id карточки для TG-тапа,
    собственный токен для веб-панели. Сентинел `0` снят — он был стабилен ровно
    до второго бескарточного источника, и панель уже схлопывала свои оплаты
    одного контакта в одну строку. Вызыватель без личности получает ОТКАЗ:
    оплата, неотличимая от следующей, — это будущая потеря выручки, а не
    удобство (DEV-18)."""
    contact_id = money.contact_id
    if card_msg_id is not None:
        dedup_key = make_dedup_key("tap", card_msg_id)
    elif (event_token or "").strip():
        dedup_key = make_dedup_key("panel", event_token)
    else:
        log.warning("route_callback: оплата без личности события (%s)", contact_id)
        return CallbackResult(
            feedback_html=console_text("fb_paid_no_identity", language),
            answer=console_text("fb_paid_no_identity", language))

    store.get_or_create_contact(contact_id)
    store.record_payment(PaymentRecord(
        contact_id=contact_id, dedup_key=dedup_key, ts=now,
        confirmed_by="owner", amount=money.amount))
    store.add_event("payment", contact_id=contact_id,
                    detail="" if money.amount is None else f"{format_major(money.amount)} USD",
                    ts=now)
    _close_funnel_as_bought(store, contact_id, now=now)
    fb = (console_text("fb_paid", language) if money.amount is None
          else console_text("fb_paid_amount", language,
                            amount=format_major(money.amount), currency="USD"))
    return CallbackResult(feedback_html=fb, answer=fb)


def route_callback(data: str, *, store, now: float, language: str, snooze_seconds: float,
                   persona_name_for=None, card_msg_id: int | None = None,
                   event_token: str | None = None) -> CallbackResult:
    """Тап кнопки → действие над Store + текст обратной связи. ЧИСТАЯ: трогает
    только store. callback_data = "<action>:<contact_id>", где contact_id сам
    содержит двоеточие ("<peer>:<slug>"), поэтому режем ПО ПЕРВОМУ двоеточию.

    persona_name_for: contact_id -> имя персоны ЭТОГО диалога для текста
    фидбека. Имя было захардкожено «Аня» — на volska-раннере тап отвечал
    «✅ Залишено Ані», и владелец решил, что действие ушло чужому клиенту
    (дрил 2026-07-22). Без резолвера — нейтральное «бот», не чужое имя.

    card_msg_id: id сообщения-карточки, ПРИШЕДШИЙ С ТАПОМ
    (`callback_query.message.message_id`) — личность оплаты. Раньше ключ брался
    из runtime-флага `esc_active`, но его затирает эта же функция (Fix 2,
    закрытие карточки), то есть ключ уничтожался тем же вызовом, который его
    читал: второй тап падал на сентинел и плодил вторую оплату. Ключ обязан
    приходить из САМОГО события, а не из изменяемого состояния, которым владеет
    другая механика.

    event_token: личность события для вызывателей БЕЗ карточки (веб-панель).
    Прежде такие вызыватели получали сентинел `0`, и все панельные оплаты
    одного контакта схлопывались в одну строку. Теперь панель присылает свой
    токен, а вызыватель без всякой личности получает ОТКАЗ.

    Битый/неизвестный тап → без мутаций (DEV-18: не притворяемся, что сделали)."""
    # Денежные кнопки разбирает КОДЕК (chatter/payments/callbacks.py): у него
    # реестр версий формата, потому что кнопка, улетевшая в Telegram, тапабельна
    # через месяцы и legacy-форму придётся понимать всегда.
    money = parse_callback(data)
    if isinstance(money, PaidAction):
        return _route_payment(money, store=store, now=now, language=language,
                              card_msg_id=card_msg_id, event_token=event_token)
    if isinstance(money, InvoiceAction):
        # Ф0 карточек счёта ещё не шлёт; кнопка не должна выглядеть сработавшей.
        log.warning("route_callback: действие по счёту %r вне Ф0", money.kind)
        return CallbackResult(
            feedback_html=console_text("fb_unknown", language),
            answer=console_text("fb_unknown", language))

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

    persona = persona_name_for(contact_id) if persona_name_for else None
    persona = persona or "бот"
    if action is Action.RESUME:
        store.unmute(contact_id)
        store.add_event("resume", contact_id=contact_id, ts=now)
        fb = console_text("fb_resumed", language, persona=persona)
    elif action is Action.SNOOZE:
        store.get_or_create_contact(contact_id)
        store.mute(contact_id, source="command", until=now + snooze_seconds, now=now)
        fb = console_text("fb_snoozed", language)
    elif action is Action.STOP:
        store.set_runtime_flag("kill_switch", "1", ts=now)
        store.add_event("kill_on", ts=now)
        fb = console_text("fb_stopped", language, persona=persona)
    elif action is Action.KEEP:
        store.add_event("escalation_kept", contact_id=contact_id, ts=now)
        fb = console_text("fb_kept", language, persona=persona)
    elif action is Action.OPEN:
        link = contact_link(user_id=_peer_of(contact_id))
        fb = console_text("fb_open", language, link=link)
    else:  # pragma: no cover - Action исчерпан выше
        fb = console_text("fb_unknown", language)

    # Fix 2: решающее действие владельца ЗАКРЫВАЕТ активную карточку эскалации
    # → следующая эскалация этого контакта создаст новую, а не будет править
    # закрытую. OPEN — навигация (просто ссылка), карточку не закрывает.
    if action is not Action.OPEN:
        store.set_runtime_flag(esc_active_key(contact_id), "", ts=now)
    return CallbackResult(
        feedback_html=fb, answer=fb, keep_buttons=action is Action.OPEN)


def _resolve_allow_arg(arg: str, m: dict) -> str:
    """/allow как реплай на пересланное сообщение (Bot API кладёт весь
    объект реплая ПРЯМО в апдейт -- сети/доп. запроса не нужно): если
    владелец не набрал явную цель (@user/id), а сообщение -- реплай на
    forward с известным отправителем, подставляем его id. Не-форвард или
    форвард со скрытым отправителем (только forward_sender_name, без id) --
    цель остаётся пустой, handle_config_command честно ответит usage/not_found."""
    probe = parse_allow_command(arg)
    if probe.action == "list" or probe.target is not None:
        return arg
    peer_id = ((m.get("reply_to_message") or {}).get("forward_from") or {}).get("id")
    if peer_id is None:
        return arg
    prefix = "remove " if probe.action == "remove" else ""
    suffix = " confirm" if probe.confirmed else ""
    return f"{prefix}{peer_id}{suffix}"


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
        # Тап-feedback: правим ТЕКСТ и УБИРАЕМ кнопки (карточка «решена»).
        self._edit(handle, text_html, reply_markup={"inline_keyboard": []})

    def update_card(self, handle: CardHandle, card: Card) -> bool:
        # Дедуп (Fix 2): правим существующую карточку, СОХРАНЯЯ кнопки. Возвращает
        # РЕАЛЬНЫЙ успех правки — H2 полагается на это, чтобы решить, обещать ли
        # лиду контакт владельца (тихая правка ≠ владелец уведомлён).
        return self._edit(
            handle, card.text_html,
            reply_markup=self._keyboard(card) if card.buttons else None)

    def _edit(self, handle: CardHandle, text_html: str, *, reply_markup) -> bool:
        try:
            _, chat_id, msg_id = handle.ref.split(":")
            payload = {
                "chat_id": int(chat_id),
                "message_id": int(msg_id),
                "text": text_html,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            data = self._post("editMessageText", payload)
        except Exception:
            log.exception("ControlBotNotifier: editMessageText упал для %s", handle.ref)
            return False
        if not isinstance(data, dict) or not data.get("ok"):
            log.error("ControlBotNotifier: editMessageText вернул не-ok: %r", data)
            return False
        return True

    @property
    def has_buttons(self) -> bool:
        return True


# --- ControlBotPoller: изолированный long-poll цикл (async IO-оболочка) ------
_OWNER_FLAG = "control_owner_chat_id"   # runtime_flag с chat_id владельца (TOFU-bind)
_LONG_POLL_TIMEOUT = 25
# callback_data кнопки «выключить честность». Намеренно БЕЗ двоеточия: так она
# не попадает в contact-scoped формат route_callback и не может быть принята за
# действие над диалогом.
_HONESTY_WARN = "honesty_warn"
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
        owner_chat_id: int | None = None, pairing_code: str | None = None, notifier=None,
        http_get=None, http_post=None, clock=None, async_sleep=None,
        on_bind=None, config_handler=None, long_poll_timeout: int = _LONG_POLL_TIMEOUT,
        persona_name_for=None,
    ):
        self._store = store
        self._language = language
        # contact_id -> имя персоны диалога (для фидбека кнопок); см. route_callback
        self._persona_name_for = persona_name_for
        # config-арка: async (name, arg, language) -> текст-ответ (runner.handle_config_command)
        self._config_handler = config_handler
        self._snooze = snooze_seconds
        self._owner_chat_id = owner_chat_id
        self._pairing_code = pairing_code
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
        # Тумблер честности НЕ идёт через route_callback: тот contact-scoped
        # ("<action>:<contact_id>"), а режим честности глобальный — впихивать
        # его в Action означало бы сломать контракт маршрутизатора.
        #
        # И главное: тап НИЧЕГО не переключает, он только показывает
        # предупреждение. Владелец 2026-07-20 отверг команды пульта для этого
        # тумблера именно из-за «выключить честность одним тапом с телефона»;
        # кнопку вернули лишь потому, что она ведёт к НАБРАННОМУ
        # `/honesty free confirm`. Мутация живёт только там.
        if (cq.get("data") or "") == _HONESTY_WARN:
            await self._post("sendMessage", {
                "chat_id": cq.get("message", {}).get("chat", {}).get("id"),
                "text": cfg_text("cfg_honesty_confirm", self._language)})
            await self._post("answerCallbackQuery", {"callback_query_id": cq.get("id")})
            return
        msg = cq.get("message", {})
        result = route_callback(
            cq.get("data", ""), store=self._store, now=self._clock(),
            language=self._language, snooze_seconds=self._snooze,
            persona_name_for=self._persona_name_for,
            card_msg_id=msg.get("message_id"))
        edit = {
            "chat_id": msg.get("chat", {}).get("id"),
            "message_id": msg.get("message_id"),
            "text": result.feedback_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # Б3(а): вернуть клавиатуру на место для немутирующего тапа. Опускать
        # reply_markup — значит стереть кнопки, и владелец теряет управление
        # карточкой из-за промаха по чисто навигационной кнопке.
        markup = msg.get("reply_markup") if result.keep_buttons else None
        if markup:
            edit["reply_markup"] = markup
        await self._post("editMessageText", edit)
        await self._post("answerCallbackQuery", {
            "callback_query_id": cq.get("id"), "text": result.answer})

    async def _reply(self, chat_id: int, key: str) -> None:
        await self._post("sendMessage", {
            "chat_id": chat_id, "text": console_text(key, self._language)})

    async def _on_message(self, m: dict) -> None:
        parts = (m.get("text") or "").strip().split()
        if not parts:
            return
        cmd = parts[0]
        chat_id = m.get("chat", {}).get("id")
        if chat_id is None:
            return
        if cmd == "/unbind":
            await self._on_unbind(chat_id)
        elif cmd == "/start":
            await self._on_start(chat_id, parts[1] if len(parts) > 1 else None)
        else:
            await self._maybe_config_command(chat_id, m)

    async def _maybe_config_command(self, chat_id: int, m: dict) -> None:
        """config-арка: /config /reload /knowledge /rollback /allow — ТОЛЬКО
        владельцу. Чужой id вообще не видит config-поверхность."""
        cc = parse_config_command(m.get("text") or "")
        if cc is None or self._config_handler is None:
            return
        if chat_id != self._effective_owner():
            log.warning("control-bot: config-команда от НЕ-владельца %s — отказ", chat_id)
            return
        name, arg = cc
        if name == "allow":
            # /allow как реплай на пересланное сообщение лида: владелец не
            # обязан набирать id руками -- берём его из forward_from того
            # сообщения, на которое отвечает. Только для /allow: остальные
            # config-команды реплай не используют вовсе.
            arg = _resolve_allow_arg(arg, m)
        try:
            reply = await self._config_handler(name, arg, language=self._language)
        except Exception:
            # DEV-18: команда упала — владелец ОБЯЗАН узнать (ответ + лог), а не
            # остаться без пульта в тишине. Раньше молчали → пульт «оглох».
            log.exception("config-команда %s упала", name)
            reply = console_text("config_command_failed", self._language)
        payload = {
            "chat_id": chat_id, "text": reply, "parse_mode": "HTML",
            "disable_web_page_preview": True}
        # Кнопку вешаем только на СТАТУС честности (/honesty без аргумента).
        # Логика осталась здесь, а не в handle_config_command, чтобы не менять
        # его контракт (str) — им пользуется ещё и путь Saved Messages, где
        # инлайн-кнопок нет в принципе.
        if name == "honesty" and not arg.strip():
            payload["reply_markup"] = {"inline_keyboard": [[{
                "text": cfg_text("btn_honesty_off", self._language),
                "callback_data": _HONESTY_WARN}]]}
        try:
            await self._post("sendMessage", payload)
        except Exception:
            # Даже отправка ответа не должна ронять цикл (её ловит и _handle_update,
            # но подстрахуемся здесь ради ясности лога).
            log.exception("control-bot: не смог отправить ответ на config-команду %s", name)

    async def _on_start(self, chat_id: int, arg: str | None) -> None:
        """Привязка владельца — БЕЗ TOFU (спека 3B-sec): пуб­личный юзернейм бота
        означает, что «первый нашедший» не должен становиться владельцем.

        Уже привязанный пульт не перебивается вторым /start (только /unbind от
        владельца). Иначе привязка проходит РОВНО одним из настроенных путей:
        явный owner_chat_id (жёсткий id-гейт) ИЛИ одноразовый pairing_code
        (/start <код>). Ни один не задан → любой /start отклоняется.

        Онбординг клиента = ОДНА ссылка-deep-link `t.me/<bot>?start=<код>`: тап
        по ней сам шлёт `/start <код>` (`arg`), клиенту ничего вводить не надо.
        Код одноразовый — «сгорает» самим фактом привязки (дальше срабатывает
        ветка bound выше). Charset payload'а Telegram ограничивает [A-Za-z0-9_-],
        ≤64 симв. — валидируется при загрузке settings (loader)."""
        bound = self._store.get_runtime_flag(_OWNER_FLAG)
        if bound:   # truthy: "" (после /unbind) считается непривязанным
            await self._reply(
                chat_id, "bind_welcome_back" if int(bound) == chat_id else "bind_rejected")
            return
        if self._owner_chat_id is not None:
            if chat_id == self._owner_chat_id:
                self._bind(chat_id)
                await self._reply(chat_id, "bind_welcome")
            else:
                log.warning("control-bot: /start от НЕ-владельца %s (ожидался %s) — отказ",
                            chat_id, self._owner_chat_id)
                await self._reply(chat_id, "bind_not_authorized")
            return
        if self._pairing_code is not None:
            if arg == self._pairing_code:
                # Код «сгорает» самим фактом привязки: далее срабатывает ветка
                # bound is not None выше, второй раз тот же код не привяжет.
                self._bind(chat_id)
                await self._reply(chat_id, "bind_welcome")
            else:
                log.warning("control-bot: неверный/пустой pairing-код от %s — отказ", chat_id)
                await self._reply(chat_id, "bind_not_authorized")
            return
        # Ни id, ни кода: TOFU-дыра закрыта — не привязываем никого.
        log.warning("control-bot: /start от %s, но ни owner_chat_id, ни pairing_code не заданы — отказ", chat_id)
        await self._reply(chat_id, "bind_not_authorized")

    async def _on_unbind(self, chat_id: int) -> None:
        """Явный разрыв привязки — ТОЛЬКО текущим владельцем (спека 3B-sec:
        «уже привязанный владелец не перебивается... только явным разрывом»)."""
        bound = self._store.get_runtime_flag(_OWNER_FLAG)
        if bound and int(bound) == chat_id:
            self._store.set_runtime_flag(_OWNER_FLAG, "", ts=self._clock())
            await self._reply(chat_id, "unbind_ack")
        else:
            log.warning("control-bot: /unbind от %s, но он не владелец — игнор", chat_id)

    def _bind(self, chat_id: int) -> None:
        self._store.set_runtime_flag(_OWNER_FLAG, str(chat_id), ts=self._clock())
        if self._on_bind:
            self._on_bind(chat_id)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("control-bot poll_once упал; продолжаю цикл")
                await self._sleep(_POLL_ERROR_BACKOFF)
