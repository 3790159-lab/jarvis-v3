"""Эскалация: детерминированный слой + оживление воронки (арка 3B).

ЧИСТЫЙ модуль — ноль сети, ноль LLM, ноль Telethon. Классификатор (LLM) живёт
отдельно в classifier.py. Здесь только бесплатные детерминированные триггеры,
которые работают, даже если классификатор/сеть лежат (спека §4, слой 1), и
тонкая обёртка над мёртвым `conversation.next_state` (§5).

ШОВ (§8): этот модуль ВЫЗЫВАЕТ `disclosure.is_bot_question`,
`guardrails.contains_unbacked_claim` и `conversation.next_state`, но НЕ правит
ни один из пяти core-файлов.
"""
from __future__ import annotations

from dataclasses import dataclass

from chatter.core.conversation import next_state
from chatter.core.disclosure import is_bot_question
from chatter.core.guardrails import contains_unbacked_claim

# Зеркалит conversation._TERMINAL (приватное там). Завершённый диалог не
# воскрешаем ни сигналом воронки, ни эскалацией.
_TERMINAL_STATES = frozenset({"closed", "dead"})

# Заголовки секции ключевых слов по языкам (settings.language). Значение
# заголовка не важно для парсинга по существу — важно найти начало списка.
_KEYWORD_HEADINGS = (
    "ключевые слова эскалации",   # ru
    "escalation keywords",        # en
    "ключові слова ескалації",    # uk
)


def parse_escalation_keywords(playbook: str) -> list[str]:
    """Достаёт детерминированные ключевые слова из playbook.md (§4: «Всё из
    playbook.md, не хардкод»).

    Конвенция: секция `## <один из _KEYWORD_HEADINGS>`, дальше пункты списка
    `- слово`, до следующего заголовка (`#`). Отсутствие секции → `[]` (слой
    ключевых слов просто выключен, это не ошибка). Слова casefold'ятся, чтобы
    матч был регистронезависимым.
    """
    lines = (playbook or "").splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            in_section = heading in _KEYWORD_HEADINGS
            continue
        if not in_section:
            continue
        if stripped.startswith("- "):
            word = stripped[2:].strip()
            if word:
                out.append(word.casefold())
    return out


@dataclass(frozen=True)
class EscalationReason:
    """Почему диалог эскалирован — для строки «почему» в карточке (§3)."""
    tag: str        # "keyword" | "bot_question" | "unbacked_claim" | "classifier"
    detail: str     # человеческая однострочная причина


def deterministic_escalation(
    *, incoming_text: str, reply: str, knowledge: str, keywords: list[str],
) -> EscalationReason | None:
    """Слой 1 (спека §4): бесплатные детерминированные триггеры. Работают, даже
    если классификатор/сеть лежат. Возвращает ПЕРВЫЙ сработавший триггер, иначе
    None. Порядок: ключевое слово во входящем → вопрос про бота → необеспеченное
    обещание в ответе.

    ШОВ: вызывает `is_bot_question`/`contains_unbacked_claim`, не правит их.
    """
    text = (incoming_text or "").casefold()
    for kw in keywords:
        if kw and kw in text:
            return EscalationReason(tag="keyword", detail=f"ключевое слово «{kw}»")
    if is_bot_question(incoming_text or ""):
        return EscalationReason(tag="bot_question", detail="спросили, бот ли это")
    if contains_unbacked_claim(reply or "", knowledge or ""):
        return EscalationReason(
            tag="unbacked_claim", detail="ответ обещал цену/срок вне базы знаний")
    return None


def advance_funnel(store, contact_id: str, *, stage_signal: str | None, escalated: bool) -> str:
    """Оживляет мёртвый `conversation.next_state` (§5): stage_signal
    классификатора гонит воронку new→qualifying→hot→escalated. Эскалация —
    внешний оверрайд (сильнее переходов воронки): уводит в 'escalated' сразу,
    даже если из текущего состояния такого перехода по сигналу нет.

    Завершённый диалог (closed/dead) не трогаем. Пишем в БД только при реальной
    смене состояния. ШОВ: вызывает `next_state`, не правит conversation.py.
    """
    current = store.get_or_create_contact(contact_id)["state"]
    if current in _TERMINAL_STATES:
        return current
    if escalated:
        new = "escalated"
    elif stage_signal:
        new = next_state(current, stage_signal)
    else:
        new = current
    if new != current:
        store.set_state(contact_id, new)
    return new


@dataclass(frozen=True)
class EscalationDecision:
    escalate: bool
    reason: str            # человеческое «почему» для карточки
    stage_signal: str | None
    degraded: bool


def decide_escalation(*, det: "EscalationReason | None", classifier_result) -> EscalationDecision:
    """Свести два слоя (§4). Детерминированный слой достоверен, поэтому его
    причина приоритетнее reason классификатора. Классификатор эскалирует ТОЛЬКО
    если он не деградировал (§6: мусорный/упавший классификатор не эскалирует).
    stage_signal берём у классификатора (даже при не-эскалации — воронку гонит
    сигнал стадии). `classifier_result` — ClassifierResult или None."""
    escalate = det is not None
    reason = det.detail if det is not None else ""
    stage_signal = None
    degraded = False
    if classifier_result is not None:
        degraded = classifier_result.degraded
        if not degraded:
            stage_signal = classifier_result.stage_signal
            if classifier_result.escalate:
                escalate = True
                if not reason:
                    reason = classifier_result.reason or "классификатор: горячий лид"
    return EscalationDecision(
        escalate=escalate, reason=reason, stage_signal=stage_signal, degraded=degraded)
