from __future__ import annotations

from chatter.notify.base import (
    Action, Button, Card, CardHandle, FakeNotifier, Notifier,
)


def _card(**kw):
    base = dict(
        kind="escalation", contact_id="42:demo", text_html="<b>hi</b>",
        buttons=[Button(action=Action.RESUME, label="▶️ Вернуть")],
        reply_hints=["/resume 1"], link="t.me/x",
    )
    base.update(kw)
    return Card(**base)


def test_fake_notifier_records_notify_and_returns_handle():
    n = FakeNotifier()
    card = _card()
    handle = n.notify(card)
    assert isinstance(handle, CardHandle)
    assert n.cards == [card]


def test_fake_notifier_records_edits():
    n = FakeNotifier()
    handle = n.notify(_card())
    n.edit(handle, "▶️ Аня вернулась")
    assert n.edits == [(handle, "▶️ Аня вернулась")]


def test_fake_notifier_has_buttons_configurable():
    assert FakeNotifier(has_buttons=True).has_buttons is True
    assert FakeNotifier(has_buttons=False).has_buttons is False


def test_fake_notifier_is_a_notifier():
    assert isinstance(FakeNotifier(), Notifier)


def test_action_values_are_stable_callback_tokens():
    # callback_data кодируется как "<action>:<contact_id>" — значения должны быть
    # стабильными короткими токенами.
    assert Action.RESUME.value == "resume"
    assert Action.SNOOZE.value == "snooze"
    assert Action.OPEN.value == "open"
    assert Action.STOP.value == "stop"
    assert Action.KEEP.value == "keep"
