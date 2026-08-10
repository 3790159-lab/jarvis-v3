"""Секция `payments` конфига клиента и валидатор СТАРТА (спека §5.1, §8.3-бис).

Решение владельца 5: фича работает из коробки. Конфиг, при котором бот молчит
про реквизиты, считается СЛОМАННЫМ, а не осторожным, — поэтому здесь нет ни
одного молчаливого дефолта: любое расхождение это исключение, и на старте оно
роняет клиента, а не откладывает сюрприз до живого диалога (урок P17).

`enabled` в файле — ФАКТ, а не цель. Значение по умолчанию `false` не осторожность
и не противоречие решению 5: гардиан деплоит из рабочего дерева, и включённая в
файле фича поднялась бы на ребуте без команды владельца — ровно та же причина,
что у `funnel_gate`. Включение — команда пульта `/payments on confirm`.

Валидатор — сторож, а не документация. Различаются ДВА состояния «включено»:

  * **клиентское** — есть канал с реквизитами КЛИЕНТА; фича работает у всех;
  * **дрил-овое** — реквизиты только тестовые (§8.3-бис: их даёт владелец до
    того, как клиент прислал свои). Старт разрешён, иначе живой прогон Ф0
    невозможен вовсе, — но он ГРОМКИЙ: живой лид получит отказ, а не реквизиты,
    и владелец обязан узнать это из лога, а не от клиента.

Состояние «включено и нечем ответить НИКОМУ» — ошибка старта.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from chatter.payments.instructions import (
    CHANNEL_MODES, Channel, RequisitesBook, RequisitesError, load_requisites)
from chatter.payments.money import MINOR_EXPONENT, Money, MoneyError, from_major
from chatter.payments.pricing import Pricing, PricingConfigError, load_pricing
from chatter.payments.scope import ScopeConfigError, ScopeText, load_scope_texts

log = logging.getLogger(__name__)

# Рабочие дефолты §5.1. Живут ЗДЕСЬ и только здесь: продублированные числами в
# загрузчике, они разъезжаются при следующей правке одного без другого.
DEFAULT_ESCALATE_ON_COMPLEXITY = True
DEFAULT_DAILY_INVOICE_CAP = 20
DEFAULT_PER_CONTACT_INVOICE_CAP = 3
DEFAULT_DUE_HOURS = 72

_ALLOWED_KEYS = frozenset({
    "enabled", "escalate_on_complexity", "owner_approval_above",
    "daily_invoice_cap", "per_contact_invoice_cap", "due_hours",
    "channels", "pricing", "scope_texts",
})

_CHANNEL_KEYS = frozenset({
    "id", "kind", "mode", "currency", "requisites_template", "display",
    # Ф2: ссылка на секрет провайдера. Ключ известен уже в Ф0, чтобы конфиг с
    # merchant-каналом не падал на «неизвестный ключ» при первой же интеграции.
    "secret_ref",
})

_POSITIVE_INT_FIELDS = ("daily_invoice_cap", "per_contact_invoice_cap", "due_hours")


class PaymentsConfigError(Exception):
    """Секция payments непригодна. Всегда ошибка СТАРТА, никогда предупреждение."""


@dataclass(frozen=True)
class PaymentsConfig:
    enabled: bool = False
    escalate_on_complexity: bool = DEFAULT_ESCALATE_ON_COMPLEXITY
    owner_approval_above: Money | None = None
    daily_invoice_cap: int = DEFAULT_DAILY_INVOICE_CAP
    per_contact_invoice_cap: int = DEFAULT_PER_CONTACT_INVOICE_CAP
    due_hours: int = DEFAULT_DUE_HOURS
    channels: tuple[Channel, ...] = ()
    pricing: Pricing | None = None
    scope_texts: Mapping[str, ScopeText] = field(default_factory=dict)
    requisites: RequisitesBook = field(
        default_factory=lambda: RequisitesBook({}, {}))


def _bool(raw: dict, key: str, default: bool) -> bool:
    val = raw.get(key, default)
    # Строгий bool, а не bool(val): `enabled: "no"` — строка, и bool("no") это
    # True. На тумблере денег молчаливое приведение типов означает включённую
    # фичу там, где владелец писал «выключено».
    if not isinstance(val, bool):
        raise PaymentsConfigError(
            f"payments.{key}={val!r}: ожидался true/false, а не {type(val).__name__} — "
            f"приведение типов на тумблере денег запрещено")
    return val


def _positive_int(raw: dict, key: str, default: int) -> int:
    val = raw.get(key, default)
    if isinstance(val, bool) or not isinstance(val, int):
        raise PaymentsConfigError(f"payments.{key}={val!r}: ожидалось целое число")
    if val <= 0:
        raise PaymentsConfigError(
            f"payments.{key}={val}: обязано быть > 0. Ноль не «запретить всё», "
            f"а выключить фичу молча — владелец узнал бы об этом от клиента")
    return val


def _threshold(raw: dict) -> Money | None:
    val = raw.get("owner_approval_above")
    if val is None:
        return None
    if not isinstance(val, dict) or set(val) != {"amount", "currency"}:
        raise PaymentsConfigError(
            "payments.owner_approval_above: ожидался {amount, currency}. Голое "
            "число означало бы «в какой-то валюте», а порог, сравниваемый не с "
            "той валютой, — backstop, который молча не срабатывает")
    ccy = val["currency"]
    if ccy not in MINOR_EXPONENT:
        raise PaymentsConfigError(
            f"payments.owner_approval_above: currency={ccy!r} — нужен ISO-4217")
    try:
        return from_major(val["amount"], ccy)
    except MoneyError as exc:
        raise PaymentsConfigError(f"payments.owner_approval_above: {exc}") from exc


def _channel(raw, idx: int, seen: set[str]) -> Channel:
    if not isinstance(raw, dict):
        raise PaymentsConfigError(f"payments.channels[{idx}]: ожидался словарь")
    unknown = sorted(set(raw) - _CHANNEL_KEYS)
    if unknown:
        raise PaymentsConfigError(
            f"payments.channels[{idx}]: неизвестные ключи {', '.join(unknown)}")

    cid = raw.get("id")
    if not isinstance(cid, str) or not cid.strip():
        raise PaymentsConfigError(f"payments.channels[{idx}]: пустой id")
    cid = cid.strip()
    if cid in seen:
        # Счёт и кнопки адресуют канал по id; два канала с одним id означают,
        # что снапшот счёта ссылается неизвестно на какой из них.
        raise PaymentsConfigError(
            f"payments.channels[{idx}]: id {cid!r} повторяется")
    seen.add(cid)

    mode = raw.get("mode")
    if mode not in CHANNEL_MODES:
        raise PaymentsConfigError(
            f"канал {cid!r}: mode={mode!r}, допустимы {CHANNEL_MODES} (§14 п.9 — "
            f"поле обязательное, не выводимое)")

    ccy = raw.get("currency")
    if ccy not in MINOR_EXPONENT:
        raise PaymentsConfigError(
            f"канал {cid!r}: currency={ccy!r} — нужен ISO-4217 из "
            f"{sorted(MINOR_EXPONENT)}, а не символ (§14 п.8)")

    tpl = raw.get("requisites_template")
    if not isinstance(tpl, str) or not tpl.strip():
        raise PaymentsConfigError(
            f"канал {cid!r}: пустой requisites_template — выдавать нечего")

    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise PaymentsConfigError(f"канал {cid!r}: пустой kind")

    display = raw.get("display")
    if not isinstance(display, str) or not display.strip():
        # Лид видит именно это слово. Пустое → «оплатіть через » в живом диалоге.
        raise PaymentsConfigError(f"канал {cid!r}: пустой display")

    return Channel(id=cid, kind=kind.strip(), mode=mode, currency=ccy,
                   requisites_template=tpl.strip(), display=display.strip())


def load_payments(raw: dict | None, *, requisites_raw: dict | None,
                  knowledge: str) -> PaymentsConfig:
    """Разобрать и ПРОВЕРИТЬ секцию `payments` + книгу реквизитов.

    Блока нет вовсе — это не ошибка: клиент, не писавший payments, обязан
    стартовать. Ошибка — блок, который есть и не сходится."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PaymentsConfigError("секция payments обязана быть словарём")
    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        # Молча проигнорированный ключ = настройка, которая не применилась, и
        # вопрос «почему бот не так считает», на который нечем ответить (DEV-18).
        raise PaymentsConfigError(
            f"payments: неизвестные ключи {', '.join(unknown)} "
            f"(известные: {', '.join(sorted(_ALLOWED_KEYS))})")

    channels_raw = raw.get("channels") or []
    if not isinstance(channels_raw, (list, tuple)):
        raise PaymentsConfigError("payments.channels обязан быть списком")
    seen: set[str] = set()
    channels = tuple(_channel(c, i, seen) for i, c in enumerate(channels_raw))

    pricing: Pricing | None = None
    if raw.get("pricing") is not None:
        try:
            pricing = load_pricing(raw["pricing"], knowledge=knowledge)
        except PricingConfigError as exc:
            raise PaymentsConfigError(f"payments.pricing: {exc}") from exc

    try:
        scope_texts = load_scope_texts(raw.get("scope_texts") or {})
    except ScopeConfigError as exc:
        raise PaymentsConfigError(f"payments.scope_texts: {exc}") from exc

    try:
        requisites = load_requisites(requisites_raw or {})
    except RequisitesError as exc:
        raise PaymentsConfigError(f"requisites.yaml: {exc}") from exc

    return PaymentsConfig(
        enabled=_bool(raw, "enabled", False),
        escalate_on_complexity=_bool(raw, "escalate_on_complexity",
                                     DEFAULT_ESCALATE_ON_COMPLEXITY),
        owner_approval_above=_threshold(raw),
        daily_invoice_cap=_positive_int(raw, "daily_invoice_cap",
                                        DEFAULT_DAILY_INVOICE_CAP),
        per_contact_invoice_cap=_positive_int(raw, "per_contact_invoice_cap",
                                              DEFAULT_PER_CONTACT_INVOICE_CAP),
        due_hours=_positive_int(raw, "due_hours", DEFAULT_DUE_HOURS),
        channels=channels, pricing=pricing, scope_texts=scope_texts,
        requisites=requisites,
    )


def _has_body(book: Mapping[str, str], key: str) -> bool:
    # `load_requisites` уже обрезал пробелы, но проверка не полагается на это:
    # пустое тело обязано считаться отсутствующим в ЛЮБОМ случае.
    return bool((book.get(key) or "").strip())


def usable_channels(payments: PaymentsConfig) -> tuple[Channel, ...]:
    """Каналы, которыми можно ответить ХОТЬ КОМУ (клиенту или дрилу).

    `mode: auto` сюда не входит: в Ф0 он не реализован, и обещание
    автоподтверждения на нём — риск 10.3, а не «почти работает»."""
    return tuple(
        c for c in payments.channels
        if c.mode == "manual"
        and (_has_body(payments.requisites.templates, c.requisites_template)
             or _has_body(payments.requisites.test_templates, c.requisites_template)))


def client_ready_channels(payments: PaymentsConfig) -> tuple[Channel, ...]:
    """Каналы, которыми можно ответить ЖИВОМУ лиду — только книга клиента."""
    return tuple(
        c for c in payments.channels
        if c.mode == "manual"
        and _has_body(payments.requisites.templates, c.requisites_template))


def assert_startable(payments: PaymentsConfig, *, slug: str = "") -> None:
    """Валидатор СТАРТА (§5.1). Молчит при `enabled: false`.

    Выключенная фича не имеет права ронять старт: один кривой блок payments
    лишил бы клиента бота целиком. Включённая — обязана."""
    if not payments.enabled:
        return

    if not payments.channels:
        raise PaymentsConfigError(
            f"payments у клиента {slug!r}: enabled=true, но не задано ни одного "
            f"канала — включено и сказать нечего")

    usable = usable_channels(payments)
    if not usable:
        auto = [c.id for c in payments.channels if c.mode == "auto"]
        missing = sorted({c.requisites_template for c in payments.channels
                          if c.mode == "manual"})
        why = []
        if missing:
            why.append("нет тел реквизитов по ключам: " + ", ".join(missing))
        if auto:
            why.append("каналы mode=auto не работают в Ф0 (провайдеры — Ф2): "
                       + ", ".join(auto))
        raise PaymentsConfigError(
            f"payments у клиента {slug!r}: enabled=true, но ответить нечем — "
            + "; ".join(why))

    # Ступени без текста обмена ловим ЗДЕСЬ, а не в момент уступки: обнаружить
    # это на третьем шаге торга значит оборвать диалог в самом дорогом месте.
    # Названы сразу ВСЕ, а не первая — чинить по одной столько же заходов,
    # сколько ступеней.
    if payments.pricing is not None:
        orphan = sorted({
            f"{pos.position_id}:{step.scope_key}"
            for pos in payments.pricing.positions.values()
            for step in pos.steps
            if step.scope_key not in payments.scope_texts})
        if orphan:
            raise PaymentsConfigError(
                f"payments у клиента {slug!r}: ступени без текста объёма — "
                + ", ".join(orphan))

    client_ready = client_ready_channels(payments)
    if not client_ready:
        # Дрил-состояние. НЕ ошибка (иначе живой прогон Ф0 невозможен), но и не
        # тишина: живой лид получит отказ вместо реквизитов, и узнать об этом
        # владелец обязан из лога, а не от клиента.
        log.warning(
            "payments у клиента %s: ВКЛЮЧЕНО на ТЕСТОВЫХ реквизитах — каналы %s "
            "обслужат только дрил-контакты, живой лид получит отказ. Это "
            "состояние дрила (§8.3-бис), а не рабочее",
            slug or "?", ", ".join(c.id for c in usable))
