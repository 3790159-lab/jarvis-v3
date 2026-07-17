"""Классификатор эскалации (арка 3B, спека §4 слой 2): ОТДЕЛЬНЫЙ дешёвый
LLM-вызов, шов основного brain-ответа не трогаем. Возвращает
`{escalate, reason, stage_signal}`.

Fail-safe (§6, DEV-18): любой сбой вызова ИЛИ мусор в ответе → `degraded=True,
escalate=False`. Мы НЕ эскалируем на деградации (иначе классификатор, отвечающий
мусором, штормил бы владельца) и НЕ глотаем сбой молча — вызывающая сторона
считает деградации и при превышении порога алертит владельца.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

log = logging.getLogger("chatter.core.classifier")

# Допустимый словарь сигналов воронки — РОВНО те, что понимает
# conversation.next_state (сигналы переходов, не состояния). 'hot' — состояние,
# не сигнал, поэтому сюда не входит и будет приведён к None.
STAGE_SIGNALS = frozenset(
    {"engaged", "interested", "needs_human", "unknown_info", "bought", "ghosted"})

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_CLASSIFIER_MAX_TOKENS = 200


@dataclass(frozen=True)
class ClassifierResult:
    escalate: bool
    reason: str
    stage_signal: str | None
    degraded: bool = False


def _degraded() -> ClassifierResult:
    return ClassifierResult(escalate=False, reason="", stage_signal=None, degraded=True)


def parse_classifier_reply(raw: str) -> ClassifierResult:
    """Терпимый парсер ответа классификатора. Снимает ```-ограждения, находит
    первый {...}, json.loads. На ЛЮБОМ сбое → деградация (не эскалируем).
    Неизвестный stage_signal приводится к None, но escalate/reason всё равно
    честно читаются."""
    text = (raw or "").strip()
    if not text:
        return _degraded()
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return _degraded()
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return _degraded()
    if not isinstance(data, dict):
        return _degraded()
    signal = data.get("stage_signal")
    if signal not in STAGE_SIGNALS:
        signal = None
    return ClassifierResult(
        escalate=bool(data.get("escalate", False)),
        reason=str(data.get("reason", "") or ""),
        stage_signal=signal,
        degraded=False,
    )


def classifier_system_prompt(playbook: str, language: str) -> str:
    signals = ", ".join(sorted(STAGE_SIGNALS))
    return (
        "Ты — тихий классификатор диалога воронки продаж. Тебя НЕ видит клиент. "
        "По переписке реши: (1) нужно ли ПРЯМО СЕЙЧАС передать диалог живому "
        "владельцу (горячий лид, готов платить/бронировать, жалоба, нестандартный "
        "запрос вне плейбука); (2) на какой стадии воронки диалог.\n\n"
        f"=== ПЛЕЙБУК ВОРОНКИ ===\n{playbook}\n\n"
        "Ответь СТРОГО одним компактным JSON-объектом, без пояснений и без "
        "markdown:\n"
        '{"escalate": true|false, "reason": "<=120 символов, что хочет лид / '
        'почему эскалация>", "stage_signal": "<' + signals + '|null>"}\n'
        f"reason пиши на языке диалога ({language})."
    )


def build_classifier_messages(history: list[dict]) -> list[dict]:
    return [{"role": m["role"], "content": m["text"]} for m in history]


def classify(llm, *, playbook: str, language: str, history: list[dict]) -> ClassifierResult:
    """Один дешёвый вызов. НИКОГДА не бросает: сбой вызова → деградация (§6)."""
    try:
        raw = llm.complete(
            classifier_system_prompt(playbook, language),
            build_classifier_messages(history),
            max_tokens=_CLASSIFIER_MAX_TOKENS,
        )
    except Exception:
        log.exception("classifier LLM call failed — деградация, не эскалирую")
        return _degraded()
    return parse_classifier_reply(raw)


# --- деградация: счётчик + решение об алерте (спека §6, DEV-18) --------------
def note_classifier_error(store, *, now: float) -> None:
    """Считаем каждую деградацию как control-event. Не глотаем в пустоту."""
    store.add_event("classifier_error", ts=now)


def classifier_degraded(store, *, now: float, window_seconds: float, threshold: int) -> bool:
    """True, если ошибок классификатора за окно СТРОГО больше порога — тогда
    вызывающая сторона (дебаунсированно) алертит владельца «классификатор
    деградировал, эскалации сейчас только по ключевым словам»."""
    return store.count_events("classifier_error", since_ts=now - window_seconds) > threshold
