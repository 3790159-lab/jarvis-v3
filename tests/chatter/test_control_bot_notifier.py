from __future__ import annotations

from chatter.notify.base import Action, Button, Card, CardHandle
from chatter.notify.control_bot import ControlBotNotifier


class FakePost:
    def __init__(self, result=None, *, fail=False):
        self.calls = []
        self._result = result or {"ok": True, "result": {"message_id": 555}}
        self._fail = fail

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if self._fail:
            raise RuntimeError("bot api down")
        return self._result


def _card():
    return Card(
        kind="escalation", contact_id="42:demo", text_html="<b>🔴 Лид</b>",
        buttons=[
            Button(action=Action.RESUME, label="▶️ Вернуть Аню"),
            Button(action=Action.STOP, label="🔴 Стоп везде"),
        ],
        reply_hints=["ignored by control bot"], link="t.me/x",
    )


def test_notify_builds_sendmessage_with_inline_keyboard():
    post = FakePost()
    n = ControlBotNotifier("TOKEN", 237616472, http_post=post)
    handle = n.notify(_card())
    assert handle == CardHandle(ref="bot:237616472:555")
    method, payload = post.calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == 237616472
    assert payload["parse_mode"] == "HTML"
    assert "🔴 Лид" in payload["text"]
    kb = payload["reply_markup"]["inline_keyboard"]
    # callback_data кодирует действие + contact_id
    flat = [btn for row in kb for btn in row]
    datas = {b["callback_data"] for b in flat}
    assert "resume:42:demo" in datas
    assert "stop:42:demo" in datas
    texts = {b["text"] for b in flat}
    assert "▶️ Вернуть Аню" in texts


def test_has_buttons_true():
    assert ControlBotNotifier("T", 1, http_post=FakePost()).has_buttons is True


def test_edit_posts_edit_message_text_and_strips_buttons():
    post = FakePost()
    n = ControlBotNotifier("TOKEN", 237616472, http_post=post)
    n.edit(CardHandle(ref="bot:237616472:555"), "▶️ Аня вернулась")
    method, payload = post.calls[0]
    assert method == "editMessageText"
    assert payload["chat_id"] == 237616472
    assert payload["message_id"] == 555
    assert payload["text"] == "▶️ Аня вернулась"
    # финальный feedback без кнопок
    assert "reply_markup" not in payload or not payload["reply_markup"].get("inline_keyboard")


def test_notify_failure_returns_none_never_raises():
    n = ControlBotNotifier("T", 1, http_post=FakePost(fail=True))
    assert n.notify(_card()) is None


def test_notify_bot_api_not_ok_returns_none():
    n = ControlBotNotifier("T", 1, http_post=FakePost(result={"ok": False, "description": "bad"}))
    assert n.notify(_card()) is None
