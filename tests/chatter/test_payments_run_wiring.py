# -*- coding: utf-8 -*-
"""Проводка Ф0 п.4: блок счёта в промпте и порядок модель → guardrails → подстановка.

Проверяется на РЕАЛЬНОМ пути `process_batch`, а не на кусках: ровно здесь живут
два свойства, которые нельзя проверить по отдельности.

1. **Блок счёта уходит ПОСЛЕ cache-breakpoint'а** (`uncached_suffix`), а не в
   стабильном префиксе: статус счёта меняется, а изменчивое в префиксе убивает
   кэш (регрессия 23.07).
2. **Подстановка идёт ПОСЛЕ guardrails.** Сумма ступени в knowledge отсутствует
   по определению, и редакция `large_number` вырезала бы её как необеспеченную —
   вместе со смыслом ответа. Тест различающий: он требует, чтобы цифра ДОЕХАЛА.

И следствие §8.3: ответ с неподставленным плейсхолдером подавляется целиком.
"""
from __future__ import annotations

import random
import shutil
from pathlib import Path

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.core.obligations_slot import invoice_slug, merge_obligations
from chatter.notify.control_bot import route_callback
from chatter.payments.money import from_major
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.base import Transport

SRC = Path(__file__).resolve().parents[2] / "chatter" / "clients"
NOW = 1_786_000_000.0
DUE = NOW + 3 * 86400.0
CONTACT = "lead:demo"

PAYMENTS_BLOCK = """
payments:
  enabled: true
  channels:
    - id: iban_main
      kind: bank_transfer
      mode: manual
      currency: USD
      requisites_template: iban_main
      display: "Банківський переказ"
"""
REQUISITES = "templates:\n  iban_main:\n    body: 'IBAN UA00 1234 5678'\n"
BLOCK_HEAD = "РАХУНОК КЛІЄНТА"


class RecordingTransport(Transport):
    def __init__(self):
        self.sent = []

    def receive(self, timeout=None):
        return None

    def send(self, text):
        self.sent.append(text)

    def send_typing(self, on):
        pass

    def set_online(self, on):
        pass

    def read_acknowledge(self):
        pass


def _clients(tmp_path, *, payments=True, requisites=True) -> Path:
    dst = tmp_path / "clients"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    if payments:
        p = dst / "demo" / "settings.yaml"
        p.write_text(p.read_text(encoding="utf-8") + PAYMENTS_BLOCK, encoding="utf-8")
    if requisites:
        (dst / "demo" / "requisites.yaml").write_text(REQUISITES, encoding="utf-8")
    return dst


def _deps(tmp_path, replies, **kw):
    cfg = load_config(_clients(tmp_path, **kw), "demo")
    llm = FakeLLM(scripted=list(replies))
    deps = Deps(cfg=cfg, store=Store(":memory:"), brain=Brain(llm, cfg),
                rng=random.Random(0), clock=lambda: NOW, sleep=lambda s: None)
    deps.notifier = None
    deps.classify = None
    deps.escalation_keywords = []
    return deps, llm


def _invoice(store, contact_id=CONTACT, *, status="issued", amount_major=900):
    store.get_or_create_contact(contact_id)
    return store.create_invoice(
        contact_id=contact_id, origin_msg_id=1,
        amount=None if amount_major is None else from_major(amount_major, "USD"),
        channel_id="iban_main", due_ts=DUE, created_by="bot",
        amount_source="price_upper", status=status, now=NOW,
        requisites_ref="iban_main")


def _tap(store, data="paidamt2:90000:USD:lead:demo", *, card_msg_id=77):
    return route_callback(data, store=store, now=NOW, language="ru",
                          snooze_seconds=3600.0, card_msg_id=card_msg_id)


# ── выключенная фича не меняет поведение ───────────────────────────────────

def test_payments_off_sends_the_reply_unchanged(tmp_path):
    deps, _ = _deps(tmp_path, ["Добрый день! Чем помочь?"],
                    payments=False, requisites=False)
    tr = RecordingTransport()
    process_batch(CONTACT, ["привет"], tr, deps)
    # Хуманайзер вправе поправить пунктуацию — сверяем, что ответ ДОЕХАЛ
    # и что подстановка ничего в нём не тронула.
    assert "Чем помочь" in " ".join(tr.sent)


def test_placeholder_never_reaches_the_lead_even_with_payments_off(tmp_path):
    """Выключенная фича не повод отправить «переказ на {REQUISITES}»: служебная
    форма в лицо клиенту — дефект независимо от тумблера."""
    deps, _ = _deps(tmp_path, ["Переказ на {REQUISITES}"],
                    payments=False, requisites=False)
    tr = RecordingTransport()
    process_batch(CONTACT, ["куда платить?"], tr, deps)
    assert tr.sent == []


# ── блок счёта: после breakpoint'а и без цифр ──────────────────────────────

def test_invoice_block_rides_after_the_cache_breakpoint(tmp_path):
    deps, llm = _deps(tmp_path, ["Нагадую про оплату {AMOUNT} на {REQUISITES} до {DUE}"])
    _invoice(deps.store)
    process_batch(CONTACT, ["привет"], RecordingTransport(), deps)

    call = [c for c in llm.calls if c["tag"] == "brain"][0]
    assert BLOCK_HEAD in (call["uncached_suffix"] or ""), \
        "блок счёта не доехал до модели вовсе"
    stable_prefix = (call["system"] or "").replace(call["uncached_suffix"] or "", "")
    assert BLOCK_HEAD not in stable_prefix, \
        "блок лёг в стабильный префикс — это убитый кэш"


def test_prompt_carries_neither_the_amount_nor_the_requisites(tmp_path):
    """Приёмка §8.5 п.3. Модель, увидевшая IBAN, может его «поправить» — один
    символ означает деньги, ушедшие не туда."""
    deps, llm = _deps(tmp_path, ["Нагадую про оплату {AMOUNT} на {REQUISITES} до {DUE}"])
    _invoice(deps.store)
    process_batch(CONTACT, ["привет"], RecordingTransport(), deps)

    prompt = " ".join((c["system"] or "") + (c["uncached_suffix"] or "") for c in llm.calls)
    assert "IBAN UA00 1234 5678" not in prompt
    assert "900" not in prompt


def test_no_invoice_no_block(tmp_path):
    deps, llm = _deps(tmp_path, ["Добрий день!"])
    process_batch(CONTACT, ["привет"], RecordingTransport(), deps)
    assert BLOCK_HEAD not in (llm.calls[0]["uncached_suffix"] or "")


# ── порядок: модель → guardrails → подстановка ─────────────────────────────

def test_substituted_amount_survives_the_guardrail(tmp_path):
    """§14 п.16, различающий тест. Подстановка ДО редакции означала бы, что
    `large_number` вырежет подставленную сумму: 900 в knowledge отсутствует."""
    deps, _ = _deps(tmp_path, ["Оплата {AMOUNT} на {REQUISITES} до {DUE}"])
    assert "900" not in deps.cfg.knowledge, "предпосылка теста сломана: 900 стало обеспеченным"
    _invoice(deps.store)
    tr = RecordingTransport()
    process_batch(CONTACT, ["куда платить?"], tr, deps)

    out = " ".join(tr.sent)
    assert "900" in out, "сумма не доехала до лида — подстановка идёт до guardrails"
    assert "IBAN UA00 1234 5678" in out
    assert "{" not in out


def test_reply_with_an_unsubstitutable_placeholder_is_suppressed_whole(tmp_path):
    """§8.3: полуотрендеренные реквизиты лиду уходить не должны. Подавляется
    ВЕСЬ ответ, а не вырезается плейсхолдер."""
    deps, _ = _deps(tmp_path, ["Напиши мені на {PHONE}, оплата {AMOUNT}"])
    _invoice(deps.store)
    tr = RecordingTransport()
    process_batch(CONTACT, ["куда платить?"], tr, deps)

    assert tr.sent == [], "ушёл ответ, в котором осталась служебная форма"
    assert deps.store.count_events("payments_placeholder_unresolved", since_ts=0) == 1, \
        "подавили молча — «бот замолчал» осталось бы без объяснения (DEV-18)"


# ── control_bot: тап 💰 закрывает СЧЁТ, а не только воронку ────────────────

def test_owner_tap_attaches_the_payment_to_the_open_invoice(tmp_path):
    """Без этой проводки блок счёта продолжал бы твердить «оплати ще немає»
    после того, как владелица подтвердила оплату."""
    deps, _ = _deps(tmp_path, ["ок"])
    row = _invoice(deps.store)
    _tap(deps.store)

    inv = deps.store.get_invoice(row["invoice_id"])
    assert inv["status"] == "paid"
    assert inv["first_payment_ts"] == NOW
    assert deps.store.received_minor(row["invoice_id"]) == 90000


def test_two_taps_on_one_card_stay_one_payment(tmp_path):
    """Идемпотентность денег (§8.5 п.18) не должна пострадать от привязки
    к счёту: ключ по-прежнему `tap:<msg_id>`."""
    deps, _ = _deps(tmp_path, ["ок"])
    row = _invoice(deps.store)
    _tap(deps.store)
    _tap(deps.store)
    assert deps.store.received_minor(row["invoice_id"]) == 90000


def test_tap_without_an_open_invoice_still_records_the_payment(tmp_path):
    """Ручной путь оплаты УЖЕ в проде и остаётся рабочим: счёт — новая
    возможность, а не новое условие."""
    deps, _ = _deps(tmp_path, ["ок"])
    deps.store.get_or_create_contact(CONTACT)
    res = _tap(deps.store, "paidamt2:50000:USD:lead:demo", card_msg_id=78)
    assert res.answer
    assert deps.store.count_events("payment", since_ts=0) >= 1


def test_paid_invoice_disappears_from_the_prompt(tmp_path):
    deps, llm = _deps(tmp_path, ["Добрий день!"])
    _invoice(deps.store)
    _tap(deps.store)
    process_batch(CONTACT, ["привет"], RecordingTransport(), deps)
    assert BLOCK_HEAD not in (llm.calls[0]["uncached_suffix"] or "")


# ── долг по счёту: заводит КОД, закрывает КОД (дубль 12.08) ────────────────

def _debt(store, invoice_id):
    from chatter.core.obligations_slot import invoice_okey
    key = invoice_okey(invoice_id)
    return next((o for o in store.get_obligations(CONTACT) if o.okey == key), None)


def test_payment_closes_the_code_owned_invoice_debt(tmp_path):
    """Долг на клиенте должен ЗАКРЫВАТЬСЯ деньгами, иначе запрет модели трогать
    `other` оставил бы его открытым навсегда: завести некому, закрыть нечем."""
    deps, _ = _deps(tmp_path, ["ок"])
    row = _invoice(deps.store)
    deps.store.save_obligations(CONTACT, merge_obligations(
        deps.store.get_obligations(CONTACT),
        [{"kind": "other", "owed_by": "client", "status": "open",
          "slug": invoice_slug(row["invoice_id"]),
          "detail": f"оплата рахунку {row['invoice_id']}"}],
        now=NOW, current_msg_id=1))
    assert _debt(deps.store, row["invoice_id"]).status == "open"

    _tap(deps.store)

    closed = _debt(deps.store, row["invoice_id"])
    assert closed is not None, "строка долга исчезла — её положено ЗАКРЫТЬ, не стереть"
    assert closed.status == "delivered", (
        "счёт оплачен, а долг на клиенте всё ещё открыт — блок обязательств "
        "будет дожимать оплату, которая уже пришла")


def test_a_partially_paid_invoice_keeps_the_debt_open(tmp_path):
    """Закрывают деньги, а не сам факт тапа: недоплата долг не снимает."""
    deps, _ = _deps(tmp_path, ["ок"])
    row = _invoice(deps.store)
    deps.store.save_obligations(CONTACT, merge_obligations(
        deps.store.get_obligations(CONTACT),
        [{"kind": "other", "owed_by": "client", "status": "open",
          "slug": invoice_slug(row["invoice_id"]),
          "detail": f"оплата рахунку {row['invoice_id']}"}],
        now=NOW, current_msg_id=1))

    _tap(deps.store, "paidamt2:50000:USD:lead:demo", card_msg_id=79)

    assert _debt(deps.store, row["invoice_id"]).status == "open"
