# -*- coding: utf-8 -*-
"""Ф0, §8.2: СКВОЗНОЙ ПУТЬ обеих операций — от входящего сообщения лида до
финального текста, который реально ушёл в транспорт.

Почему путь целиком, а не по кускам. Ф0 был написан, покрыт 1662 зелёными
тестами и при этом НЕ ПОДКЛЮЧЁН: `assess_complexity` не имел ни одного
вызывателя, счёт не создавал никто, блок реквизитов в промпт не попадал.
Каждый кусок был зелёным, путь не существовал. Это четвёртый такой случай в
проекте (silent-pult, слепые сторожа, слепая фикстура), поэтому здесь гейт
приёмки — ровно путь, и ни один тест этого файла не имеет права быть зелёным,
пока звено между лидом и отправленным текстом отсутствует.

Каждый тест держит ОБА конца одновременно:
  * промпт, ушедший в модель, реально несёт инструкцию (иначе живая модель
    никогда не написала бы плейсхолдер, и скриптованный ответ был бы фикцией);
  * текст, ушедший в транспорт, реально несёт подставленное значение.
Проверять только второй конец — значит проверять скрипт FakeLLM, а не путь
(урок «логировать ОБА конца: посчитано ≠ доехало»).
"""
from __future__ import annotations

import random
import shutil
from dataclasses import replace
from pathlib import Path

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.notify.base import CardHandle
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.base import Transport

SRC = Path(__file__).resolve().parents[2] / "chatter" / "clients"
NOW = 1_786_000_000.0            # чт 2026-08-06 10:06 Киев
DUE_TEXT = "10:00 в воскресенье, 9 августа"   # +72 ч, пол часа вниз, demo = ru
CONTACT = "lead:demo"
IBAN = "IBAN UA00 1234 5678 9012"

PAYMENTS_BLOCK = """
payments:
  enabled: true
  due_hours: 72
  channels:
    - id: iban_main
      kind: bank_transfer
      mode: manual
      currency: USD
      requisites_template: iban_main
      display: "Банковский перевод"
  pricing:
    amount_source: price_upper
    positions:
      logo:
        title: "Создание логотипа"
        currency: USD
        aliases: ["логотип", "лого"]
        price_range: [300, 400]
        ladder:
          - {amount: 400, scope_key: logo_full}
          - {amount: 300, scope_key: logo_floor}
      presentation:
        title: "Создание презентации"
        currency: USD
        aliases: ["презентац"]
        price_range: [100, 200]
        ladder:
          - {amount: 200, scope_key: pres_full}
          - {amount: 100, scope_key: pres_floor}
  scope_texts:
    logo_full: {text: "логотип с вариациями"}
    logo_floor: {text: "логотип в одном варианте"}
    pres_full: {text: "презентация полного объёма"}
    pres_floor: {text: "сокращённая презентация"}
"""
# Границы вилки обязаны быть в knowledge ЛИТЕРАЛОМ (правило №5): цену, которой
# клиент нигде не публиковал, бот назвать не может даже из своего же конфига.
# Поэтому «400» в промпте — норма (оно опубликовано), а «400 USD» — нет: это уже
# значение подстановки, и его в модели быть не должно.
KNOWLEDGE_PRICES = ("\n## Логотип\n- Создание логотипа: $300–$400."
                    "\n- Создание презентации: $100–$200.\n")
REQUISITES = f"templates:\n  iban_main:\n    body: '{IBAN}'\n"
# Состояние дрила (§8.3-бис): реквизиты владельца есть, клиентских ещё нет.
# Старт разрешён, но живой лид обязан получить ОТКАЗ, а не тестовый IBAN.
REQUISITES_TEST_ONLY = "test_templates:\n  iban_main:\n    body: 'IBAN TEST 0000'\n"


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


def _clients(tmp_path, *, requisites: str = REQUISITES) -> Path:
    dst = tmp_path / "clients"
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns(".versions"))
    p = dst / "demo" / "settings.yaml"
    p.write_text(p.read_text(encoding="utf-8") + PAYMENTS_BLOCK, encoding="utf-8")
    k = dst / "demo" / "knowledge.md"
    k.write_text(k.read_text(encoding="utf-8") + KNOWLEDGE_PRICES, encoding="utf-8")
    (dst / "demo" / "requisites.yaml").write_text(requisites, encoding="utf-8")
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


def _prompt(llm) -> str:
    """Всё, что реально увидела модель ответа: и стабильный префикс, и хвост."""
    return " ".join((c["system"] or "") + (c["uncached_suffix"] or "")
                    for c in llm.calls if c["tag"] == "brain")


# ── операция А: «дать реквизиты» ───────────────────────────────────────────

def test_lead_asking_where_to_pay_gets_the_requisites_in_the_same_turn(tmp_path):
    """Капкан №2 (§8.1). До Ф0 лид получал отсылку к владелице; путь обязан
    отдать реквизиты САМ, без счёта, без суммы, без утверждения."""
    deps, llm = _deps(tmp_path, ["Конечно! Перевод на {REQUISITES} 🙂"])
    tr = RecordingTransport()

    process_batch(CONTACT, ["Куда вам платить?"], tr, deps)

    prompt = _prompt(llm)
    assert "{REQUISITES}" in prompt, \
        "модель не получила инструкцию про плейсхолдер — живая модель его не напишет"
    assert IBAN not in prompt, \
        "реквизиты уехали в модель: один поправленный символ IBAN = чужие деньги (10.5)"

    out = " ".join(tr.sent)
    assert IBAN in out, "реквизиты не доехали до лида — путь оборван"
    assert "{" not in out, "служебная форма в лицо клиенту"


def test_requisites_reach_the_lead_byte_for_byte_from_the_config(tmp_path):
    """Правило №4. Сверка ПОБАЙТОВАЯ, а не «похоже»: пробел или переставленный
    символ в IBAN — это деньги, ушедшие не туда."""
    deps, _ = _deps(tmp_path, ["Реквизиты: {REQUISITES}"])
    tr = RecordingTransport()

    process_batch(CONTACT, ["Куда вам платить?"], tr, deps)

    body = (tmp_path / "clients" / "demo" / "requisites.yaml").read_text(
        encoding="utf-8").split("body: ")[1].strip().strip("'")
    assert body in " ".join(tr.sent)


def test_a_live_lead_never_gets_the_test_requisites(tmp_path):
    """§8.3-бис пп.2–3. Книга клиента пуста, заполнены только тестовые —
    состояние дрила. Живой лид обязан получить ОТКАЗ (ответ подавлен целиком),
    а не чужой IBAN: «заглянуть и не взять» — на одну правку от «заглянуть и
    взять»."""
    deps, _ = _deps(tmp_path, ["Перевод на {REQUISITES}"],
                    requisites=REQUISITES_TEST_ONLY)
    tr = RecordingTransport()

    process_batch(CONTACT, ["Куда вам платить?"], tr, deps)

    assert tr.sent == [], "тестовые реквизиты уехали живому лиду"
    assert deps.store.count_events("payments_placeholder_unresolved", since_ts=0) == 1, \
        "подавили молча — «бот замолчал» осталось бы без объяснения (DEV-18)"


# ── операция Б: «выставить счёт» ───────────────────────────────────────────

def test_ready_lead_gets_amount_requisites_and_due_in_the_same_turn(tmp_path):
    """Путь целиком: разбор запроса → котировка по ВЕРХНЕЙ границе вилки →
    счёт с одной ступенью → блок в промпт → ответ модели с плейсхолдерами →
    guardrails → подстановка → текст лиду."""
    deps, llm = _deps(
        tmp_path,
        ["Отлично! Сумма {AMOUNT}, реквизиты {REQUISITES}, оплата до {DUE} 🙂"])
    tr = RecordingTransport()

    process_batch(CONTACT, ["Давайте начнём с логотипом. Куда платить?"], tr, deps)

    out = " ".join(tr.sent)
    assert "400" in out, "верхняя граница вилки не доехала до лида"
    assert IBAN in out
    assert DUE_TEXT in out, "срок без часа — требование владельца §8.4 не выполнено"

    # §8.5 п.3. Сверяем именно ЗНАЧЕНИЯ ПОДСТАНОВКИ: «400» само по себе в
    # промпте законно — оно опубликовано в knowledge, иначе бот не имел бы права
    # его называть (правило №5). Незаконно, если в модель уехало то, что
    # подставляет код.
    prompt = _prompt(llm)
    for value in (IBAN, "400 USD", DUE_TEXT):
        assert value not in prompt, f"значение подстановки {value!r} ушло в модель"


def test_ready_lead_turn_creates_the_invoice_object_itself(tmp_path):
    """Текст мог бы совпасть и без объекта счёта — тогда «оплачено» нечему
    случиться, а остаток считать не из чего. Проверяем, ПОЧЕМУ текст такой."""
    deps, _ = _deps(tmp_path, ["Сумма {AMOUNT} на {REQUISITES} до {DUE}"])

    process_batch(CONTACT, ["Давайте начнём с логотипом. Куда платить?"],
                  RecordingTransport(), deps)

    invoices = deps.store.invoices_for(contact_id=CONTACT)
    assert len(invoices) == 1, "счёт не создан — операция Б не подключена"
    inv = invoices[0]
    assert inv["status"] == "issued"
    assert inv["amount_total"] == 40000, "сумма не минорная или не верх вилки"
    assert inv["currency"] == "USD"
    assert inv["amount_source"] == "price_upper"
    assert inv["created_by"] == "bot"
    assert inv["requisites_ref"] == "iban_main"
    assert len(deps.store.invoice_stages(inv["invoice_id"])) == 1, \
        "Ф0 пишет РОВНО одну ступень (§14 п.3)"

    quotes = deps.store.quotes_for(CONTACT)
    assert len(quotes) == 1 and quotes[0]["status"] == "active"
    assert quotes[0]["position_id"] == "logo" and quotes[0]["step_idx"] == 0
    assert inv["quote_id"] == quotes[0]["quote_id"], "счёт не привязан к котировке"


def test_the_invoice_turn_puts_the_debt_on_the_client(tmp_path):
    """§8.2 п.8 + контракт T2: ожидание оплаты — долг КЛИЕНТА, не бота."""
    deps, _ = _deps(tmp_path, ["Сумма {AMOUNT} на {REQUISITES} до {DUE}"])

    process_batch(CONTACT, ["Давайте начнём с логотипом. Куда платить?"],
                  RecordingTransport(), deps)

    inv = deps.store.invoices_for(contact_id=CONTACT)[0]
    obligations = deps.store.get_obligations(CONTACT)
    mine = [o for o in obligations if o.okey == f"other:inv-{inv['invoice_id']}"]
    assert mine, "обязательства по счёту нет — о долге не узнает ни бот, ни модель"
    assert mine[0].owed_by == "client"
    assert mine[0].status == "open"


def test_a_price_question_alone_quotes_but_issues_no_invoice(tmp_path):
    """Решение владельца: счёт рождает ЯВНАЯ готовность платить. Вопрос цены —
    это котировка и оговорка, без срока и без долга."""
    deps, _ = _deps(tmp_path, ["Ориентировочно {AMOUNT}. Точную сумму зафиксируем в счёте"])
    tr = RecordingTransport()

    process_batch(CONTACT, ["Сколько стоит логотип?"], tr, deps)

    assert "400" in " ".join(tr.sent)
    assert deps.store.invoices_for(contact_id=CONTACT) == []
    assert len(deps.store.quotes_for(CONTACT)) == 1
    assert deps.store.get_obligations(CONTACT) == []


def test_two_services_in_one_request_go_to_the_owner_but_requisites_still_fly(tmp_path):
    """§5.2, приёмка §8.5 п.7. Гейт сложности не распространяется на реквизиты:
    решение владельца 4 — реквизиты не деньги."""
    deps, _ = _deps(tmp_path, ["Уточню сумму у руководителя. Реквизиты: {REQUISITES}"])
    tr = RecordingTransport()

    process_batch(CONTACT, ["Хочу логотип и презентацию. Куда платить?"], tr, deps)

    inv = deps.store.invoices_for(contact_id=CONTACT)
    assert len(inv) == 1 and inv[0]["status"] == "awaiting_owner"
    assert inv[0]["amount_total"] is None, "сумма названа в обход гейта"
    assert IBAN in " ".join(tr.sent), "реквизиты подпали под гейт суммы (§5.2)"


def test_unparsed_request_calls_the_owner_instead_of_guessing(tmp_path):
    """§2.4: дефолт закрыт ОТКАЗОМ. Нет разбора — зовём владельца, а не
    «наверное просто» (урок P17)."""
    deps, _ = _deps(tmp_path, ["Уточню у руководителя. Реквизиты: {REQUISITES}"])

    process_batch(CONTACT, ["Готов оплатить, куда платить?"],
                  RecordingTransport(), deps)

    inv = deps.store.invoices_for(contact_id=CONTACT)
    assert len(inv) == 1 and inv[0]["status"] == "awaiting_owner"


def test_a_repeated_ask_does_not_stack_a_second_invoice(tmp_path):
    """Ф0 держит не больше ОДНОГО открытого счёта на контакт. Лид, повторивший
    вопрос, обязан получить тот же счёт, а не второй: два открытых счёта на
    одну работу — это два разных «сколько я должен» у одного клиента."""
    deps, _ = _deps(tmp_path, ["Сумма {AMOUNT} на {REQUISITES} до {DUE}",
                               "Ещё раз: {AMOUNT} на {REQUISITES} до {DUE}"])
    text = ["Давайте начнём с логотипом. Куда платить?"]

    process_batch(CONTACT, text, RecordingTransport(), deps)
    first = deps.store.invoices_for(contact_id=CONTACT)
    process_batch(CONTACT, text, RecordingTransport(), deps)

    assert len(first) == 1
    assert deps.store.invoices_for(contact_id=CONTACT) == first


def test_the_invoice_carries_the_snapshot_of_what_was_actually_sent(tmp_path):
    """§14 п.7: в счёте лежит отрендеренный текст инструкции на момент выдачи.
    В Ф2 ссылка провайдера персональна и с TTL — восстановить её задним числом
    будет нечем, и место для неё обязано существовать уже сейчас."""
    deps, _ = _deps(tmp_path, ["Сумма {AMOUNT} на {REQUISITES} до {DUE}"])

    process_batch(CONTACT, ["Давайте начнём с логотипом. Куда платить?"],
                  RecordingTransport(), deps)

    inv = deps.store.invoices_for(contact_id=CONTACT)[0]
    assert inv["instruction_snapshot"] == IBAN


def test_an_amount_without_the_disclaimer_never_reaches_the_lead(tmp_path):
    """Приёмка §8.5 п.4. «Це коштує $400» — оферта: клиент вправе требовать
    именно её после пересчёта. Подавляется ВЕСЬ ответ, как и полуотрендеренные
    реквизиты, — «почти оговорка» защищает ровно настолько, насколько её нет."""
    deps, _ = _deps(tmp_path, ["Це коштує {AMOUNT}, беремо?"])
    tr = RecordingTransport()

    process_batch(CONTACT, ["Сколько стоит логотип?"], tr, deps)

    assert tr.sent == [], "сумма без оговорки ушла лиду"
    assert deps.store.count_events("payments_disclaimer_missing", since_ts=0) == 1


def test_the_same_amount_with_the_disclaimer_goes_through(tmp_path):
    """Различающая пара к предыдущему: подавление обязано ловить отсутствие
    оговорки, а не сумму как таковую."""
    deps, _ = _deps(tmp_path, ["Орієнтовно {AMOUNT} — точну зафіксуємо в рахунку"])
    tr = RecordingTransport()

    process_batch(CONTACT, ["Сколько стоит логотип?"], tr, deps)

    assert "400" in " ".join(tr.sent)
    assert deps.store.count_events("payments_disclaimer_missing", since_ts=0) == 0


def test_awaiting_owner_invoice_reaches_the_owner_as_a_card(tmp_path):
    """Счёт, который бот не может назвать, обязан дойти до владелицы: иначе лид
    ждёт сумму, а о его готовности платить не знает никто."""
    deps, _ = _deps(tmp_path, ["Уточню сумму. Реквизиты: {REQUISITES}"])
    sent = []

    class FakeNotifier:
        def notify(self, card):
            sent.append(card)
            return CardHandle(ref="777:42")

        def update_card(self, handle, card):
            return True

    deps.notifier = FakeNotifier()

    process_batch(CONTACT, ["Хочу логотип и презентацию. Куда платить?"],
                  RecordingTransport(), deps)

    cards = [c for c in sent if c.kind == "invoice_awaiting_owner"]
    assert len(cards) == 1, "владелица не узнала о счёте"
    inv = deps.store.invoices_for(contact_id=CONTACT)[0]
    assert inv["invoice_id"] in cards[0].text_html
    assert "multiple_services" in cards[0].text_html, \
        "карточка без причины бесполезна: непонятно, что решать"


def test_a_rate_limited_turn_creates_no_money_object(tmp_path):
    """Счёт, выставленный на ходу, который бот всё равно не отправит, — это
    идущий срок и записанный долг при молчащем боте."""
    deps, _ = _deps(tmp_path, ["Здравствуйте! Чем помочь?",
                               "Сумма {AMOUNT} на {REQUISITES} до {DUE}"])
    deps.cfg = replace(
        deps.cfg,
        settings=replace(deps.cfg.settings,
                         limits=replace(deps.cfg.settings.limits,
                                        per_contact_hourly=1)))

    process_batch(CONTACT, ["Привет"], RecordingTransport(), deps)
    process_batch(CONTACT, ["Давайте начнём с логотипом. Куда платить?"],
                  RecordingTransport(), deps)

    assert deps.store.invoices_for(contact_id=CONTACT) == []
