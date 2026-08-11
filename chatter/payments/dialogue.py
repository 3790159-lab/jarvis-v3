"""Проводка Ф0 в разговор: обе операции §8.2 на одном ходу.

Это то самое звено, которого не было. Модули арки — прайс, гейт сложности,
книга реквизитов, модель счёта — существовали и были зелёными, но между
сообщением лида и текстом ответа не стояло ничего: `assess_complexity` не имел
вызывателей, счёт не создавал никто, а блок в промпт попадал, только если счёт
уже существовал.

**Операция А — «дать реквизиты».** Работает всегда при `enabled`, без суммы, без
счёта, без утверждения владельца (решение владельца 4: реквизиты — не деньги).
Ровно это чинит капкан №2, и оно не зависит ни от чего остального.

**Операция Б — «выставить счёт».** Запускается ЯВНОЙ готовностью платить, а не
вопросом цены: вопрос цены даёт котировку и оговорку, счёт же несёт срок и долг.

Порядок жёсткий и он же причина, по которой разбор живёт до `brain.reply`:
    предпасс → котировка/счёт → блок в промпт → модель → guardrails → подстановка
Классификатор отвечает ПОСЛЕ реплики, поэтому фактами для денег он в Ф0 быть не
может — иначе цена доезжала бы до лида ходом позже.

Модуль пишет в хранилище и НЕ ходит в сеть. Ни одной цифры и ни одного символа
реквизитов он в промпт не отдаёт (§8.3): туда уходят только служебные
подстановки, а значения подставляет `run.py` уже после редакции (§14 п.16).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from chatter.core.obligations_slot import merge_obligations
from chatter.payments.complexity import (
    NeedsOwner, QuoteRequest, RequestedItem, Simple, assess_complexity)
from chatter.payments.drill_gate import NotForProduction
from chatter.payments.instructions import (
    PaymentInstruction, RequisitesError, RequisitesUnavailable, resolve_instruction)
from chatter.payments.intent import read_intent
from chatter.payments.money import Money, format_major
from chatter.payments.prompt import (
    due_at, format_due, pick_open_invoice, render_invoice_block,
    render_no_price_block, render_quote_block, render_requisites_block)
from chatter.payments.scope import ScopeConfigError, assert_pricing_usable
from chatter.payments.settings import PaymentsConfig, usable_channels

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OwnerNote:
    """Что владельцу нужно знать по деньгам ЭТОГО хода. Карточку собирает и шлёт
    `run.py`: у него notifier и язык пульта, здесь — только факт и причины."""
    kind: str                       # invoice_awaiting_owner
    invoice_id: str
    reasons: tuple[str, ...] = ()   # ключи REASONS либо один пояснительный текст


@dataclass(frozen=True)
class PaymentTurn:
    block: str = ""
    values: Mapping[str, str] = field(default_factory=dict)
    owner_note: OwnerNote | None = None
    # §2.2: названная сумма без оговорки — оферта. Требование живёт здесь, а
    # проверка — в `run.py` по итоговому тексту, потому что текст пишет модель.
    requires_disclaimer: bool = False


@dataclass(frozen=True)
class _Blocked:
    """Цену назвать нельзя не из-за запроса, а из-за конфига: тексты объёма —
    заглушки, а контакт живой (§8.3-бис п.4)."""
    why: str


def _instruction(payments: PaymentsConfig, contact_id: str) -> PaymentInstruction | None:
    """Реквизиты для контакта. `None` — выдать нечего; это штатный исход, а не
    ошибка, и подставлять вместо них что-либо запрещено (правило №4)."""
    channels = usable_channels(payments)
    if not channels:
        return None
    # Ф0: один канал. Перечисление нескольких — Ф1, и оно ничего не меняет в
    # гарантии §8.3, потому что тела реквизитов всё равно не идут в промпт.
    try:
        return resolve_instruction(channel=channels[0], book=payments.requisites,
                                   contact_id=contact_id)
    except RequisitesUnavailable as exc:
        # Штатный отказ (клиентских реквизитов ещё нет), но не молчаливый:
        # «бот перестал отвечать про оплату» обязано иметь объяснение (DEV-18).
        log.info("реквизиты для %s не выданы: %s", contact_id, exc)
    except (RequisitesError, NotForProduction) as exc:
        log.warning("реквизиты для %s не выданы: %s", contact_id, exc)
    return None


def _with_quote_fallback(request: QuoteRequest, store, contact_id: str) -> QuoteRequest:
    """«Ок, давайте почнемо» без названия услуги — это про то, что уже
    котировали. Позиция берётся из АКТИВНОЙ котировки, а не угадывается: у
    догадки здесь цена — счёт за не ту работу."""
    if request.items:
        return request
    active = [q for q in store.quotes_for(contact_id) if q["status"] == "active"]
    if not active:
        return request
    return QuoteRequest(
        items=(RequestedItem(position_id=active[-1]["position_id"], qty=1,
                             raw="(з попередньої котировки)"),),
        volume_note=request.volume_note, deadline_note=request.deadline_note,
        parsed=True)


def _verdict(payments: PaymentsConfig, request: QuoteRequest, contact_id: str):
    """Simple | NeedsOwner | _Blocked. Решение принимает КОД (§2.4)."""
    if payments.pricing is None:
        return NeedsOwner(("no_parse",))
    outcome = assess_complexity(request, payments.pricing)
    if isinstance(outcome, NeedsOwner):
        return outcome
    try:
        # Проверяем ВЕСЬ прайс, а не одну ступень: узнать про заглушку на третьем
        # шаге торга — значит оборвать диалог в самом дорогом месте.
        assert_pricing_usable(payments.pricing, payments.scope_texts,
                              contact_id=contact_id)
    except (ScopeConfigError, NotForProduction) as exc:
        return _Blocked(str(exc))
    return outcome


def _money_value(amount: Money) -> str:
    return f"{format_major(amount)} {amount.ccy}"


def _record_obligation(store, contact_id: str, invoice_id: str, *, now: float,
                       msg_id: int | None) -> None:
    """Долг оплаты — на КЛИЕНТЕ (§8.2 п.8, контракт T2). Ключ `other:inv-<id>`
    (§14 п.12): один активный ключ на счёт, иначе второй счёт затрёт первый.

    Пишется независимо от тумблера слота обязательств: тумблер решает, инъектить
    ли в промпт долги БОТА, а это долг клиента и он в промпт едет своим блоком."""
    store.save_obligations(contact_id, merge_obligations(
        store.get_obligations(contact_id),
        [{"kind": "other", "owed_by": "client", "status": "open",
          "slug": f"inv-{invoice_id}", "detail": f"оплата рахунку {invoice_id}"}],
        now=now, current_msg_id=msg_id))


def payment_turn(*, store, payments: PaymentsConfig, contact_id: str, text: str,
                 msg_id: int | None, now: float, language: str = "uk",
                 work_hours: tuple[int, int] = (9, 20),
                 knowledge_version: str = "") -> PaymentTurn:
    """Один ход разговора со стороны денег. Зовётся ДО генерации реплики."""
    if not payments.enabled:
        return PaymentTurn()

    instruction = _instruction(payments, contact_id)
    values: dict[str, str] = {}
    sections: list[str] = []
    if instruction is not None:
        values["REQUISITES"] = instruction.body_text
        sections.append(render_requisites_block([instruction.display]))

    invoice = pick_open_invoice(store.invoices_for(contact_id=contact_id))
    intent = read_intent(text, payments.pricing)
    owner_note: OwnerNote | None = None
    requires_disclaimer = False
    quote: Mapping | None = None

    # Операция Б. Счёта без реквизитов не бывает: «заплати невідомо куди до
    # четверга» — это идущий срок и записанный долг при неоплатимом счёте.
    if invoice is None and instruction is not None and (
            intent.wants_invoice or intent.asks_price):
        request = _with_quote_fallback(intent.request, store, contact_id)
        outcome = _verdict(payments, request, contact_id)

        if isinstance(outcome, Simple):
            position = payments.pricing.positions[outcome.position_id]
            step = position.top          # price_upper: верх вилки (§2.1)
            quote = store.create_quote(
                contact_id=contact_id, position_id=position.position_id,
                step_idx=0, amount=step.amount, scope_key=step.scope_key,
                amount_source="price_upper", knowledge_version=knowledge_version,
                origin_msg_id=msg_id, now=now)
            if intent.wants_invoice:
                invoice = store.create_invoice(
                    contact_id=contact_id, origin_msg_id=msg_id,
                    amount=step.amount, channel_id=instruction.channel_id,
                    due_ts=due_at(now, due_hours=payments.due_hours,
                                  work_hours=work_hours),
                    created_by="bot", amount_source="price_upper",
                    status="issued", now=now, quote_id=quote["quote_id"],
                    price_source=f"{position.position_id}@{knowledge_version}",
                    requisites_ref=instruction.requisites_ref,
                    # §14 п.7: в счёте лежит ТО, что реально отправили. В Ф2
                    # ссылка провайдера персональна и с TTL — восстановить её
                    # задним числом будет нечем.
                    instruction_snapshot=instruction.body_text)
                _record_obligation(store, contact_id, invoice["invoice_id"],
                                   now=now, msg_id=msg_id)
            else:
                requires_disclaimer = True

        elif intent.wants_invoice:
            # Лид готов платить, а мы не знаем за что либо не имеем права
            # называть сумму. Счёт заводится ПУСТЫМ и уходит владельцу: молчание
            # здесь было бы потерянной продажей, а догадка — не той ценой.
            invoice = store.create_invoice(
                contact_id=contact_id, origin_msg_id=msg_id, amount=None,
                channel_id=instruction.channel_id, due_ts=None,
                created_by="bot", amount_source=None, status="awaiting_owner",
                now=now, requisites_ref=instruction.requisites_ref,
                instruction_snapshot=instruction.body_text,
                currency=payments.channels[0].currency)
            reasons = (outcome.reasons if isinstance(outcome, NeedsOwner)
                       else (outcome.why,))
            owner_note = OwnerNote("invoice_awaiting_owner",
                                   invoice["invoice_id"], tuple(reasons))

    # Блок денег: счёт вытесняет котировку — у выставленного счёта сумма уже
    # зафиксирована, и «орієнтовно» рядом с ней приглашает спорить о решённом.
    if invoice is not None:
        sections.append(render_invoice_block(invoice, now=now))
        total = invoice.get("amount_total")
        if total is not None:
            values["AMOUNT"] = _money_value(Money(int(total), invoice["currency"]))
        if invoice.get("due_ts") is not None:
            values["DUE"] = format_due(invoice["due_ts"], language=language)
    elif quote is not None:
        sections.append(render_quote_block())
        values["AMOUNT"] = _money_value(
            Money(int(quote["amount_minor"]), quote["currency"]))
    elif intent.asks_price:
        sections.append(render_no_price_block())

    return PaymentTurn(
        block="\n\n".join(s for s in sections if s),
        values=values, owner_note=owner_note,
        requires_disclaimer=requires_disclaimer)
