"""Б4 (D2/P4): отмена УСТАРЕВШЕГО ответа, когда лид дописал во время доставки.

Позорный сценарий продукта «неотличима от человека»: Ольга минуту шлёт три
баббла с расчётом, лид на первом же передумал («ой, не треба поки»), а она
досылает остаток уже ПОСЛЕ его новой реплики. Живой человек так не делает —
он перестаёт печатать, когда собеседник пишет.

Спека: docs/superpowers/specs/2026-07-23-chatter-b4-epoch-cancel.md
"""
from __future__ import annotations

import asyncio
import random
from pathlib import Path

from chatter.core.brain import Brain
from chatter.core.classifier import ClassifierResult
from chatter.core.llm import FakeLLM
from chatter.config.loader import load_config
from chatter.notify.base import FakeNotifier
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.base import Transport

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"
CONTACT = "42:demo"

# Длинный ответ, который хуманайзер гарантированно порежет на несколько бабблов
# (split_max_len у demo = 160). БЕЗ цифр и обещаний: иначе гардрейл-слой
# подавит ответ как необеспеченный (unbacked_claim), схлопнет его в одну
# короткую фразу — и тест перестанет проверять то, ради чего написан.
LONG_REPLY = (
    "Ми робимо повну айдентику: логотип, підбір шрифтів, кольорову палітру "
    "і графічні елементи для носіїв. "
    "Зазвичай усе починається з короткого брифу, щоб зрозуміти вашу сферу "
    "та вподобання у стилі. "
    "Підкажіть, будь ласка, що у вас уже є з матеріалів?"
)


class RecordingTransport(Transport):
    """Считает бабблы и состояние «печатает»."""

    def __init__(self):
        self.sent: list[str] = []
        self.typing: list[bool] = []

    def receive(self, timeout=None):
        return None

    def send(self, text):
        self.sent.append(text)

    def send_typing(self, on):
        self.typing.append(on)

    def set_online(self, on):
        pass

    def read_acknowledge(self):
        pass


def _deps(*, notifier=None, classify=None, reply=LONG_REPLY):
    cfg = load_config(CLIENTS, "demo")
    store = Store(":memory:")
    deps = Deps(
        cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=[reply]), cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None,
    )
    deps.notifier = notifier
    deps.classify = classify
    deps.escalation_keywords = []
    store.get_or_create_contact(CONTACT)
    return deps


def _events(store, kind):
    return list(store._conn.execute(
        "SELECT contact_id, detail FROM control_events WHERE kind=?", (kind,)))


def _assistant_history(store):
    return [m["text"] for m in store.history(CONTACT) if m["role"] == "assistant"]


# --- 1-4: process_batch, чек перед каждым Say -------------------------------

def test_fresh_incoming_between_bubbles_cancels_the_remainder():
    """Свежая реплика пришла после первого баббла: остаток НЕ уходит,
    отправленная часть остаётся в истории, «печатает» снято, факт в БД."""
    deps = _deps()
    t = RecordingTransport()
    process_batch(CONTACT, ["скільки коштує лого?"], t, deps,
                  fresh_incoming=lambda: len(t.sent) >= 1)
    assert len(t.sent) == 1, f"остаток устаревшего ответа ушёл: {t.sent}"
    assert _assistant_history(deps.store) == t.sent
    assert t.typing and t.typing[-1] is False, "осталась висеть плашка «печатає»"
    rows = _events(deps.store, "stale_reply_cancelled")
    assert rows and rows[0][0] == CONTACT
    assert rows[0][1] and "1" in rows[0][1], "в событии нет «N из M бабблов»"


def test_fresh_incoming_before_first_bubble_drops_whole_reply():
    """Лид дописал, пока модель ещё генерила: выбрасывается ВЕСЬ ответ.
    Стоимость LLM-вызова уже потрачена — принимаем осознанно (спека §3)."""
    deps = _deps()
    t = RecordingTransport()
    process_batch(CONTACT, ["скільки коштує лого?"], t, deps,
                  fresh_incoming=lambda: True)
    assert t.sent == []
    assert _assistant_history(deps.store) == []
    assert _events(deps.store, "stale_reply_cancelled")


def test_without_fresh_incoming_everything_is_delivered():
    """Регресс-защита консоли, catch-up и существующих тестов: None = поведение
    ровно прежнее, чек всегда молчит."""
    deps = _deps()
    t = RecordingTransport()
    process_batch(CONTACT, ["скільки коштує лого?"], t, deps)
    assert len(t.sent) >= 2, "хуманайзер не разбил ответ — тест ничего не проверяет"
    assert _assistant_history(deps.store) == t.sent
    assert _events(deps.store, "stale_reply_cancelled") == []


def test_cancelled_remainder_never_enters_history():
    """Контракт для регенерации: модель не должна думать, будто уже сказала
    невысказанное. Неотправленного в истории нет — значит следующий ответ
    вправе сказать это заново."""
    deps = _deps()
    t = RecordingTransport()
    process_batch(CONTACT, ["скільки коштує лого?"], t, deps,
                  fresh_incoming=lambda: len(t.sent) >= 1)
    history = " ".join(_assistant_history(deps.store))
    assert "700–900" not in history, "отменённый хвост попал в историю"


# --- 5-6: ChatDebouncer, счётчик поколений ---------------------------------

def test_message_arriving_during_delivery_makes_fresh_incoming_true():
    """add() во время on_ready виден изнутри ЭТОГО же on_ready, а слитый буфер
    после доставки процессится следующей итерацией (поведение слива не сломано)."""
    from chatter.telethon_run import ChatDebouncer

    async def scenario():
        clock = {"t": 0.0}
        seen: list[bool] = []
        batches: list[list[str]] = []

        async def on_ready(batch):
            batches.append(batch)
            if len(batches) == 1:
                deb.add("а ні, передумав")           # лид дописал МИД-доставки
                seen.append(deb.fresh_incoming())

        deb = ChatDebouncer(
            window=0.0, max_window=0.0, clock=lambda: clock["t"],
            async_sleep=lambda s: asyncio.sleep(0), on_ready=on_ready,
        )
        deb.add("скільки коштує?")
        await asyncio.wait_for(deb.task, timeout=2.0)
        return seen, batches

    seen, batches = asyncio.run(scenario())
    assert seen == [True], "входящее во время доставки не видно чеку"
    assert batches == [["скільки коштує?"], ["а ні, передумав"]], (
        "слив буфера сломан — дописанное сообщение потерялось")


def test_epoch_is_snapshotted_at_flush_so_own_batch_is_not_fresh():
    """Эпоха снимается на флаше: сообщения САМОГО батча не считаются свежими,
    иначе любой залп из двух сообщений отменял бы собственный ответ."""
    from chatter.telethon_run import ChatDebouncer

    async def scenario():
        seen: list[bool] = []

        async def on_ready(batch):
            seen.append(deb.fresh_incoming())

        deb = ChatDebouncer(
            window=0.0, max_window=0.0, clock=lambda: 0.0,
            async_sleep=lambda s: asyncio.sleep(0), on_ready=on_ready,
        )
        deb.add("привіт")
        deb.add("скільки коштує?")          # тот же залп, до флаша
        await asyncio.wait_for(deb.task, timeout=2.0)
        return seen

    assert asyncio.run(scenario()) == [False], "батч отменил сам себя"


# --- 7: сквозной ---------------------------------------------------------

def test_next_reply_sees_sent_part_plus_new_line():
    """Сквозной: реплика мид-доставки → остаток отменён → следующий ответ
    генерируется с историей «отправленная часть + новая реплика лида»."""
    deps = _deps()
    t = RecordingTransport()
    process_batch(CONTACT, ["скільки коштує лого?"], t, deps,
                  fresh_incoming=lambda: len(t.sent) >= 1)
    sent_part = list(t.sent)

    deps.brain = Brain(FakeLLM(scripted=["Звісно, без проблем 🙂"]), deps.cfg)
    t2 = RecordingTransport()
    process_batch(CONTACT, ["ой, поки не треба"], t2, deps)

    history = deps.store.history(CONTACT)
    texts = [m["text"] for m in history]
    assert sent_part[0] in texts
    assert "ой, поки не треба" in texts
    assert texts.index(sent_part[0]) < texts.index("ой, поки не треба")


# --- дополнение Даниила: судьба карточки керівниці при отмене --------------

def test_escalation_card_is_not_rolled_back_by_the_cancel():
    """Карточка НЕ откатывается: факт «лид просил точную смету» реален
    независимо от того, ушли ли бабблы. Откат карточки означал бы, что
    владелец теряет живой сигнал из-за задержки доставки."""
    n = FakeNotifier()
    deps = _deps(
        notifier=n,
        classify=lambda history, profile=None: ClassifierResult(
            escalate=True, reason="просить точну смету", stage_signal="interested"),
    )
    t = RecordingTransport()
    process_batch(CONTACT, ["прорахуйте мій проєкт"], t, deps,
                  fresh_incoming=lambda: len(t.sent) >= 1)
    assert [c for c in n.cards if c.kind == "escalation"], "карточка исчезла"
    assert deps.store.get_or_create_contact(CONTACT)["state"] == "escalated"


def test_owner_is_warned_that_the_card_may_be_stale():
    """Спурьезная карточка керівниці хуже отменённого ответа: если карточка
    ушла на ЭТОМ ходу, а лид тут же дописал (в т.ч. «передумав»), владелец
    обязан узнать, что карточка могла устареть. Тихая правка не годится —
    editMessageText не шлёт пуш (дрил 07-18), поэтому это ОТДЕЛЬНОЕ сообщение."""
    n = FakeNotifier()
    deps = _deps(
        notifier=n,
        classify=lambda history, profile=None: ClassifierResult(
            escalate=True, reason="просить точну смету", stage_signal="interested"),
    )
    t = RecordingTransport()
    process_batch(CONTACT, ["прорахуйте мій проєкт"], t, deps,
                  fresh_incoming=lambda: len(t.sent) >= 1)
    notices = [c for c in n.cards if c.kind == "alert"]
    assert len(notices) == 1, "владелец не предупреждён об устаревшей карточке"
    assert CONTACT.split(":")[0] in notices[0].text_html


def test_no_stale_card_notice_when_no_card_was_posted_this_turn():
    """Без эскалации на этом ходу предупреждать не о чем — лишний пуш владельцу
    это шум, а шум обесценивает карточки."""
    deps = _deps(notifier=FakeNotifier())
    t = RecordingTransport()
    process_batch(CONTACT, ["скільки коштує лого?"], t, deps,
                  fresh_incoming=lambda: len(t.sent) >= 1)
    assert [c for c in deps.notifier.cards if c.kind == "alert"] == []


# --- проводка в раннере ----------------------------------------------------

def test_runner_hands_the_debouncer_check_to_process_batch(monkeypatch):
    """Без этой проводки весь Б4 — мёртвый код: чек существует, но в прод
    приходит None и остаток всегда досылается."""
    import chatter.telethon_run as tr
    from tests.chatter.test_telethon_run import _runner

    captured: dict = {}

    def fake_process_batch(contact_id, batch, transport, deps, **kw):
        captured["kw"] = kw

    monkeypatch.setattr(tr, "process_batch", fake_process_batch)
    monkeypatch.setattr(tr, "TelethonTransport", lambda *a, **k: None)

    async def scenario():
        runner, _ = _runner()
        deb = runner._new_debouncer(peer=object(), sender_id=42, persona_slug="demo")
        await deb._on_ready(["привіт"])
        assert "fresh_incoming" in captured["kw"], "раннер не прокинул чек — Б4 мёртв"
        check = captured["kw"]["fresh_incoming"]
        before = check()
        # add() заводит задачу дебаунсера — только внутри живого loop'а
        deb.add("а ні, передумав")
        after = check()
        deb.task.cancel()
        return before, after

    before, after = asyncio.run(scenario())
    assert before is False
    assert after is True, "прокинут не тот чек — новое входящее его не двигает"
