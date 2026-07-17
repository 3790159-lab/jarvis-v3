"""End-to-end арки 3B БЕЗ сети: реальные ControlBotNotifier + ControlBotPoller
поверх фейкового HTTP. Горячий лид → карточка с кнопками у владельца → тап
«Вернуть Аню» → размут + мгновенный feedback. Доказательство петли перед
живым дрилом (критерий приёмки: владелец рулит тапами, не открывая аккаунт)."""
from __future__ import annotations

import asyncio
import random
from pathlib import Path

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.classifier import ClassifierResult
from chatter.core.llm import FakeLLM
from chatter.notify.control_bot import ControlBotNotifier, ControlBotPoller
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.base import Transport

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"
OWNER = 237616472
CONTACT = "42:demo"


class _Silent(Transport):
    def receive(self, timeout=None): return None
    def send(self, text): pass
    def send_typing(self, on): pass
    def set_online(self, on): pass
    def read_acknowledge(self): pass


class FakeBotApi:
    """Один объект под sync (notifier) и async (poller) вызовы Bot API."""
    def __init__(self):
        self.calls = []
        self._next_msg_id = 500
        self._updates = []

    # sync — для ControlBotNotifier.notify
    def post_sync(self, method, payload):
        self.calls.append((method, payload))
        self._next_msg_id += 1
        return {"ok": True, "result": {"message_id": self._next_msg_id}}

    # async — для ControlBotPoller
    async def get(self, method, payload):
        return {"ok": True, "result": self._updates.pop(0) if self._updates else []}

    async def post(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True, "result": {}}

    def queue_updates(self, batch):
        self._updates.append(batch)

    def methods(self):
        return [m for m, _ in self.calls]

    def payload_for(self, method):
        return next(p for m, p in self.calls if m == method)

    def last_payload_for(self, method):
        return next(p for m, p in reversed(self.calls) if m == method)


def test_hot_lead_escalates_to_control_bot_then_owner_tap_resumes():
    api = FakeBotApi()
    store = Store(":memory:")
    cfg = load_config(CLIENTS, "demo")
    notifier = ControlBotNotifier("TOKEN", OWNER, http_post=api.post_sync)
    deps = Deps(
        cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=["Отлично, расскажу!"]), cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None,
        notifier=notifier, escalation_keywords=["позови", "оплата"],
        classify=lambda h: ClassifierResult(
            escalate=True, reason="готов внести предоплату", stage_signal="interested"),
        control=cfg.settings.control,
    )
    store.get_or_create_contact(CONTACT)

    # 1) горячий лид пишет, Аня АКТИВНА — эскалация уходит контрол-ботом
    #    ВЛАДЕЛЬЦУ с кнопками
    process_batch(CONTACT, ["хочу забронировать, где оплата?"], _Silent(), deps)

    send = api.payload_for("sendMessage")
    assert send["chat_id"] == OWNER
    assert "предоплат" in send["text"]                          # что хочет — в карточке
    kb = send["reply_markup"]["inline_keyboard"]
    datas = {b["callback_data"] for row in kb for b in row}
    assert f"snooze:{CONTACT}" in datas                         # «⏸ Ещё 1ч» (беру диалог)
    assert f"stop:{CONTACT}" in datas                           # «🔴 Стоп везде»
    assert store.get_or_create_contact(CONTACT)["state"] == "escalated"

    poller = ControlBotPoller(
        "TOKEN", store=store, language="ru", snooze_seconds=3600, owner_chat_id=OWNER,
        http_get=api.get, http_post=api.post, clock=lambda: 2000.0)

    def _tap(action, msg_id):
        api.queue_updates([{
            "update_id": msg_id, "callback_query": {
                "id": f"cbq{msg_id}", "data": f"{action}:{CONTACT}", "from": {"id": OWNER},
                "message": {"message_id": msg_id, "chat": {"id": OWNER}}}}])
        asyncio.run(poller.poll_once())

    # 2) владелец ТАПАЕТ «Ещё 1ч» — берёт диалог себе: Аня замолкает здесь
    _tap("snooze", 501)
    row = store.get_or_create_contact(CONTACT)
    assert row["paused"] == 1 and row["pause_until"] == 2000.0 + 3600
    assert "пауза" in api.last_payload_for("editMessageText")["text"].casefold()  # мгновенный feedback
    assert "answerCallbackQuery" in api.methods()

    # 3) владелец закончил — ТАПАЕТ «Вернуть Аню»: она снова отвечает
    _tap("resume", 502)
    assert store.get_or_create_contact(CONTACT)["paused"] == 0
    assert "верну" in api.last_payload_for("editMessageText")["text"].casefold()
