from __future__ import annotations

import asyncio

from chatter.notify.base import Action, Button, Card, CardHandle
from chatter.notify.saved_messages import SavedMessagesNotifier


def _run_coro(coro):
    """Тестовый маршалер: гоняет корутину синхронно, без реального loop/сети."""
    return asyncio.run(coro)


class FakeMsg:
    def __init__(self, mid): self.id = mid


class FakeClient:
    def __init__(self, *, fail=False):
        self.sent = []
        self.edited = []
        self._fail = fail

    async def send_message(self, entity, text, parse_mode=None):
        if self._fail:
            raise RuntimeError("network down")
        self.sent.append((entity, text, parse_mode))
        return FakeMsg(777)

    async def edit_message(self, entity, msg_id, text, parse_mode=None):
        self.edited.append((entity, msg_id, text, parse_mode))


def _card():
    return Card(
        kind="pause", contact_id="42:demo", text_html="<b>⏸ Пауза</b>",
        buttons=[Button(action=Action.RESUME, label="▶️")],
        reply_hints=["Ответьте /resume на это сообщение", "или /status → /resume 1"],
        link="t.me/x",
    )


def test_notify_sends_to_me_with_hints_appended_html():
    client = FakeClient()
    n = SavedMessagesNotifier(client, loop=None, run_coro=_run_coro)
    handle = n.notify(_card())
    assert handle == CardHandle(ref="me:777")
    entity, text, parse_mode = client.sent[0]
    assert entity == "me"
    assert parse_mode == "html"
    assert "⏸ Пауза" in text
    # копипаст-подсказки приклеены снизу (фоллбек без кнопок)
    assert "Ответьте /resume" in text
    assert "/status → /resume 1" in text


def test_has_no_buttons():
    n = SavedMessagesNotifier(FakeClient(), loop=None, run_coro=_run_coro)
    assert n.has_buttons is False


def test_notify_failure_returns_none_never_raises():
    n = SavedMessagesNotifier(FakeClient(fail=True), loop=None, run_coro=_run_coro)
    assert n.notify(_card()) is None    # DEV-18: не бросает, возвращает None


def test_edit_marshals_edit_message():
    client = FakeClient()
    n = SavedMessagesNotifier(client, loop=None, run_coro=_run_coro)
    n.edit(CardHandle(ref="me:777"), "▶️ Аня вернулась")
    entity, msg_id, text, parse_mode = client.edited[0]
    assert (entity, msg_id, text, parse_mode) == ("me", 777, "▶️ Аня вернулась", "html")
