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


def _poller(api, *, store, owner_chat_id=237616472, on_bind=None):
    return ControlBotPoller(
        "TOKEN", store=store, language="ru", snooze_seconds=3600,
        owner_chat_id=owner_chat_id, http_get=api.get, http_post=api.post,
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


def test_start_with_no_configured_owner_binds_tofu():
    store = Store(":memory:")
    bound = []
    api = FakeApi([[{
        "update_id": 5,
        "message": {"message_id": 1, "text": "/start", "chat": {"id": 555000}},
    }]])
    poller = _poller(api, store=store, owner_chat_id=None, on_bind=bound.append)

    asyncio.run(poller.poll_once())

    assert store.get_runtime_flag("control_owner_chat_id") == "555000"
    assert bound == [555000]
    assert "sendMessage" in api.methods()
    # и теперь этот chat — владелец
    assert poller._effective_owner() == 555000


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
