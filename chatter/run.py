from __future__ import annotations
import argparse
import datetime as _dt
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from chatter.config.loader import HONESTY_HONEST, Config, ControlConfig, load_config
from chatter.core import humanizer as H
from chatter.core.brain import Brain
from chatter.core.window import estimate_tokens, select_window
from chatter.core.obligations_slot import (
    filter_model_updates, merge_obligations, render_current_for_classifier,
    render_slot_block,
)
from chatter.core.brand_safety import forbidden_mention
from chatter.core.classifier import (
    ClassifierResult, classifier_degraded, classifier_failure_count,
    note_classifier_error, note_classifier_recovered, note_profile_miss,
    reset_profile_miss,
)
from chatter.core.console import (
    console_text, contact_link, display_name, escalation_buttons, escape_html,
    format_escalation_card,
)
from chatter.core.disclosure import honest_disclosure, is_bot_question
from chatter.core.escalation import (
    advance_funnel, awaiting_owner_fallback, decide_escalation,
    deterministic_escalation, esc_active_key, mentions_owner_contact,
    honest_self_action_fallback, pick_non_repeating, self_action_fallback,
    suppressed_fallback,
)
from chatter.payments.dialogue import OwnerNote, PaymentTurn, payment_turn
from chatter.payments.prompt import (  # noqa: F401
    needs_caveat,
    UnsubstitutedPlaceholder, finalize, find_placeholders, has_disclaimer,
)
from chatter.core.guardrails import (
    redact_unbacked, within_daily_cap, within_hourly_limit,
)
from chatter.core.llm import AnthropicLLM, FakeLLM
from chatter.core.pause import is_attributed, is_muted
from chatter.notify.base import Card, CardHandle, Notifier
from chatter.storage.db import Store, usage_sink_for
from chatter.transport.base import Transport
from chatter.transport.fake import FakeConsoleTransport


def _obligations_enabled() -> bool:
    """Флаг арки обязательств (спека 2026-07-24 §12): default OFF → поведение
    БАЙТ-В-БАЙТ как до арки. Читаем env КАЖДЫЙ раз (не кэшируем на импорте),
    чтобы тесты и выкатка переключали без перезапуска процесса."""
    return os.getenv("CHATTER_OBLIGATIONS_SLOT", "").strip().lower() in (
        "1", "true", "yes", "on")

log = logging.getLogger("chatter.run")


@dataclass
class Deps:
    cfg: Config
    store: Store
    brain: Brain
    rng: random.Random
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    # Арка 3B (всё опционально → без них поведение как арки 3A/3B-off):
    notifier: Notifier | None = None
    # (history, profile) -> результат; профиль лида идёт в промпт классификатора
    classify: Callable[[list[dict], str | None], ClassifierResult] | None = None
    escalation_keywords: list[str] = field(default_factory=list)
    # Раннер (Telethon) даёт билдер карточки с кликабельным ИМЕНЕМ/ссылкой
    # (резолв entity живёт на loop). Без него — текстовый фоллбек по contact_id.
    escalation_card: Callable[..., Card] | None = None
    control: ControlConfig | None = None


# Онбординг-дырка №0. Эта строка уходит В ОТВЕТ ЛИДУ на «ты бот?» дословно
# (см. honest_disclosure), поэтому markdown-разметка из persona.md — не
# косметика, а порча главного инварианта продукта: клиент, начавший файл с
# «# Аня», получал «# Аня — честно говоря, я — виртуальный ассистент».
# Демо-персона начинается с прозы, поэтому дефект был невидим тестам.
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MD_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+")
_MD_ORDERED_RE = re.compile(r"^\s{0,3}\d+[.)]\s+")
_MD_QUOTE_RE = re.compile(r"^\s{0,3}>\s*")
# Горизонтальная линейка / разделитель front-matter: содержательного текста нет.
_MD_RULE_RE = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")


def _strip_markdown_prefix(line: str) -> str:
    """Снять ведущую markdown-разметку. Возвращает («чистый текст», был_ли_маркер)."""
    for rx in (_MD_HEADING_RE, _MD_BULLET_RE, _MD_ORDERED_RE, _MD_QUOTE_RE):
        m = rx.match(line)
        if m:
            return line[m.end():].strip()
    return line.strip()


def _is_structural(line: str) -> bool:
    return any(rx.match(line) for rx in
               (_MD_HEADING_RE, _MD_BULLET_RE, _MD_ORDERED_RE, _MD_QUOTE_RE))


def _persona_first_line(persona: str) -> str:
    """Первая СОДЕРЖАТЕЛЬНАЯ строка persona.md, очищенная от markdown.

    Предпочитаем прозу («Меня зовут Аня, мне 26…») заголовку («# Аня»): именно
    проза задаёт тон честного ответа. Если прозы нет вовсе — отдаём очищенный
    заголовок (лучше, чем пустота), но БЕЗ решётки. YAML front-matter в начале
    файла пропускаем целиком, иначе его первая пара `title: …` сойдёт за прозу."""
    lines = persona.splitlines()

    # front-matter: '---' первой непустой строкой → всё до закрывающего '---'.
    start = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if _MD_RULE_RE.match(line):
            for j in range(i + 1, len(lines)):
                if _MD_RULE_RE.match(lines[j]):
                    start = j + 1
                    break
            else:                      # незакрытый front-matter — не съедаем файл
                start = i + 1
        break

    fallback = ""
    for line in lines[start:]:
        if not line.strip() or _MD_RULE_RE.match(line):
            continue
        cleaned = _strip_markdown_prefix(line)
        if not cleaned:
            continue
        if not _is_structural(line):
            return cleaned             # проза — то, что нужно
        if not fallback:
            fallback = cleaned         # заголовок/буллет — запасной вариант
    return fallback


# A message that sat unanswered longer than this is one a real person would
# acknowledge ("sorry for the delay") when they finally reply. Below it, no
# apology -- an instant answer needs no excuse.
APOLOGY_AGE_THRESHOLD_SECONDS = 600.0  # 10 minutes


def _humanize_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"около {minutes} минут"
    hours = minutes // 60
    return f"около {hours} ч" if hours < 24 else "больше суток"


def missed_reply_context(age_seconds: float | None) -> str | None:
    """One-off brain context for a message answered late (after downtime).

    Returns None for fresh/recent messages (no apology needed). For anything
    older than the threshold it returns an INSTRUCTION (not a hardcoded phrase)
    telling the model to acknowledge the pause in its own voice, so the apology
    stays in-persona instead of a canned line."""
    if age_seconds is None or age_seconds < APOLOGY_AGE_THRESHOLD_SECONDS:
        return None
    return (
        f"Это сообщение ждало ответа {_humanize_age(age_seconds)} — ты увидела его "
        f"только сейчас. Если это уместно в твоём тоне, начни с короткого, "
        f"естественного извинения за паузу своими словами (без шаблонных фраз), "
        f"а дальше ответь по существу."
    )


def gather_batch(transport: Transport, deps: Deps, first: str) -> list[str]:
    """Collect a burst of inbound messages.

    Tracks `first_received_at` (set once, when the burst starts) and
    `last_received_at` (advanced on every new message). Keeps waiting until
    `debounce_ready` fires either because the quiet gap since the last
    message elapsed, or the hard ceiling since the first message was hit.
    """
    t = deps.cfg.settings.timings
    batch = [first]
    first_received_at = deps.clock()
    last_received_at = first_received_at
    while not H.debounce_ready(
        first_received_at=first_received_at,
        last_received_at=last_received_at,
        now=deps.clock(),
        window=t.debounce_window,
        max_window=t.debounce_max,
    ):
        now = deps.clock()
        remaining_quiet = t.debounce_window - (now - last_received_at)
        remaining_ceiling = t.debounce_max - (now - first_received_at)
        remaining = max(0.0, min(remaining_quiet, remaining_ceiling))
        msg = transport.receive(timeout=remaining)
        if msg is None:
            break
        batch.append(msg)
        last_received_at = deps.clock()
    return batch


def _muted_now(deps: Deps, contact_id: str) -> bool:
    """Читает состояние ЗАНОВО при каждом вызове, а не один раз в начале
    process_batch: владелец мог вмешаться секунду назад, пока Аня "печатала"
    (Pause/Typing из humanizer реально спят wall-clock время), и гейт обязан
    это увидеть перед следующей же отправкой, а не только на входе."""
    row = deps.store.get_or_create_contact(contact_id)
    if not is_attributed(row):
        # DEV-18: пауза без причины — баг-класс (спека §4), а не повод тихо
        # проглотить состояние. Логируем и считаем событие, но НЕ решаем сами
        # снять паузу — is_muted ниже всё равно её уважит.
        deps.store.add_event("unattributed_pause", contact_id=contact_id, ts=deps.clock())
        print(f"  [BUG] paused without a source: {contact_id}")
    kill = deps.store.get_runtime_flag("kill_switch") == "1"
    return is_muted(row, kill_switch=kill, now=deps.clock())


def _payment_context(deps: "Deps", contact_id: str, *, text: str) -> PaymentTurn:
    """Обе операции §8.2 на этом ходу: реквизиты (А) и счёт (Б).

    Возвращает ход целиком, потому что его части уходят в РАЗНЫЕ концы: блок —
    в модель до ответа, значения — в текст после guardrails, заметка — владельцу.
    Разъехаться они не могут: собраны из одного снимка.

    Разбор идёт ЗДЕСЬ, до `brain.reply`, а не в классификаторе: классификатор
    отвечает после реплики (он обязан видеть pending_reply), и цена, разобранная
    им, доехала бы до лида только следующим ходом.

    Реквизитов и цифр в блоке нет: модель их не видит вовсе (§8.3). Ошибка в
    одном символе IBAN — это деньги, ушедшие не туда (риск 10.5)."""
    wh = deps.cfg.settings.work_hours
    return payment_turn(
        store=deps.store, payments=deps.cfg.settings.payments,
        contact_id=contact_id, text=text,
        msg_id=deps.store.max_message_id(contact_id), now=deps.clock(),
        language=deps.cfg.settings.language, work_hours=(wh.start, wh.end),
        knowledge_version=deps.cfg.settings.model)


def _post_invoice_card(deps: "Deps", contact_id: str, note: OwnerNote, *,
                       now: float) -> None:
    """Счёт ушёл в `awaiting_owner` — владелец обязан узнать об этом от нас.

    Кнопок ✅/✏️/❌ (§6) здесь НЕТ намеренно: `route_callback` их пока не
    исполняет и честно отвечает «вне Ф0». Кнопка, которая выглядит сработавшей и
    не срабатывает, хуже её отсутствия — владелец решит, что счёт утверждён.
    Карточка информирующая, действия — руками, до отдельной проводки пульта.

    Никогда не роняет ход (DEV-18): деньги уже записаны, и падение на доставке
    не имеет права стереть ответ лиду."""
    if deps.notifier is None:
        return
    language = deps.cfg.settings.language
    peer = contact_id.split(":", 1)[0]
    why = ", ".join(note.reasons) or "причина не названа"
    text = (f"💸 Рахунок {note.invoice_id} чекає на тебе\n"
            f"Лід готовий платити, але суму назвати не можна: {why}\n"
            f"Реквізити ліду вже надіслані — гроші й реквізити розділені "
            f"навмисно (§8.2).")
    try:
        handle = deps.notifier.notify(Card(
            kind=note.kind, contact_id=contact_id, text_html=escape_html(text),
            buttons=[], reply_hints=[], link=contact_link(user_id=peer)))
    except Exception:
        log.exception("карточка счёта %s НЕ доставлена", note.invoice_id)
        return
    if handle is None:
        log.warning("карточка счёта %s не доставлена (notifier вернул None)",
                    note.invoice_id)
        return
    try:
        deps.store.add_card(msg_id=int(handle.ref.split(":")[-1]),
                            contact_id=contact_id, kind=note.kind, ts=now)
    except Exception:
        log.warning("не удалось записать карточку счёта %r", handle, exc_info=True)


def _escalation_pass(
    deps: "Deps", contact_id: str, *, incoming_text: str, reply: str, now: float,
    disclosure_sent: bool = False,
) -> tuple[str, bool]:
    """Арка 3B: свести детерминированный слой и классификатор, оживить воронку,
    при эскалации отправить карточку владельцу. Возвращает (возможно
    переписанный) reply и факт «карточка ушла владельцу на ЭТОМ ходу» (нужен
    Б4: если ответ потом отменят, владельца надо предупредить, что карточка
    могла устареть). Всё аддитивно: без keywords/classify/notifier это просто
    гардрейл-переписывание, как в арке 3A."""
    store = deps.store
    cfg = deps.cfg
    det = deterministic_escalation(
        incoming_text=incoming_text, reply=reply,
        knowledge=cfg.knowledge, keywords=deps.escalation_keywords,
        forbidden_terms=cfg.settings.forbidden_terms,
        owner_id=cfg.settings.owner_id, owner_ref=cfg.settings.owner_ref,
        strict_knowledge=cfg.settings.strict_knowledge)
    profile = store.get_profile(contact_id)
    slot_on = _obligations_enabled()
    cr = None
    if deps.classify is not None:
        lim = deps.cfg.settings.limits
        window = select_window(store.history(contact_id),
                               budget_tokens=lim.history_budget_tokens,
                               max_messages=lim.history_max_messages)
        if slot_on:
            # Классификатор видит открытые обязательства, чтобы закрыть их по
            # выполнению функции. track_obligations гейтит расширение промпта.
            # pending_reply (P18, дрил №3 2026-07-26): ответ бота ЭТОГО хода в
            # историю ещё НЕ записан — он уходит в транспорт позже. Без него
            # классификатор судит ход по одной реплике лида и собственных
            # обещаний бота не видит, поэтому долг возникал только СЛЕДУЮЩИМ
            # ходом. Едет СИСТЕМНЫМ блоком, а не сообщением: разговор обязан
            # заканчиваться репликой лида, иначе API отвечает 400 «assistant
            # message prefill» и классификатор умирает на каждом ходу
            # (инцидент 2026-07-26 01:2x — поймано живым прогоном).
            cr = deps.classify(
                window, profile, track_obligations=True,
                obligations_block=render_current_for_classifier(
                    store.get_obligations(contact_id)),
                pending_reply=reply or "")
        else:
            # flag off: вызов 2-позиционный, как до арки (байт-в-байт).
            cr = deps.classify(window, profile)
        # Профиль применяем ТОЛЬКО на здоровом ответе (обрезка/мусор →
        # degraded → профиль не трогаем, следующий ход догонит).
        if cr is not None and not cr.degraded:
            if cr.retried:
                # Ход не потерян (ретрай спас), но модель спутала роль — для
                # порога алерта это полноценный сбой, иначе включение ретрая
                # обнулило бы статистику (инцидент 2026-07-23).
                note_classifier_recovered(
                    store, now=now, contact_id=contact_id,
                    detail="первый ответ не был JSON — спасено повтором")
                _maybe_degraded_alert(deps, now=now)
            p_tokens = estimate_tokens(cr.profile) if cr.profile else 0
            if cr.profile and p_tokens > lim.profile_budget_tokens:
                # Условие 4: профиль не кэшируется и платится на КАЖДОМ
                # вызове — сверх потолка НЕ применяем (старый жив), и это
                # ЯВНАЯ деградация (событие + лог), не тихая обрезка.
                detail = (f"профиль превысил бюджет: ~{p_tokens} ток > "
                          f"{lim.profile_budget_tokens} — не применён, "
                          f"старый сохранён")
                log.warning("classifier profile over budget (%s): %s",
                            contact_id, detail)
                note_classifier_error(store, now=now, detail=detail,
                                      contact_id=contact_id)
                _maybe_degraded_alert(deps, now=now)
                _note_profile_miss(deps, contact_id, now=now, why=detail)
            else:
                if cr.profile:
                    store.set_profile(contact_id, cr.profile, ts=now)
                # profile=null — это «нового ничего нет», а НЕ пропуск:
                # классификатор жив, отставать памяти нечем. Серия рвётся.
                reset_profile_miss(store, contact_id, now=now)
            # Слот обязательств (спека §4): применяем на ЗДОРОВОМ классификаторе,
            # НЕЗАВИСИМО от судьбы профиля (перебор бюджета профиля — деградация
            # ПРОФИЛЯ, не классификатора; долг перед лидом всё равно актуален).
            # На degraded этот блок не выполняется (guard выше) → долг не тронут.
            if slot_on:
                # filter_model_updates: жизненный цикл owner_write модель не ведёт —
                # только код по факту карточки (см. _close_owner_write_by_card).
                # existing прокидываем в фильтр: он роняет owner_write open, если
                # строка уже есть (иначе модель переоткрыла бы code-доставку — баг
                # дрила Д-10 T4). Читаем один раз — используем и в фильтре, и в merge.
                existing_obl = store.get_obligations(contact_id)
                store.save_obligations(contact_id, merge_obligations(
                    existing_obl,
                    filter_model_updates(cr.obligations, existing=existing_obl),
                    now=now, current_msg_id=store.max_message_id(contact_id)))
    decision = decide_escalation(det=det, classifier_result=cr)

    if decision.degraded:
        # detail несёт КЛАСС сбоя (парс/exception) — иначе control_events.detail
        # пуст и деградацию не диагностировать (инцидент volska 2026-07-22).
        detail = cr.detail if cr is not None else ""
        note_classifier_error(store, now=now, detail=detail, contact_id=contact_id)
        _maybe_degraded_alert(deps, now=now)
        # Профиль на этом ходу применить было нечем — память отстала на ход.
        _note_profile_miss(deps, contact_id, now=now, why=detail)

    advance_funnel(store, contact_id, stage_signal=decision.stage_signal,
                   escalated=decision.escalate, now=now)

    awaiting_used = False
    # Запасные формулировки ТОЙ ЖЕ канцелярской заглушки, которую мы подставили.
    # Нужны анти-самоповтору (P20 в), который работает В САМОМ КОНЦЕ — после
    # H2-переписывания: иначе его легко обойти, подменив текст уже после проверки.
    alt_variants: tuple[str, ...] = ()
    if det is not None and det.suppress:
        # Гардрейл-подавление: НЕ отправляем ни выдуманную цену/срок (unbacked_claim),
        # ни запрещённый термин (forbidden_reply, рубли/росбанк), ни безцифровое
        # ОБЕЩАНИЕ вне базы (unbacked_promise: скидка/гарантия/рассрочка/«свяжется» —
        # H1). НО подавление ≠ тишина: лид обязан получить КОРРЕКТНЫЙ ответ (верные
        # способы / нейтральное «уточню и вернусь»), иначе бот молчит и мы теряем
        # продажу, защитив репутацию. Даём безопасный ответ + эскалируем владельцу.
        print(f"  [escalation flag] {det.tag} for {contact_id}: {reply!r}")
        safe = cfg.settings.safe_payment_reply
        # Защита от само-простреливания: если клиент вписал запрещённый термин в
        # сам safe_payment_reply — не отправляем его, откатываемся к нейтральному.
        if det.tag == "forbidden_reply" and safe and forbidden_mention(safe, cfg.settings.forbidden_terms) is None:
            reply = safe
        else:
            if det.tag == "forbidden_reply" and safe:
                log.warning("safe_payment_reply сам содержит запрещённый термин — не использую")
            # P20 (D): СНАЧАЛА пробуем вырезать необеспеченное место, а не весь
            # ответ. Замер 2026-07-29: одно число («21») убивало ответ вместе с
            # четырьмя обеспеченными ценами, и лид трижды не увидел прайса.
            #
            # ⚠️ ТОЛЬКО для unbacked_claim. Редакция работает по ЧИСЛАМ, а
            # forbidden_reply (запрещённый термин) и unbacked_promise
            # (безцифровое «дам скидку») она не видит — пропустить их через неё
            # значило бы проделать дыру в гардрейле, поэтому им по-прежнему
            # полное подавление.
            redacted = (_try_redact(deps, contact_id, reply=reply, now=now)
                        if det.tag == "unbacked_claim" else None)
            if redacted is not None:
                reply = redacted
            else:
                # Падеж-безопасно (owner_id не склоняем: «позову Дмитрий» → криво):
                # глагол «свяж»/«зв'яж» держит H2-детекцию, а как назвать владельца
                # задаёт owner_ref (уже в нужном падеже; пусто → per-language дефолт).
                # Б2: текст локализован по settings.language — украиноязычный лид не
                # должен получать русскую аварийную фразу.
                reply, awaiting_used, alt_variants = _suppression_fallback(
                    deps, contact_id, slot_on=slot_on)

    delivered = False
    if decision.escalate and deps.notifier is not None:
        delivered = _post_escalation_card(deps, contact_id, det=det, cr=cr, now=now)
    # H2: обещание участия ВЛАДЕЛЬЦА (называет его по имени или «свяжется/
    # перезвонит») допустимо, ТОЛЬКО если карточка реально дошла до владельца
    # (delivered). Не дошла (сбой доставки, тихая правка устаревшей карточки,
    # нет notifier, эскалации не было) → не обещаем контакт от его имени: говорим
    # то, что Аня выполнит сама. Причина↔следствие.
    #
    # Покрывает ВСЕ пути (suppress/keyword/классификатор/без эскалации), а не
    # только suppress: дрил 07-18 показал, что обещание «позову Дмитрия» на
    # keyword-эскалации со сбоем доставки старый гейт пропускал. ИСКЛЮЧЕНИЕ —
    # честное раскрытие «я бот, подключу владельца» (tag=bot_question): там
    # упоминание владельца часть ЧЕСТНОСТИ (H3), не обещание за него, и трогать
    # его нельзя даже при неудачной доставке.
    # Защищаем ФАКТ раскрытия, а не тег. Тег bot_question ставится по ВХОДЯЩЕМУ
    # и срабатывает независимо от honesty_mode, поэтому при honesty_mode=
    # free_owner_liability ответ пишет brain — и защита по тегу пропустила бы
    # его недоставленное «свяжу с владельцем» мимо гейта H2. Защищать надо
    # ровно тот текст, который мы САМИ сгенерировали как честное раскрытие.
    # P20 (б): «вже передала керівниці» — КОД-проверенный факт (owner_write
    # закрывается по доставленной карточке), а не обещание за владельца.
    # Без этой защиты H2-гейт при недоставленной карточке ЭТОГО хода затёр бы
    # честное состояние обратно в «уточню и вернусь» — ровно ту заглушку,
    # из-за которой P20 и открыт.
    protected = disclosure_sent or awaiting_used
    implies_owner = mentions_owner_contact(
        reply, owner_id=cfg.settings.owner_id, owner_ref=cfg.settings.owner_ref)
    if not protected and not delivered and implies_owner:
        # В honest-режиме аварийная подмена обязана НЕСТИ честный факт сама.
        # Раньше здесь стоял голый self_action_fallback, и он затирал раскрытие,
        # сочинённое моделью на формулировке, которую не поймал is_bot_question
        # (замер volska 2026-07-21: лид на «ви Ольга особисто?» получал ответ,
        # из которого следовало, что перед ним человек). Условия «спрашивали ли
        # про личность» тут нет специально — оно совпадает с `disclosure_sent`
        # выше, то есть с уже защищённым множеством, и дыру не закрывает.
        honest = cfg.settings.honesty_mode == HONESTY_HONEST
        maker = honest_self_action_fallback if honest else self_action_fallback
        reply = maker(language=cfg.settings.language)
        # H2 подменяет текст ПОСЛЕ выбора заглушки подавления, поэтому запасные
        # формулировки тоже надо переставить на это семейство — иначе
        # анти-самоповтор будет сравнивать не с тем, что реально уйдёт лиду
        # (замер: два подавления подряд снова давали байт-идентичный текст).
        alt_variants = (maker(language=cfg.settings.language, variant=1),)
        implies_owner = False   # заменили на само-действие — контакта больше нет
    # Q2 (дрил 07-19): обещание контакта/эскалация не тянет встречный вопрос —
    # лида ПЕРЕДАЛИ, а бот бы продолжал продавать в том же сообщении и сбивал его.
    # Одно из двух: либо хэндофф, либо ведение диалога — не оба. Режем хвостовой
    # вопрос у ответов, реально обещающих контакт владельца (fallback выше его не
    # содержит → там no-op).
    if implies_owner and not protected:
        reply = _drop_trailing_question(reply)
    # Слот §3: owner_write закрывает КОД по факту ДОСТАВЛЕННОЙ карточки (не
    # модель). Карточка дошла → долг «керівниця напише» выполнен (владелец
    # уведомлён); msg_id — id карточки из esc_active. Только при slot on.
    if slot_on and delivered:
        _close_owner_write_by_card(deps, contact_id, now=now)

    # P20 (в) — ПОСЛЕДНИЙ шаг: лид не получает ту же реплику дважды подряд.
    # Стоит здесь, после ВСЕХ подмен (подавление, H2, срез хвостового вопроса),
    # потому что дважды-идентичным был именно ИТОГОВЫЙ текст (msg 318/320/322).
    final = pick_non_repeating(
        reply, previous=_last_assistant_text(deps.store, contact_id),
        variants=alt_variants)
    if final is None:
        # Вариантов нет (обычный ответ brain, а не заглушка) → честная пауза.
        # Молчание видимо: событие в ленте, иначе пауза выглядит как «бот умер».
        log.warning("reply repeat: та же реплика подряд, пауза для %s", contact_id)
        try:
            deps.store.add_event("reply_repeat_paused", contact_id=contact_id,
                                 detail="повтор предыдущей реплики", ts=now)
        except Exception:        # DEV-18: аудит не имеет права ронять ход
            log.exception("не удалось записать событие анти-повтора")
        return "", delivered
    return final, delivered


def _last_assistant_text(store, contact_id: str) -> str | None:
    """Предыдущий ОТВЕТ целиком — то, что реально увидел лид.

    Humanizer режет ответ на баббл(ы), и каждый пишется в `messages` отдельной
    строкой. Сравнивать надо с ХОДОМ, а не с последним бабблом: иначе длинная
    заглушка из двух предложений «не совпадает» сама с собой и анти-самоповтор
    молча ничего не ловит. Берём последний непрерывный ряд assistant-строк."""
    msgs = list(store.history(contact_id) or ())
    # Входящее ЭТОГО хода уже лежит в истории (process_batch пишет его до
    # эскалации), поэтому сначала перешагиваем хвост из user-строк, и только
    # потом собираем ряд assistant — иначе «предыдущего ответа» не видно вовсе.
    i = len(msgs) - 1
    while i >= 0 and msgs[i].get("role") != "assistant":
        i -= 1
    run: list[str] = []
    while i >= 0 and msgs[i].get("role") == "assistant":
        run.append(msgs[i].get("text") or "")
        i -= 1
    if not run:
        return None
    return " ".join(reversed(run))


def _owner_write_delivered(store, contact_id: str, *, slot_on: bool) -> bool:
    """Вопрос лида уже лежит у владельца: owner_write закрыт КОДОМ по факту
    доставленной карточки (спека слота §3), поэтому это надёжный факт, а не
    мнение модели. Слот выключен → состояния нет, ведём себя как раньше."""
    if not slot_on:
        return False
    return any(o.okey == "owner_write" and o.status == "delivered"
               for o in store.get_obligations(contact_id))


def _try_redact(deps: "Deps", contact_id: str, *, reply: str, now: float) -> str | None:
    """P20 (D): вырезать необеспеченные числа, сохранив остальной ответ.

    None → редакцией не спаслось (или резать было нечего) ⇒ вызывающий обязан
    подавить ответ целиком. Лог PII-free: число-причина, правило и длина
    вырезанного куска — без текста лида и без текста ответа."""
    cfg = deps.cfg
    res = redact_unbacked(reply, cfg.knowledge, language=cfg.settings.language)
    if not res.clean or not res.records:
        return None
    for r in res.records:
        log.info("redaction: rule=%s number=%s clause_chars=%d lang=%s",
                 r.rule, r.number, r.clause_chars, cfg.settings.language)
    try:
        deps.store.add_event(
            "unbacked_redacted", contact_id=contact_id,
            detail=",".join(f"{r.rule}:{r.number}" for r in res.records), ts=now)
    except Exception:            # DEV-18: аудит-запись не имеет права ронять ход
        log.exception("не удалось записать событие редакции")
    return res.text


def _suppression_fallback(deps: "Deps", contact_id: str, *,
                          slot_on: bool) -> tuple[str, bool, tuple[str, ...]]:
    """Какую заглушку получит лид и является ли она «ждём владельца».

    P20 (а): раньше здесь стояла ОДНА константа, и три подавления подряд дали
    три байт-идентичных сообщения — причём второе было ответом на прямой вопрос
    «вы уточнили детали?». Теперь: если вопрос уже у владельца — говорим это
    (состояние), иначе обещаем уточнить (действие); и в обоих случаях не
    повторяем дословно предыдущую реплику.

    ⚠️ Вариант обязан нести ТОТ ЖЕ факт: «передала» не подставляется вместо
    «уточню», пока карточка не доставлена, иначе анти-повтор начнёт врать."""
    cfg = deps.cfg
    lang, ref = cfg.settings.language, cfg.settings.owner_ref
    if _owner_write_delivered(deps.store, contact_id, slot_on=slot_on):
        maker, awaiting = awaiting_owner_fallback, True
    else:
        maker, awaiting = suppressed_fallback, False
    return (maker(language=lang, owner_ref=ref), awaiting,
            (maker(language=lang, owner_ref=ref, variant=1),))


def _card_msg_id(store, contact_id: str) -> int | None:
    """id доставленной карточки из runtime_flag esc_active (`bot:<contact>:<id>`).
    None, если формат неожиданный — тогда штампуем max(messages.id)."""
    from chatter.core.escalation import esc_active_key
    raw = store.get_runtime_flag(esc_active_key(contact_id))
    if raw and ":" in raw:
        try:
            return int(raw.rsplit(":", 1)[1])
        except ValueError:
            return None
    return None


def _close_owner_write_by_card(deps: "Deps", contact_id: str, *, now: float) -> None:
    """Детерминированно перевести owner_write в delivered (спека §3): карточка
    керівниці доставлена. merge создаёт-и-закрывает, если owner_write ещё не был
    открыт (эскалация без явного «керівниця напише»). closed_msg_id = id карточки."""
    store = deps.store
    card_id = _card_msg_id(store, contact_id)
    if card_id is None:
        card_id = store.max_message_id(contact_id)
    existing = store.get_obligations(contact_id)
    ow = next((o for o in existing if o.okey == "owner_write"), None)
    if ow is not None and ow.status != "open":
        # Повторная эскалация: owner_write уже закрыт, но доставлена НОВАЯ карточка
        # → новое достоверное событие закрытия. Код — единственный владелец
        # жизненного цикла owner_write (спека §3), поэтому перештамповываем closed_*
        # на актуальную карточку НАПРЯМУЮ, в обход idempotency merge (та бережёт
        # ПЕРВОЕ закрытие для классификатор-ведомых brief/examples/recalc). Так фикс
        # filter (модель не переоткрывает) не запирает легитимный повтор.
        rest = [o for o in existing if o.okey != "owner_write"]
        rest.append(replace(ow, status="delivered",
                            closed_msg_id=card_id, closed_ts=now))
        store.save_obligations(contact_id, rest)
        return
    # первое закрытие (owner_write open) или create-and-close (строки ещё нет)
    store.save_obligations(contact_id, merge_obligations(
        existing,
        [{"kind": "owner_write", "owed_by": "bot", "status": "delivered",
          "detail": "карточка керівниці доставлена"}],
        now=now, current_msg_id=card_id))


# Разбивка на предложения по границе .!? + пробел (хвостовой вопрос режем с конца).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _drop_trailing_question(reply: str) -> str:
    """Убрать хвостовые предложения-вопросы. «А. Б? В?» → «А.». Если ВЕСЬ ответ —
    вопрос (резать нечего осмысленно), возвращаем исходный (не молчим)."""
    parts = _SENTENCE_SPLIT.split((reply or "").strip())
    while parts and parts[-1].rstrip().endswith("?"):
        parts.pop()
    return " ".join(parts).strip() or reply


# Окно дедупа карточек эскалации: повторная эскалация того же лида В ПРЕДЕЛАХ
# окна правит существующую карточку (гасит случайные двойные срабатывания
# одного залпа — дебаунсер уже склеивает залп в один process_batch). За окном
# это НОВЫЙ ход лида → новая уведомляющая карточка (editMessageText не шлёт пуш,
# тихая правка устаревшей карточки = владелец слеп; дрил 07-18).
_ESCALATION_DEDUP_SECONDS = 60.0


def _post_escalation_card(deps: "Deps", contact_id: str, *, det, cr, now: float) -> bool:
    """Собрать и отправить карточку эскалации. Имя/ссылку строит раннер
    (`deps.escalation_card`, у него есть Telethon-entity); без него — текстовый
    фоллбек по contact_id. Никогда не роняет process_batch (DEV-18).

    Возвращает True, если карточка ДОШЛА до владельца (notify/update успешны),
    иначе False. H2: обещание участия владельца лиду допустимо, только если
    владелец реально получил карточку — иначе он «слеп», а лид ждёт впустую."""
    cfg = deps.cfg
    language = cfg.settings.language
    recent = [(m["role"], m["text"]) for m in deps.store.history(contact_id)][-5:]
    cr_escalated = cr is not None and not cr.degraded and cr.escalate
    cr_reason = cr.reason if (cr is not None and not cr.degraded and cr.reason) else ""
    # Fix 5b: «Хочет» (summary) ≠ «Почему» (reason). det.detail — это ПРИЧИНА
    # (сработавшее слово), а НЕ то, что лид хочет; ставить её в summary =
    # категориальная ошибка (при чисто детерминированной эскалации обе строки
    # схлопывались в одну). Когда классификатор не дал reason, честный «что
    # хочет» — последняя реплика самого лида (она уже в history к этому моменту).
    last_lead = next((text for role, text in reversed(recent) if role == "user"), "")
    summary = cr_reason or last_lead or "нужно внимание владельца"
    # Fix 2: одно решение — НЕСКОЛЬКО причин. Если сработали оба слоя, «почему»
    # показывает обе («ключевое слово … + классификатор: …»), а не только одну.
    reasons = []
    if det is not None:
        reasons.append(det.detail)
    if cr_escalated and cr_reason:
        reasons.append(f"классификатор: {cr_reason}")
    why = " + ".join(reasons) if reasons else "классификатор отметил горячий лид"
    try:
        if deps.escalation_card is not None:
            card = deps.escalation_card(contact_id, summary, why, recent)
        else:
            peer = contact_id.split(":", 1)[0]
            # Fix 5a: имя лида — «777» через display_name (как no-entity fallback
            # раннера), а НЕ сырой composite key «777:demo». Имени в Store нет
            # (Telethon-entity недоступен на этом пути), голый id — честный минимум.
            text = format_escalation_card(
                name_html=display_name(user_id=peer),
                link=contact_link(user_id=peer), summary=summary, reason=why,
                recent=recent, language=language, persona_name=cfg.settings.persona_name)
            card = Card(
                kind="escalation", contact_id=contact_id, text_html=text,
                buttons=escalation_buttons(language),
                reply_hints=[
                    console_text("card_resume_reply_hint", language),
                    console_text("card_resume_status_hint", language),
                ],
                link=contact_link(user_id=peer))
        # Fix 2 + дрил 07-18: дедуп только в пределах КОРОТКОГО окна. Внутри окна
        # (случайное двойное срабатывание того же залпа) правим существующую
        # карточку. За окном — активный флаг УСТАРЕЛ: владелец давно не тапал, а
        # editMessageText НЕ шлёт пуш, значит тихая правка = владелец слеп.
        # Поэтому новый ход лида после окна = НОВАЯ уведомляющая карточка
        # (sendMessage). route_callback чистит флаг при действии владельца.
        active = deps.store.get_runtime_flag(esc_active_key(contact_id))
        active_ts = deps.store.get_runtime_flag_ts(esc_active_key(contact_id))
        if active and active_ts is not None and (now - active_ts) < _ESCALATION_DEDUP_SECONDS:
            # delivered = РЕАЛЬНЫЙ успех правки (не безусловный True): H2 обязан
            # видеть, дошло ли до владельца на самом деле.
            return deps.notifier.update_card(CardHandle(ref=active), card)
        handle = deps.notifier.notify(card)
    except Exception:
        log.exception("escalation card FAILED for %s", contact_id)
        return False
    if handle is None:
        return False       # notify проглотил сбой доставки — владелец НЕ получил
    deps.store.set_runtime_flag(esc_active_key(contact_id), handle.ref, ts=now)
    try:
        msg_id = int(handle.ref.split(":")[-1])
        deps.store.add_card(msg_id=msg_id, contact_id=contact_id, kind="escalation", ts=now)
    except Exception:
        log.warning("could not record escalation card handle %r", handle, exc_info=True)
    return True


def _maybe_degraded_alert(deps: "Deps", *, now: float) -> None:
    """Классификатор сбоит (§6): алертим владельца ОДИН раз за окно
    (не штормим), только если сбоев за окно набралось на порог."""
    control = deps.control or ControlConfig()
    window = control.status_window_hours * 3600.0
    if not classifier_degraded(
        deps.store, now=now, window_seconds=window, threshold=control.classifier_error_threshold):
        return
    last = deps.store.get_runtime_flag("classifier_degraded_alerted_ts")
    if last and (now - float(last)) < window:
        return
    if deps.notifier is None:
        return
    count = classifier_failure_count(deps.store, now=now, window_seconds=window)
    text = console_text(
        "degraded_alert", deps.cfg.settings.language,
        count=count, hours=control.status_window_hours)
    try:
        handle = deps.notifier.notify(Card(
            kind="alert", contact_id="", text_html=escape_html(text),
            buttons=[], reply_hints=[]))
    except Exception:
        log.exception("degraded-classifier alert FAILED to send")
        return
    # AUDIT D4: кулдаун ставим ТОЛЬКО по факту доставки. Раньше флаг писался
    # до notify — упавшая или проглоченная доставка сжигала окно на сутки, и
    # владелец оставался глух, не получив ни одного алерта. Ровно как в
    # _post_escalation_card: флаг = подтверждённый успех, а не намерение.
    if handle is None:
        log.warning("degraded-classifier alert NOT delivered (notifier вернул None)")
        return
    deps.store.set_runtime_flag("classifier_degraded_alerted_ts", str(now), ts=now)


def _note_profile_miss(deps: "Deps", contact_id: str, *, now: float, why: str) -> None:
    """Профиль на этом ходу применить не удалось. Считаем серию ПО КОНТАКТУ и
    при M подряд алертим отдельно от «сбоев за сутки»: два разных лида по
    одному сбою — шум, три подряд по одному лиду — замёрзшая память в живом
    диалоге (бот выглядит помнящим, а помнит позавчерашнее)."""
    store = deps.store
    streak = note_profile_miss(store, contact_id, now=now)
    control = deps.control or ControlConfig()
    threshold = control.profile_stale_threshold
    log.warning("profile miss #%d for %s: %s", streak, contact_id, why)
    if threshold <= 0 or streak < threshold:
        return
    # Один алерт на СЕРИЮ: флаг снимает reset_profile_miss на первом здоровом
    # ходу. Иначе каждый следующий сбой в той же серии штормил бы владельца.
    alerted_key = f"profile_miss_alerted:{contact_id}"
    if (store.get_runtime_flag(alerted_key) or "0") != "0":
        return
    if deps.notifier is None:
        return
    peer = contact_id.split(":", 1)[0]
    text = console_text(
        "profile_stale_alert", deps.cfg.settings.language,
        count=streak, name=display_name(user_id=peer),
        link=contact_link(user_id=peer))
    try:
        handle = deps.notifier.notify(Card(
            kind="alert", contact_id=contact_id, text_html=text,
            buttons=[], reply_hints=[], link=contact_link(user_id=peer)))
    except Exception:
        log.exception("stale-profile alert FAILED to send for %s", contact_id)
        return
    # AUDIT D4, тот же класс бага: «уже алертили» ставим по факту доставки,
    # иначе одна упавшая отправка глушит серию до первого здорового хода.
    if handle is None:
        log.warning("stale-profile alert NOT delivered for %s", contact_id)
        return
    store.set_runtime_flag(alerted_key, str(streak), ts=now)


def _maybe_stale_card_notice(deps: "Deps", contact_id: str, *, now: float,
                             card_posted: bool) -> None:
    """Б4 + вопрос Даниила: что делать с карточкой, если ответ отменён.

    Карточку НЕ откатываем — факт («лид просит точную смету») реален
    независимо от того, ушли ли бабблы; откат означал бы, что владелец теряет
    живой сигнал из-за задержки доставки. Но карточка, отправленная за секунду
    до того, как лид дописал «ой, поки не треба», — ровно та спурьёзная
    карточка керівниці, которая хуже отменённого ответа: владелец звонит лиду
    с предложением, от которого тот уже отказался.

    Поэтому: карточка живёт, а владельцу уходит ОТДЕЛЬНОЕ сообщение «лид
    дописал, карточка могла устареть». Именно отдельное, а не тихая правка
    существующей: editMessageText не шлёт пуш, и владелец её не увидит
    (дрил 07-18). Шлём ТОЛЬКО если карточка ушла на этом же ходу — иначе
    предупреждать не о чем, а лишний пуш обесценивает карточки."""
    if not card_posted or deps.notifier is None:
        return
    peer = contact_id.split(":", 1)[0]
    text = console_text(
        "stale_card_notice", deps.cfg.settings.language,
        name=display_name(user_id=peer), link=contact_link(user_id=peer))
    try:
        deps.notifier.notify(Card(
            kind="alert", contact_id=contact_id, text_html=text,
            buttons=[], reply_hints=[], link=contact_link(user_id=peer)))
    except Exception:
        log.exception("stale-card notice FAILED to send for %s", contact_id)


def process_batch(
    contact_id: str, incoming: list[str], transport: Transport, deps: Deps,
    *, missed_age_seconds: float | None = None,
    fresh_incoming: Callable[[], bool] | None = None,
) -> None:
    """Coalesce the batch, guard on rate limits, decide a reply
    (disclosure > guardrails > brain), then deliver it via the humanizer's
    action plan (compose_reply): Pause/Typing/Say interpreted in order.

    `missed_age_seconds`: when this batch is a message that arrived while the
    bot was OFFLINE (catch-up), how long the oldest message waited. It drives
    the "sorry for the pause" acknowledgement on the brain path. None (default)
    = a live message, no apology.

    `fresh_incoming`: Б4 — «лид дописал ПОКА мы доставляем». Замыкание
    дебаунсера, читается перед КАЖДЫМ бабблом; True = наш ответ устарел, и
    остаток не отправляется. None (консоль, catch-up, старые тесты) =
    поведение ровно прежнее."""
    text = H.coalesce(incoming)
    if not text:
        return
    deps.store.add_message(contact_id, "user", text, ts=deps.clock())

    if _muted_now(deps, contact_id):
        # Проверка В НАЧАЛЕ: заглушённый диалог не должен даже дойти до
        # brain/LLM. Входящее уже сохранено выше — контекст не рвётся.
        print(f"  [muted] {contact_id}: входящее записано, ответа не будет")
        return

    limits = deps.cfg.settings.limits
    if not within_hourly_limit(deps.store, contact_id, now=deps.clock(), limit=limits.per_contact_hourly):
        print(f"  [rate limit] hourly limit hit for {contact_id}; skipping")
        return
    if not within_daily_cap(deps.store, now=deps.clock(), cap=limits.daily_cap):
        print("  [rate limit] daily cap hit; skipping")
        return

    # Деньги — ПОСЛЕ лимитов: счёт, выставленный на ходу, который бот всё равно
    # не отправит, это идущий срок и записанный долг при молчащем боте.
    pay = _payment_context(deps, contact_id, text=text)
    pay_block, pay_values = pay.block, pay.values
    if pay.owner_note is not None:
        _post_invoice_card(deps, contact_id, pay.owner_note, now=deps.clock())

    # honesty_mode (per-client, дефолт honest): захардкоженная гарантия честности
    # стала ОСОЗНАННЫМ выбором владельца — но именно выбором, а не удалением
    # механики. Детектор вопроса и текст раскрытия остаются на месте и под
    # тестами; тумблер решает лишь, перехватывать ли ответ. Эскалация вопроса
    # владельцу от режима НЕ зависит.
    # Пилотная метрика (Фаза 2): считаем КАЖДЫЙ вопрос «ты бот?» — независимо от
    # honesty-режима. Нужен для оценки, как часто лиды спрашивают про личность
    # (сигнал доверия/подозрения). Событие пишется ДО ветки раскрытия, чтобы в
    # free-режиме (перехвата нет, отвечает модель) вопрос всё равно учитывался.
    # Счётчик за окно: store.count_events("bot_question", since_ts=...).
    bot_question = is_bot_question(text)
    if bot_question:
        deps.store.add_event("bot_question", contact_id=contact_id, ts=deps.clock())

    disclosure_sent = False
    if bot_question and deps.cfg.settings.honesty_mode == HONESTY_HONEST:
        reply = honest_disclosure(
            owner_id=deps.cfg.settings.owner_id,
            persona_line=_persona_first_line(deps.cfg.persona),
            language=deps.cfg.settings.language,
        )
        disclosure_sent = True
    else:
        lim = deps.cfg.settings.limits
        # Слот обязательств (спека §5): рендер из ТАБЛИЦЫ, не из окна — долг
        # доезжает до brain, даже когда ход-источник уехал за окно истории.
        # flag off → "" / log_shape=False → brain.reply как раньше (байт-в-байт).
        slot_on = _obligations_enabled()
        obl_block = ""
        obl_list = ()
        if slot_on:
            obl_list = deps.store.get_obligations(contact_id)
            obl_block = render_slot_block(obl_list, now=deps.clock())
        reply = deps.brain.reply(
            select_window(deps.store.history(contact_id),
                          budget_tokens=lim.history_budget_tokens,
                          max_messages=lim.history_max_messages),
            context_note=missed_reply_context(missed_age_seconds),
            profile=deps.store.get_profile(contact_id),
            obligations_block=obl_block,
            obligations=obl_list, log_shape=slot_on, contact_id=contact_id,
            invoice_block=pay_block,
        )

    # Арка 3B: единый проход эскалации (детерминированный слой + классификатор),
    # оживление воронки и — при эскалации — карточка владельцу. Здесь же
    # остаётся гардрейл-переписывание необеспеченного обещания (перенесено из
    # инлайна в _escalation_pass), чтобы Аня не отправила выдуманную цену.
    reply, card_posted = _escalation_pass(
        deps, contact_id, incoming_text=text, reply=reply,
        now=deps.clock(), disclosure_sent=disclosure_sent)

    # ПОДСТАНОВКА — ПОСЛЕ guardrails и ТОЛЬКО здесь (§14 п.16). Сделай её до
    # редакции — и `large_number` вырежет подставленную сумму как необеспеченную:
    # промежуточных ступеней торга в knowledge нет по определению.
    #
    # Проверка идёт и при выключенной фиче: служебное «{REQUISITES}» в лицо
    # клиенту — дефект независимо от тумблера, а значений тогда просто нет.
    if pay.requires_disclaimer and needs_caveat(reply) and not has_disclaimer(reply):
        # §2.2 и приёмка §8.5 п.4. Сумма без оговорки — это оферта, а не оценка:
        # клиент вправе считать её ценой и требовать её же после пересчёта.
        # Подавляется ВЕСЬ ответ, как и полуотрендеренные реквизиты: «почти
        # оговорка» защищает ровно настолько, насколько её нет.
        log.error("ответ для %s подавлен: названа сумма без оговорки", contact_id)
        deps.store.add_event("payments_disclaimer_missing", contact_id=contact_id,
                             detail=reply[:120], ts=deps.clock())
        return

    try:
        reply = finalize(reply, pay_values)
    except UnsubstitutedPlaceholder as exc:
        # §8.3: подавляется ВЕСЬ ответ, а не вырезается плейсхолдер.
        # Полуотрендеренные реквизиты хуже молчания: ошибка в одном символе
        # IBAN — это деньги, ушедшие не туда. Молча не глотаем (DEV-18):
        # событие + лог, чтобы «бот замолчал» имело объяснение в БД.
        log.error("ответ для %s подавлен: %s (плейсхолдеры: %s)",
                  contact_id, exc, ", ".join(find_placeholders(reply)))
        deps.store.add_event("payments_placeholder_unresolved",
                             contact_id=contact_id, detail=str(exc), ts=deps.clock())
        return

    if not (reply or "").strip():
        # Честная пауза анти-самоповтора (P20 в) — единственный путь сюда.
        # Пустой текст в transport.send() был бы ошибкой API, а не молчанием.
        log.info("нечего отправлять для %s — ход пропущен", contact_id)
        return

    now_hour = _dt.datetime.fromtimestamp(deps.clock()).hour
    actions = H.compose_reply(
        reply, deps.rng, deps.cfg.settings.timings, deps.cfg.settings.work_hours, now_hour,
    )
    total_bubbles = sum(1 for a in actions if isinstance(a, H.Say))
    said = 0
    for action in actions:
        if isinstance(action, H.Pause):
            deps.sleep(action.seconds)
        elif isinstance(action, H.Online):
            transport.set_online(action.on)
        elif isinstance(action, H.ReadAck):
            transport.read_acknowledge()
        elif isinstance(action, H.Typing):
            transport.send_typing(action.on)
        elif isinstance(action, H.Say):
            if _muted_now(deps, contact_id):
                # Позорный сценарий (спека §3): Аня ушла в паузу
                # чтения+печати, за это время владелец ответил руками — этот
                # чек ловит его ПЕРЕД каждой отправкой, не только один раз в
                # начале, иначе она договорит поверх него.
                print(f"  [muted mid-reply] {contact_id}: отменяю остаток ответа")
                return
            if fresh_incoming is not None and fresh_incoming():
                # Б4: лид дописал, пока мы «печатали» — наш ответ устарел.
                # Молча досылать остаток нельзя: он уйдёт ПОСЛЕ его новой
                # реплики, и это самый видимый провал «неотличима от человека».
                # Регенерацию тут НЕ делаем: буфер дебаунсера не пуст, та же
                # итерация запустит новый process_batch с историей
                # «отправленная часть + новая реплика», и модель сама решит,
                # что из недосказанного повторить. Неотправленного в истории
                # нет (persist только на send) — повторить его она вправе.
                transport.send_typing(False)      # не висеть «печатає»
                detail = f"не відправлено {total_bubbles - said} з {total_bubbles} бабблів"
                deps.store.add_event("stale_reply_cancelled", contact_id=contact_id,
                                     detail=detail, ts=deps.clock())
                log.info("stale reply cancelled for %s: %s", contact_id, detail)
                print(f"  [stale mid-reply] {contact_id}: {detail}")
                _maybe_stale_card_notice(
                    deps, contact_id, now=deps.clock(),
                    card_posted=card_posted)
                return
            transport.send(action.text)
            said += 1
            deps.store.add_message(contact_id, "assistant", action.text, ts=deps.clock())


def _build_llm(cfg: Config, mode: str, usage_sink=None):
    if mode == "real" or (mode == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM(cfg.settings.model, usage_sink=usage_sink)
    return FakeLLM()


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default stdin/stdout to a legacy codepage (e.g. cp1251)
    # instead of UTF-8, which silently corrupts Cyrillic instead of raising -- and
    # specifically breaks disclosure.is_bot_question()'s match on the
    # honesty-critical "ты бот?" question (observed live: without this, a merged
    # batch containing "ты бот?" fell through to the brain/guardrail path instead
    # of the honest disclosure). Force UTF-8 explicitly rather than relying on the
    # operator to set PYTHONUTF8=1 externally.
    for _stream in (sys.stdin, sys.stdout):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(prog="chatter.run")
    p.add_argument("--client", required=True)
    p.add_argument("--transport", choices=["fake"], default="fake")
    p.add_argument("--clients-dir", default=str(Path(__file__).resolve().parent / "clients"))
    p.add_argument("--llm", choices=["auto", "real", "fake"], default="auto")
    p.add_argument("--db", default=":memory:")
    p.add_argument("--contact", default="console-user")
    args = p.parse_args(argv)

    cfg = load_config(Path(args.clients_dir), args.client)  # raises ConfigError loudly at startup
    store = Store(args.db)
    llm = _build_llm(cfg, args.llm, usage_sink=usage_sink_for(store))
    deps = Deps(
        cfg=cfg, store=store, brain=Brain(llm, cfg),
        rng=random.Random(), clock=time.time, sleep=time.sleep,
    )
    transport = FakeConsoleTransport()
    deps.store.get_or_create_contact(args.contact)

    print(f"[chatter] client={cfg.slug} llm={type(llm).__name__} lang={cfg.settings.language}")
    print("[chatter] пишите сообщения (Ctrl-D для выхода). Быстрые подряд склеятся.\n")
    try:
        while True:
            first = transport.receive(timeout=None)
            if first is None:
                break
            batch = gather_batch(transport, deps, first)
            process_batch(args.contact, batch, transport, deps)
    finally:
        deps.store.close()
    print("\n[chatter] пока!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
