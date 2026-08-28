from __future__ import annotations

import asyncio
import inspect

import pytest

from chatter.notify.control_bot import ControlBotPoller
from chatter.storage.db import Store
from chatter.telethon_run import TelethonRunner


class FakeApi:
    """Захватывает POST-вызовы; отдаёт скриптованные getUpdates-батчи по очереди."""

    def __init__(self, update_batches):
        self._batches = list(update_batches)
        self.posts = []

    async def get(self, method, payload):
        assert method == "getUpdates"
        if self._batches:
            return {"ok": True, "result": self._batches.pop(0)}
        return {"ok": True, "result": []}

    async def post(self, method, payload):
        self.posts.append((method, payload))
        return {"ok": True, "result": {}}

    def methods(self):
        return [m for m, _ in self.posts]

    def payload_for(self, method):
        return next(p for m, p in self.posts if m == method)


def _poller(api, *, store, owner_chat_id=237616472, pairing_code=None, on_bind=None):
    return ControlBotPoller(
        "TOKEN", store=store, language="ru", snooze_seconds=3600,
        owner_chat_id=owner_chat_id, pairing_code=pairing_code,
        http_get=api.get, http_post=api.post,
        clock=lambda: 1000.0, on_bind=on_bind,
    )


def _callback_update(uid, *, data, from_id, chat_id=237616472, message_id=555):
    return {
        "update_id": uid,
        "callback_query": {
            "id": "cbq1", "data": data, "from": {"id": from_id},
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
        },
    }


def test_owner_callback_routes_edits_answers_and_advances_offset():
    store = Store(":memory:")
    store.get_or_create_contact("telegram:42:demo")
    store.mute("telegram:42:demo", source="human_takeover", now=1.0)
    api = FakeApi([[_callback_update(10, data="resume:42:demo", from_id=237616472)]])
    poller = _poller(api, store=store)

    asyncio.run(poller.poll_once())

    # действие исполнено
    assert store.get_or_create_contact("telegram:42:demo")["paused"] == 0
    # мгновенная обратная связь: карточка отредактирована + тост
    assert "editMessageText" in api.methods()
    assert "answerCallbackQuery" in api.methods()
    edit = api.payload_for("editMessageText")
    assert edit["message_id"] == 555 and edit["chat_id"] == 237616472
    assert "верну" in edit["text"].casefold()
    # offset сдвинут за апдейт
    assert poller._offset == 11


def test_poller_passes_the_card_message_id_into_the_payment_key():
    """ШОВ: личность оплаты приезжает из `callback_query.message.message_id`.

    Раньше `route_callback` брал ключ из runtime-флага `esc_active`, который
    сам же и затирал, — повторный тап плодил вторую оплату. Ключ обязан
    приходить из события; этот тест держит проводку поллера, а не только
    чистую функцию."""
    store = Store(":memory:")
    store.get_or_create_contact("telegram:42:demo")
    api = FakeApi([[_callback_update(10, data="paidamt:900:42:demo",
                                     from_id=237616472, message_id=777)]])

    asyncio.run(_poller(api, store=store).poll_once())

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1
    assert rows[0]["dedup_key"] == "tap:777", (
        f"ключ оплаты не из message_id: {rows[0]}")


def test_repeated_delivery_of_the_same_tap_does_not_double_the_payment():
    """Telegram переспрашивает неподтверждённые апдейты. Повторная доставка
    ОДНОГО тапа обязана дать одну оплату, а не две."""
    store = Store(":memory:")
    store.get_or_create_contact("telegram:42:demo")
    store.set_runtime_flag("esc_active:42:demo", "bot:1:777", ts=1.0)
    tap = dict(data="paidamt:900:42:demo", from_id=237616472, message_id=777)
    api = FakeApi([[_callback_update(10, **tap)], [_callback_update(11, **tap)]])
    poller = _poller(api, store=store)

    asyncio.run(poller.poll_once())
    asyncio.run(poller.poll_once())

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1, f"повторная доставка удвоила оплату: {rows}"
    assert sum(r["amount_minor"] or 0 for r in rows) == 90000
    # Схлопнулось по ИДЕНТИЧНОСТИ КАРТОЧКИ, а не случайно по сентинелу: без
    # этой строки тест переживает снятие проводки message_id (проверено
    # мутацией) и перестаёт что-либо доказывать.
    assert rows[0]["dedup_key"] == "tap:777"


def test_non_owner_callback_is_rejected_without_mutation():
    store = Store(":memory:")
    store.get_or_create_contact("telegram:42:demo")
    store.mute("telegram:42:demo", source="human_takeover", now=1.0)
    api = FakeApi([[_callback_update(10, data="resume:42:demo", from_id=999999)]])
    poller = _poller(api, store=store)

    asyncio.run(poller.poll_once())

    assert store.get_or_create_contact("telegram:42:demo")["paused"] == 1   # НЕ размучен
    assert "answerCallbackQuery" in api.methods()
    assert "editMessageText" not in api.methods()                  # чужому карточку не правим


def _start(uid, *, chat_id, arg=None):
    text = "/start" if arg is None else f"/start {arg}"
    return {"update_id": uid, "message": {"message_id": uid, "text": text, "chat": {"id": chat_id}}}


OWNER = 237616472
_OWNER_FLAG = "control_owner_chat_id"


def test_start_with_no_owner_and_no_code_refuses_to_bind():
    # ДЫРА TOFU ЗАКРЫТА: без owner_chat_id и без pairing_code любой /start
    # ОТКЛОНЯЕТСЯ (иначе первый нашедший бота становится владельцем пульта).
    store = Store(":memory:")
    bound = []
    api = FakeApi([[_start(5, chat_id=555000)]])
    poller = _poller(api, store=store, owner_chat_id=None, on_bind=bound.append)

    asyncio.run(poller.poll_once())

    assert store.get_runtime_flag(_OWNER_FLAG) is None      # НИКТО не привязан
    assert bound == []
    assert poller._effective_owner() is None


def test_start_binds_only_the_configured_owner_id():
    store = Store(":memory:")
    bound = []
    api = FakeApi([[_start(1, chat_id=OWNER)]])
    poller = _poller(api, store=store, owner_chat_id=OWNER, on_bind=bound.append)
    asyncio.run(poller.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) == str(OWNER)
    assert bound == [OWNER]


def test_start_from_wrong_id_is_rejected():
    store = Store(":memory:")
    bound = []
    api = FakeApi([[_start(1, chat_id=999999)]])   # чужой
    poller = _poller(api, store=store, owner_chat_id=OWNER, on_bind=bound.append)
    asyncio.run(poller.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) is None       # не привязан
    assert bound == []
    # владельцу-нарушителю ответили отказом
    assert any("Доступ" in p.get("text", "") for _, p in api.posts)


def test_pairing_code_binds_whoever_has_the_code():
    store = Store(":memory:")
    bound = []
    api = FakeApi([[_start(1, chat_id=42, arg="secret-xyz")]])
    poller = _poller(api, store=store, owner_chat_id=None, pairing_code="secret-xyz", on_bind=bound.append)
    asyncio.run(poller.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) == "42"
    assert bound == [42]


def test_wrong_pairing_code_is_rejected():
    store = Store(":memory:")
    api = FakeApi([[_start(1, chat_id=42, arg="nope")]])
    poller = _poller(api, store=store, owner_chat_id=None, pairing_code="secret-xyz")
    asyncio.run(poller.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) is None


def test_pairing_code_missing_arg_is_rejected():
    store = Store(":memory:")
    api = FakeApi([[_start(1, chat_id=42)]])   # /start без кода
    poller = _poller(api, store=store, owner_chat_id=None, pairing_code="secret-xyz")
    asyncio.run(poller.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) is None


def test_code_burns_after_bind_second_holder_rejected():
    store = Store(":memory:")
    # первый с кодом привязался
    api1 = FakeApi([[_start(1, chat_id=42, arg="secret-xyz")]])
    p1 = _poller(api1, store=store, owner_chat_id=None, pairing_code="secret-xyz")
    asyncio.run(p1.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) == "42"
    # второй с ТЕМ ЖЕ кодом — уже привязано, отказ, владелец не меняется
    api2 = FakeApi([[_start(2, chat_id=99, arg="secret-xyz")]])
    p2 = _poller(api2, store=store, owner_chat_id=None, pairing_code="secret-xyz")
    asyncio.run(p2.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) == "42"       # не перебит
    assert any("привязан" in p.get("text", "") for _, p in api2.posts)


def test_already_bound_owner_not_overridden_by_second_start():
    store = Store(":memory:")
    store.set_runtime_flag(_OWNER_FLAG, str(OWNER), ts=0.0)
    api = FakeApi([[_start(1, chat_id=999999)]])   # чужой пробует перехватить
    poller = _poller(api, store=store, owner_chat_id=None)
    asyncio.run(poller.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) == str(OWNER)


def test_binding_survives_restart():
    store = Store(":memory:")
    store.set_runtime_flag(_OWNER_FLAG, "42", ts=0.0)
    # свежий поллер (рестарт), owner_chat_id не задан — читает привязку из SQLite
    poller = _poller(FakeApi([]), store=store, owner_chat_id=None)
    assert poller._effective_owner() == 42


def test_unbind_from_owner_releases_then_rebind_possible():
    store = Store(":memory:")
    store.set_runtime_flag(_OWNER_FLAG, str(OWNER), ts=0.0)
    # владелец отвязывает
    api = FakeApi([[{"update_id": 1, "message": {
        "message_id": 1, "text": "/unbind", "chat": {"id": OWNER}}}]])
    poller = _poller(api, store=store, owner_chat_id=None)
    asyncio.run(poller.poll_once())
    assert not store.get_runtime_flag(_OWNER_FLAG)           # пусто = отвязан
    assert poller._effective_owner() is None
    # теперь новый код может привязать заново
    api2 = FakeApi([[_start(2, chat_id=77, arg="fresh-code")]])
    p2 = _poller(api2, store=store, owner_chat_id=None, pairing_code="fresh-code")
    asyncio.run(p2.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) == "77"


def test_unbind_from_stranger_ignored():
    store = Store(":memory:")
    store.set_runtime_flag(_OWNER_FLAG, str(OWNER), ts=0.0)
    api = FakeApi([[{"update_id": 1, "message": {
        "message_id": 1, "text": "/unbind", "chat": {"id": 999999}}}]])
    poller = _poller(api, store=store, owner_chat_id=None)
    asyncio.run(poller.poll_once())
    assert store.get_runtime_flag(_OWNER_FLAG) == str(OWNER)  # чужой не отвязал


def test_run_forever_swallows_getupdates_error_and_continues():
    store = Store(":memory:")

    class Boom:
        def __init__(self): self.slept = []
        async def get(self, method, payload):
            raise RuntimeError("network down")
        async def post(self, method, payload):
            return {"ok": True}

    boom = Boom()

    class _Stop(Exception):
        pass

    async def stop_sleep(_seconds):
        raise _Stop()   # прерываем бесконечный цикл ПОСЛЕ того как ошибка проглочена

    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=1,
        http_get=boom.get, http_post=boom.post, clock=lambda: 0.0,
        async_sleep=stop_sleep,
    )

    async def scenario():
        with pytest.raises(_Stop):
            await poller.run_forever()

    asyncio.run(scenario())   # если бы RuntimeError не глотался, вылетел бы он, а не _Stop


# --- config-арка: контрол-бот маршрутизирует config-команды владельца --------
def test_config_handler_fake_matches_real_signature():
    """КЛАСС-ГАРД (см. CLAUDE.md рядом с DEV-18): фейк config_handler ОБЯЗАН
    совпадать по сигнатуре с реальным TelethonRunner.handle_config_command.
    Именно расхождение (фейк принимал language позиционно, реальный — keyword-only)
    держало фичу сломанной при 511 зелёных. Проверяем автоматически, не «на глаз»."""
    real = inspect.signature(TelethonRunner.handle_config_command)
    # реальный параметр language — keyword-only (после `*`)
    assert real.parameters["language"].kind is inspect.Parameter.KEYWORD_ONLY

    # фейк, которым пользуются тесты роутинга ниже, должен повторять это же
    async def config_handler(name, arg, *, language):
        return "x"

    fake = inspect.signature(config_handler)
    assert fake.parameters["language"].kind is inspect.Parameter.KEYWORD_ONLY
    # и позиционные имена/порядок совпадают с реальными (без self)
    real_pos = [p for p in real.parameters.values()
                if p.name != "self" and p.kind is not inspect.Parameter.KEYWORD_ONLY]
    fake_pos = [p for p in fake.parameters.values()
                if p.kind is not inspect.Parameter.KEYWORD_ONLY]
    assert [p.name for p in real_pos] == [p.name for p in fake_pos] == ["name", "arg"]


def test_owner_config_command_routed_and_replied():
    store = Store(":memory:")
    called = {}

    # keyword-only language — ОДИН-В-ОДИН с реальным handle_config_command.
    # Если прод-вызов регрессирует к позиционному, фейк даст TypeError и этот
    # тест покраснеет (раньше фейк молча принимал language позиционно и лгал).
    async def config_handler(name, arg, *, language):
        called["args"] = (name, arg, language)
        return "⚙️ ОТВЕТ КОНФИГА"

    api = FakeApi([[{
        "update_id": 7,
        "message": {"message_id": 1, "text": "/knowledge новая цена 9999", "chat": {"id": OWNER}},
    }]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert called["args"] == ("knowledge", "новая цена 9999", "ru")
    sent = api.payload_for("sendMessage")
    assert sent["text"] == "⚙️ ОТВЕТ КОНФИГА"


def test_config_command_failure_replies_to_owner_not_silence():
    """DEV-18: если config-хендлер падает, владелец ОБЯЗАН получить ответ
    (локализованный сигнал сбоя) + ошибка в логе, а НЕ тишину. Раньше падение
    молча возвращало None и пульт «оглох»."""
    store = Store(":memory:")

    async def config_handler(name, arg, *, language):
        raise RuntimeError("boom внутри хендлера")

    api = FakeApi([[{
        "update_id": 7,
        "message": {"message_id": 1, "text": "/reload", "chat": {"id": OWNER}},
    }]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    # владелец получил СООБЩЕНИЕ (не тишину)
    sent = api.payload_for("sendMessage")
    assert sent["chat_id"] == OWNER
    # это локализованный сигнал сбоя из console_text("config_command_failed", "ru")
    from chatter.core.console import console_text
    assert sent["text"] == console_text("config_command_failed", "ru")


def test_non_owner_config_command_rejected():
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, *, language):
        called["hit"] = True
        return "x"

    api = FakeApi([[{
        "update_id": 7,
        "message": {"message_id": 1, "text": "/reload", "chat": {"id": 999999}},
    }]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert "hit" not in called   # чужому config-команды недоступны


# --- Б3(а): навигационный тап не должен разрушать карточку -------------------

_KB = {"inline_keyboard": [
    [{"text": "▶️ Вернуть Аню", "callback_data": "resume:42:demo"},
     {"text": "⏸ Ещё 1ч", "callback_data": "snooze:42:demo"}],
    [{"text": "🔗 Открыть диалог", "callback_data": "open:42:demo"}],
]}


def _callback_update_with_kb(uid, *, data, from_id=237616472):
    u = _callback_update(uid, data=data, from_id=from_id)
    u["callback_query"]["message"]["reply_markup"] = _KB
    return u


def test_open_tap_keeps_card_buttons_alive():
    """Баг живого теста volska 2026-07-21: владелец промахнулся по кнопке и
    потерял управление карточкой.

    editMessageText БЕЗ reply_markup удаляет инлайн-клавиатуру (Bot API), так
    что ЛЮБОЙ тап стирал все кнопки. Для `open` это особенно скверно: действие
    чисто навигационное — не мутирует Store, не пишет событие, не закрывает
    esc_active — а карточка после него становится неуправляемой, и «переиграть»
    промах уже нечем.
    """
    store = Store(":memory:")
    store.get_or_create_contact("telegram:42:demo")
    api = FakeApi([[_callback_update_with_kb(10, data="open:42:demo")]])

    asyncio.run(_poller(api, store=store).poll_once())

    edit = api.payload_for("editMessageText")
    assert edit.get("reply_markup") == _KB, "навигационный тап убил кнопки карточки"


def test_open_tap_mutates_nothing():
    # Стоп-гард к фиксу выше: open остаётся чистой навигацией.
    store = Store(":memory:")
    store.get_or_create_contact("telegram:42:demo")
    store.set_runtime_flag("esc_active:42:demo", "bot:1:25", ts=1.0)
    api = FakeApi([[_callback_update_with_kb(11, data="open:42:demo")]])

    asyncio.run(_poller(api, store=store).poll_once())

    assert store.get_or_create_contact("telegram:42:demo")["paused"] == 0
    assert store.get_runtime_flag("esc_active:42:demo") == "bot:1:25"


# --- /honesty: видимая кнопка, но переключение ТОЛЬКО по набранному confirm ---

def test_honesty_status_reply_carries_the_button():
    store = Store(":memory:")

    async def config_handler(name, arg, *, language):
        return "Чесність: УВІМК"

    api = FakeApi([[{
        "update_id": 40,
        "message": {"message_id": 1, "text": "/honesty", "chat": {"id": OWNER}},
    }]])
    poller = ControlBotPoller(
        "T", store=store, language="uk", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    sent = api.payload_for("sendMessage")
    kb = sent.get("reply_markup", {}).get("inline_keyboard")
    assert kb, "у /honesty нет кнопки"
    assert kb[0][0]["callback_data"] == "honesty_warn"


def test_honesty_button_tap_only_warns_and_never_switches():
    """Ядро задачи: тап — это ПРЕДУПРЕЖДЕНИЕ, а не переключение.

    Владелец отверг «выключить честность одним тапом»; кнопку вернули только
    потому, что тап ведёт к набранному /honesty free confirm. Если тап начнёт
    что-то переключать сам — защита исчезла."""
    store = Store(":memory:")
    calls = []

    async def config_handler(name, arg, *, language):
        calls.append((name, arg))
        return "не должно вызываться на тапе"

    api = FakeApi([[{
        "update_id": 41,
        "callback_query": {
            "id": "cb", "data": "honesty_warn", "from": {"id": OWNER},
            "message": {"message_id": 9, "chat": {"id": OWNER}},
        },
    }]])
    poller = ControlBotPoller(
        "T", store=store, language="uk", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert calls == [], "тап дёрнул config-хендлер — переключение возможно тапом"
    sent = api.payload_for("sendMessage")
    assert "confirm" in sent["text"].casefold()
    assert "editMessageText" not in api.methods()   # карточку не трогаем


# --- /allow: цель можно взять из пересланного сообщения (реплай) -----------


def _allow_update(uid, *, text, chat_id=OWNER, reply_to=None):
    m = {"message_id": uid, "text": text, "chat": {"id": chat_id}}
    if reply_to is not None:
        m["reply_to_message"] = reply_to
    return {"update_id": uid, "message": m}


def test_allow_explicit_target_passes_through_unchanged():
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, *, language):
        called["args"] = (name, arg)
        return "ok"

    api = FakeApi([[_allow_update(1, text="/allow 555 confirm")]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert called["args"] == ("allow", "555 confirm")


def test_allow_reply_to_forwarded_message_resolves_target():
    """Владелец пересылает сообщение лида в контрол-бот, затем отвечает на
    него голым /allow -- цель должна взяться из forward_from.id пересланного
    сообщения, а не остаться пустой."""
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, *, language):
        called["args"] = (name, arg)
        return "ok"

    reply_to = {"message_id": 9, "forward_from": {"id": 777888}}
    api = FakeApi([[_allow_update(2, text="/allow", reply_to=reply_to)]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert called["args"] == ("allow", "777888")


def test_allow_remove_confirm_reply_to_forward_resolves_target_in_order():
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, *, language):
        called["args"] = (name, arg)
        return "ok"

    reply_to = {"message_id": 9, "forward_from": {"id": 777888}}
    api = FakeApi([[_allow_update(3, text="/allow remove confirm", reply_to=reply_to)]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert called["args"] == ("allow", "remove 777888 confirm")


def test_allow_reply_to_non_forward_message_leaves_target_unresolved():
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, *, language):
        called["args"] = (name, arg)
        return "ok"

    reply_to = {"message_id": 9, "text": "обычный ответ, не форвард"}
    api = FakeApi([[_allow_update(4, text="/allow", reply_to=reply_to)]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert called["args"] == ("allow", "")   # цель не подставилась -- нечего резолвить


def test_reload_command_ignores_reply_to_message():
    """Инъекция цели -- только для /allow; другие config-команды реплай не трогают."""
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, *, language):
        called["args"] = (name, arg)
        return "ok"

    reply_to = {"message_id": 9, "forward_from": {"id": 777888}}
    api = FakeApi([[_allow_update(5, text="/reload", reply_to=reply_to)]])
    poller = ControlBotPoller(
        "T", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 0.0,
        config_handler=config_handler)
    asyncio.run(poller.poll_once())

    assert called["args"] == ("reload", "")
