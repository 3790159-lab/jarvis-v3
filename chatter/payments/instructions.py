"""Реквизиты и платёжные инструкции (спека §8.3, §14 п.7).

Форма `PaymentInstruction` — КОНЕЧНАЯ: в Ф0 заполняется только config-ветка, но
поля `link`/`expires_ts`/`source=provider` уже есть. В Ф2 ссылка провайдера
персональна и живёт с TTL, а сеть в hot-path запрещена (§1.3) — значит рендер
ответа обязан брать готовый снапшот, а не ходить за ним. Если бы Ф0 отдавал
голую строку, этот стык пришлось бы переписывать вместе со всеми вызывателями.

Источник тела подписан в `source`. Это не украшение: инвариант
«source == 'test' ⇒ контакт дрил-овый» — то, что проверяется перебором в тестах
и ловит любую будущую правку резолвера, открывающую утечку.
"""
from __future__ import annotations

from dataclasses import dataclass

from chatter.payments.drill_gate import guard_test_asset, is_drill_contact

CHANNEL_MODES: tuple[str, ...] = ("manual", "auto")


class RequisitesError(Exception):
    """Конфиг канала/книги реквизитов непригоден."""


class RequisitesUnavailable(RequisitesError):
    """Выдать реквизиты нечем — ОТКАЗ.

    Отдельный тип, потому что вызыватель обязан обработать это иначе, чем
    ошибку конфига: фича молчит (безопасный текст владельцу/лиду), но ничего
    не подставляет. «Что-нибудь вместо реквизитов» хуже, чем ничего."""


@dataclass(frozen=True)
class Channel:
    id: str
    kind: str                    # wise | paypal | liqpay | monobank | bank_transfer
    mode: str                    # manual | auto (§14 п.9; auto — не Ф0)
    currency: str                # ISO-4217
    requisites_template: str
    display: str


@dataclass(frozen=True)
class PaymentInstruction:
    channel_id: str
    kind: str
    display: str
    body_text: str
    requisites_ref: str
    source: str                  # config | test | provider
    link: str | None = None      # Ф2
    expires_ts: float | None = None   # Ф2


@dataclass(frozen=True)
class RequisitesBook:
    """Две РАЗДЕЛЬНЫЕ книги. Разделение структурное, а не по флажку внутри
    записи: перепутать «клиентские» и «тестовые» тогда можно только правкой
    ключа верхнего уровня, а не опечаткой в булеве."""
    templates: dict[str, str]
    test_templates: dict[str, str]


def _body(raw: dict, key: str, where: str) -> str:
    entry = raw.get(key)
    if entry is None:
        return ""
    if isinstance(entry, str):
        body = entry
    elif isinstance(entry, dict):
        body = entry.get("body") or ""
    else:
        raise RequisitesError(f"{where}[{key!r}]: ожидалась строка или словарь с body")
    if not isinstance(body, str):
        raise RequisitesError(f"{where}[{key!r}]: body обязан быть строкой")
    # Пустое тело равно отсутствующему: пробел в конфиге не имеет права
    # превратиться в «Реквізити: » в живом диалоге.
    return body.strip()


def load_requisites(raw: dict) -> RequisitesBook:
    if not isinstance(raw, dict):
        raise RequisitesError("книга реквизитов обязана быть словарём")
    tpl_raw = raw.get("templates") or {}
    test_raw = raw.get("test_templates") or {}
    for name, block in (("templates", tpl_raw), ("test_templates", test_raw)):
        if not isinstance(block, dict):
            raise RequisitesError(f"{name} обязан быть словарём")
    templates = {k: _body(tpl_raw, k, "templates") for k in tpl_raw}
    test_templates = {k: _body(test_raw, k, "test_templates") for k in test_raw}
    return RequisitesBook(templates, test_templates)


def resolve_instruction(*, channel: Channel, book: RequisitesBook,
                        contact_id: str) -> PaymentInstruction:
    """Собрать платёжную инструкцию для контакта.

    Порядок источников жёсткий:
    1. дрил-контакт — test_templates, если есть;
    2. любой контакт — templates клиента;
    3. ничего нет — `RequisitesUnavailable` (отказ, не пустая строка).

    Живой контакт в test_templates НЕ ЗАГЛЯДЫВАЕТ ВООБЩЕ. Это важнее, чем
    кажется: «заглянуть и не взять» — на одну правку от «заглянуть и взять»."""
    if channel.mode not in CHANNEL_MODES:
        raise RequisitesError(
            f"канал {channel.id!r}: mode={channel.mode!r}, допустимы {CHANNEL_MODES}")
    if channel.mode == "auto":
        # §14 п.9: поле обязательно уже в Ф0, реализация — Ф2. Явный отказ, а не
        # тихая деградация в manual: обещание автоподтверждения на мёртвом
        # канале — риск 10.3.
        raise RequisitesError(
            f"канал {channel.id!r}: mode=auto не реализован в Ф0 "
            f"(провайдерские интеграции — Ф2)")

    key = channel.requisites_template

    if is_drill_contact(contact_id):
        test_body = book.test_templates.get(key, "")
        if test_body:
            # Гейт зовём даже здесь, хотя контакт уже проверен: единственная
            # точка, где решение «можно» принимается, — сам гейт.
            guard_test_asset(contact_id=contact_id, what=f"test-реквизиты {key!r}")
            return PaymentInstruction(
                channel_id=channel.id, kind=channel.kind, display=channel.display,
                body_text=test_body, requisites_ref=key, source="test")

    body = book.templates.get(key, "")
    if not body:
        raise RequisitesUnavailable(
            f"канал {channel.id!r}: реквизитов клиента по ключу {key!r} нет "
            f"(или они пусты) — выдавать нечего. Фича молчит; подставлять "
            f"что-либо вместо них запрещено (правило №4)")

    return PaymentInstruction(
        channel_id=channel.id, kind=channel.kind, display=channel.display,
        body_text=body, requisites_ref=key, source="config")
