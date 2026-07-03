"""Seam: приём ОДНОЙ свободной motion-строки для одиночных команд.

Выравнивает ``/animate`` и ``/videoref`` с batch-потоком (у которого свой промт
уже есть). Чисто текстовый и БЕСПЛАТНЫЙ слой: ни сети, ни платных вызовов —
только состояние ожидания + clamp по капу ``WAVESPEED_PROMPT_MAX_CHARS``.

Контракт (control-слой дёргает его из текст-роутера и из кнопок):
    arm(chat_id, kind)      — включить ожидание строки (kind: "animate"/"videoref")
    is_awaiting(chat_id)    — ждём ли строку от этого чата
    awaiting_kind(chat_id)  — какой kind ждём (для label «промт задан»)
    disarm(chat_id)         — снять ожидание (сброс / переключение на Grok)
    consume(chat_id, text)  — ЕДИНОРАЗОВО забрать строку → ConsumeResult|None

``consume`` возвращает ``None``, если чат НЕ в ожидании — тогда роутер идёт
дальше (batch-intercept, plain-text). Иначе снимает ожидание, стрипает и
клампит текст, возвращает результат; пустой после стрипа motion="" — контрол-
слой решает игнорировать (не подставлять). Seam про pending-словари не знает.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# NB: только бесплатный clamp_prompt — НИКАКИХ Grok/движков (money-tooth).
from app.services.block_m2_video.prompt_assembly import clamp_prompt

# chat_id -> kind ("animate" | "videoref")
_AWAITING: dict[int, str] = {}


@dataclass(frozen=True)
class ConsumeResult:
    kind: str
    motion: str
    truncated: bool


def arm(chat_id: int, kind: str) -> None:
    _AWAITING[int(chat_id)] = kind


def is_awaiting(chat_id: int) -> bool:
    return int(chat_id) in _AWAITING


def awaiting_kind(chat_id: int) -> str | None:
    return _AWAITING.get(int(chat_id))


def disarm(chat_id: int) -> None:
    _AWAITING.pop(int(chat_id), None)


def consume(chat_id: int, text: str) -> ConsumeResult | None:
    kind = _AWAITING.pop(int(chat_id), None)
    if kind is None:
        return None
    stripped = (text or "").strip()
    cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))
    motion, truncated = clamp_prompt(stripped, cap)
    return ConsumeResult(kind=kind, motion=motion, truncated=truncated)
