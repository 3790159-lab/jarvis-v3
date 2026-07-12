# -*- coding: utf-8 -*-
"""Этап 3, кирпич #1 — генератор IG-подписей (Master-Plan «Мастер-Инстаграм»).

Строит подпись (caption) к посту Instagram из короткого brief'а: 2-4
предложения, эмодзи умеренно, хэштеги миксом широких и нишевых, без тем из
``brief.get('forbidden', [])``. Ответ чистится от markdown-фенсов и преамбул,
затем усекается до лимита IG (2200 символов).

Чистый модуль: ноль сети (тот же принцип, что у ``app/services/devtask/
suggest.py``) — :func:`generate_caption` требует ``ask_llm`` явно, а не
лениво падает на реальный биллируемый вызов. Обвязка бота
(``tools/jarvis_smart_telegram_control.py``) зовёт LLM под ``guard_spend`` +
``record_cost`` тем же паттерном, что ``/suggest_tasks``.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 500
EST_USD = 0.02

IG_CAPTION_MAX_LEN = 2200  # жёсткий лимит длины caption в Instagram
DEFAULT_HASHTAGS_COUNT = 8
DEFAULT_LANG = "uk"
DEFAULT_TONE: Tuple[str, ...] = ("дружній", "живий")

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\n?|\n?```$")
_PREAMBLE_RE = re.compile(
    r"^\s*(вот|ось|here'?s|here is|caption|подпись|підпис|текст)[^\n]{0,40}[:\-–—]\s*",
    re.IGNORECASE,
)
_HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)


def build_prompt(brief: Dict[str, Any]) -> Tuple[str, List[dict]]:
    """(system, messages) для одного LLM-вызова. Чисто, $0, без сети."""
    business = str(brief.get("business") or "").strip()
    topic = str(brief.get("topic") or "").strip()
    tone = brief.get("tone") or list(DEFAULT_TONE)
    cta = str(brief.get("cta") or "").strip()
    hashtags_count = int(brief.get("hashtags_count") or DEFAULT_HASHTAGS_COUNT)
    lang = str(brief.get("lang") or DEFAULT_LANG).strip()
    forbidden = brief.get("forbidden") or []

    system = (
        "Ты — SMM-копирайтер для Instagram. Напиши подпись (caption) к посту: "
        "2-4 предложения, эмодзи умеренно (как разметка абзацев, не конфетти), "
        f"в конце — ровно {hashtags_count} хэштегов миксом широких и нишевых "
        "(ниша+гео+бренд), каждый начинается с #, без пробелов внутри тега. "
        f"Язык ответа: {lang}. Структура: хук (1 строка) -> тело (польза/атмосфера) "
        "-> CTA. Ответ — ТОЛЬКО текст подписи целиком, без markdown-фенсов "
        "(```), без преамбулы вида 'вот подпись:', без пояснений до или после."
    )
    if forbidden:
        system += (
            " НИКОГДА не упоминай и не затрагивай запрещённые темы: "
            + "; ".join(str(f) for f in forbidden) + "."
        )

    lines = [
        "Бизнес: %s" % (business or "(не указан)"),
        "Тема поста: %s" % (topic or "(не указана)"),
        "Тон: %s" % ", ".join(str(t) for t in tone),
    ]
    if cta:
        lines.append("Желаемое действие (CTA): %s" % cta)
    return system, [{"role": "user", "content": "\n".join(lines)}]


def clean_caption(raw: Optional[str]) -> str:
    """Снять markdown-фенсы и типовую преамбулу LLM-ответа."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = _FENCE_RE.sub("", text).strip()
    text = _PREAMBLE_RE.sub("", text, count=1).strip()
    return text


def count_hashtags(caption: str) -> int:
    return len(_HASHTAG_RE.findall(caption or ""))


def enforce_length(caption: str, max_len: int = IG_CAPTION_MAX_LEN) -> str:
    """Обрезать caption до лимита IG (2200 символов), не разрывая с запасом."""
    if len(caption) <= max_len:
        return caption
    return caption[:max_len].rstrip()


_BRAND_OVERRIDE_KEYS = ("business", "tone", "lang", "cta", "forbidden", "hashtags_count")


def apply_brand(brief: Dict[str, Any], brand: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Подмешать клиентский бренд-конфиг (``clients/<name>/brand.md``) в ``brief``.

    ``brand`` (см. ``app.services.brand_config.load_brand_config``) переопределяет
    business/tone/lang/cta/forbidden/hashtags_count, если поле непустое. ``topic``
    — всегда из юзерского ввода, бренд его никогда не переопределяет. ``brand`` —
    None/пусто -> честная копия ``brief`` без изменений (нет конфига клиента —
    старое поведение).
    """
    merged = dict(brief)
    if not brand:
        return merged
    for key in _BRAND_OVERRIDE_KEYS:
        value = brand.get(key)
        if value not in (None, "", []):
            merged[key] = value
    return merged


def generate_caption(
    brief: Dict[str, Any],
    ask_llm: Callable[[str, List[dict]], str],
) -> str:
    """Сгенерировать IG caption по ``brief`` через ``ask_llm`` (билл-путь бота).

    ``ask_llm`` обязателен — модуль намеренно не лезет в сеть сам (money-safety
    зеркалит ``app/services/devtask/suggest.py``): пропуск аргумента — баг
    вызывающего кода, а не повод тихо потратить реальные деньги.
    """
    system, messages = build_prompt(brief)
    raw = ask_llm(system, messages)
    caption = clean_caption(raw)
    return enforce_length(caption)
