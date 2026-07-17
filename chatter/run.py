from __future__ import annotations
import argparse
import datetime as _dt
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from chatter.config.loader import Config, ControlConfig, load_config
from chatter.core import humanizer as H
from chatter.core.brain import Brain
from chatter.core.brand_safety import forbidden_mention
from chatter.core.classifier import (
    ClassifierResult, classifier_degraded, note_classifier_error,
)
from chatter.core.console import (
    console_text, contact_link, display_name, escalation_buttons, escape_html,
    format_escalation_card,
)
from chatter.core.disclosure import honest_disclosure, is_bot_question
from chatter.core.escalation import (
    advance_funnel, decide_escalation, deterministic_escalation, esc_active_key,
)
from chatter.core.guardrails import (
    within_daily_cap, within_hourly_limit,
)
from chatter.core.llm import AnthropicLLM, FakeLLM
from chatter.core.pause import is_attributed, is_muted
from chatter.notify.base import Card, CardHandle, Notifier
from chatter.storage.db import Store
from chatter.transport.base import Transport
from chatter.transport.fake import FakeConsoleTransport

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
    classify: Callable[[list[dict]], ClassifierResult] | None = None
    escalation_keywords: list[str] = field(default_factory=list)
    # Раннер (Telethon) даёт билдер карточки с кликабельным ИМЕНЕМ/ссылкой
    # (резолв entity живёт на loop). Без него — текстовый фоллбек по contact_id.
    escalation_card: Callable[..., Card] | None = None
    control: ControlConfig | None = None


def _persona_first_line(persona: str) -> str:
    for line in persona.splitlines():
        if line.strip():
            return line.strip()
    return ""


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


def _escalation_pass(
    deps: "Deps", contact_id: str, *, incoming_text: str, reply: str, now: float,
) -> str:
    """Арка 3B: свести детерминированный слой и классификатор, оживить воронку,
    при эскалации отправить карточку владельцу. Возвращает (возможно
    переписанный) reply. Всё аддитивно: без keywords/classify/notifier это
    просто гардрейл-переписывание, как в арке 3A."""
    store = deps.store
    cfg = deps.cfg
    det = deterministic_escalation(
        incoming_text=incoming_text, reply=reply,
        knowledge=cfg.knowledge, keywords=deps.escalation_keywords,
        forbidden_terms=cfg.settings.forbidden_terms)
    cr = deps.classify(store.history(contact_id)) if deps.classify is not None else None
    decision = decide_escalation(det=det, classifier_result=cr)

    if decision.degraded:
        note_classifier_error(store, now=now)
        _maybe_degraded_alert(deps, now=now)

    advance_funnel(store, contact_id, stage_signal=decision.stage_signal, escalated=decision.escalate)

    if det is not None and det.tag in ("unbacked_claim", "forbidden_reply"):
        # Гардрейл-подавление: НЕ отправляем ни выдуманную цену/срок, ни
        # запрещённый термин (рубли/росбанк — brand-safety). НО подавление ≠
        # тишина: на вопрос об оплате лид обязан получить КОРРЕКТНЫЙ ответ
        # (верные способы, без запрещённого слова) — иначе бот молчит на «как
        # заплатить», и мы теряем продажу, защитив репутацию. Даём безопасный
        # ответ + эскалируем владельцу.
        print(f"  [escalation flag] {det.tag} for {contact_id}: {reply!r}")
        safe = cfg.settings.safe_payment_reply
        # Защита от само-простреливания: если клиент вписал запрещённый термин в
        # сам safe_payment_reply — не отправляем его, откатываемся к нейтральному.
        if det.tag == "forbidden_reply" and safe and forbidden_mention(safe, cfg.settings.forbidden_terms) is None:
            reply = safe
        else:
            if det.tag == "forbidden_reply" and safe:
                log.warning("safe_payment_reply сам содержит запрещённый термин — не использую")
            reply = (
                f"Хороший вопрос — уточню детали и вернусь. "
                f"Если удобно, позову {cfg.settings.owner_id}."
            )

    if decision.escalate and deps.notifier is not None:
        _post_escalation_card(deps, contact_id, det=det, cr=cr, now=now)
    return reply


def _post_escalation_card(deps: "Deps", contact_id: str, *, det, cr, now: float) -> None:
    """Собрать и отправить карточку эскалации. Имя/ссылку строит раннер
    (`deps.escalation_card`, у него есть Telethon-entity); без него — текстовый
    фоллбек по contact_id. Никогда не роняет process_batch (DEV-18)."""
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
        # Fix 2: один лид = одна карточка. Пока по контакту есть НЕ-закрытая
        # карточка эскалации (владелец ещё не тапнул), повторная эскалация
        # РЕДАКТИРУЕТ её (обновляет причину), а не шлёт новую. route_callback
        # чистит active-флаг при любом действии владельца → следующая эскалация
        # после его реакции = новая карточка.
        active = deps.store.get_runtime_flag(esc_active_key(contact_id))
        if active:
            deps.notifier.update_card(CardHandle(ref=active), card)
            return
        handle = deps.notifier.notify(card)
    except Exception:
        log.exception("escalation card FAILED for %s", contact_id)
        return
    if handle is not None:
        deps.store.set_runtime_flag(esc_active_key(contact_id), handle.ref, ts=now)
        try:
            msg_id = int(handle.ref.split(":")[-1])
            deps.store.add_card(msg_id=msg_id, contact_id=contact_id, kind="escalation", ts=now)
        except Exception:
            log.warning("could not record escalation card handle %r", handle, exc_info=True)


def _maybe_degraded_alert(deps: "Deps", *, now: float) -> None:
    """Классификатор деградировал (§6): алертим владельца ОДИН раз за окно
    (не штормим), только если ошибок за окно больше порога."""
    control = deps.control or ControlConfig()
    window = control.status_window_hours * 3600.0
    if not classifier_degraded(
        deps.store, now=now, window_seconds=window, threshold=control.classifier_error_threshold):
        return
    last = deps.store.get_runtime_flag("classifier_degraded_alerted_ts")
    if last and (now - float(last)) < window:
        return
    deps.store.set_runtime_flag("classifier_degraded_alerted_ts", str(now), ts=now)
    if deps.notifier is None:
        return
    count = deps.store.count_events("classifier_error", since_ts=now - window)
    text = console_text(
        "degraded_alert", deps.cfg.settings.language,
        count=count, hours=control.status_window_hours)
    try:
        deps.notifier.notify(Card(
            kind="alert", contact_id="", text_html=escape_html(text),
            buttons=[], reply_hints=[]))
    except Exception:
        log.exception("degraded-classifier alert FAILED to send")


def process_batch(
    contact_id: str, incoming: list[str], transport: Transport, deps: Deps,
    *, missed_age_seconds: float | None = None,
) -> None:
    """Coalesce the batch, guard on rate limits, decide a reply
    (disclosure > guardrails > brain), then deliver it via the humanizer's
    action plan (compose_reply): Pause/Typing/Say interpreted in order.

    `missed_age_seconds`: when this batch is a message that arrived while the
    bot was OFFLINE (catch-up), how long the oldest message waited. It drives
    the "sorry for the pause" acknowledgement on the brain path. None (default)
    = a live message, no apology."""
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

    if is_bot_question(text):
        reply = honest_disclosure(
            owner_id=deps.cfg.settings.owner_id,
            persona_line=_persona_first_line(deps.cfg.persona),
            language=deps.cfg.settings.language,
        )
    else:
        reply = deps.brain.reply(
            deps.store.history(contact_id),
            context_note=missed_reply_context(missed_age_seconds),
        )

    # Арка 3B: единый проход эскалации (детерминированный слой + классификатор),
    # оживление воронки и — при эскалации — карточка владельцу. Здесь же
    # остаётся гардрейл-переписывание необеспеченного обещания (перенесено из
    # инлайна в _escalation_pass), чтобы Аня не отправила выдуманную цену.
    reply = _escalation_pass(deps, contact_id, incoming_text=text, reply=reply, now=deps.clock())

    now_hour = _dt.datetime.fromtimestamp(deps.clock()).hour
    actions = H.compose_reply(
        reply, deps.rng, deps.cfg.settings.timings, deps.cfg.settings.work_hours, now_hour,
    )
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
            transport.send(action.text)
            deps.store.add_message(contact_id, "assistant", action.text, ts=deps.clock())


def _build_llm(cfg: Config, mode: str):
    if mode == "real" or (mode == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM(cfg.settings.model)
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
    llm = _build_llm(cfg, args.llm)
    deps = Deps(
        cfg=cfg, store=Store(args.db), brain=Brain(llm, cfg),
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
