from __future__ import annotations

import asyncio

import pytest

from chatter.notify.control_bot import ControlBotPoller
from chatter.storage.db import Store


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
    store.get_or_create_contact("42:demo")
    store.mute("42:demo", source="human_takeover", now=1.0)
    api = FakeApi([[_callback_update(10, data="resume:42:demo", from_id=237616472)]])
    poller = _poller(api, store=store)

    asyncio.run(poller.poll_once())

    # действие исполнено
    assert store.get_or_create_contact("42:demo")["paused"] == 0
    # мгновенная обратная связь: карточка отредактирована + тост
    assert "editMessageText" in api.methods()
    assert "answerCallbackQuery" in api.methods()
    edit = api.payload_for("editMessageText")
    assert edit["message_id"] == 555 and edit["chat_id"] == 237616472
    assert "верну" in edit["text"].casefold()
    # offset сдвинут за апдейт
    assert poller._offset == 11


def test_non_owner_callback_is_rejected_without_mutation():
    store = Store(":memory:")
    store.get_or_create_contact("42:demo")
    store.mute("42:demo", source="human_takeover", now=1.0)
    api = FakeApi([[_callback_update(10, data="resume:42:demo", from_id=999999)]])
    poller = _poller(api, store=store)

    asyncio.run(poller.poll_once())

    assert store.get_or_create_contact("42:demo")["paused"] == 1   # НЕ размучен
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
def test_owner_config_command_routed_and_replied():
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, language):
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


def test_non_owner_config_command_rejected():
    store = Store(":memory:")
    called = {}

    async def config_handler(name, arg, language):
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
