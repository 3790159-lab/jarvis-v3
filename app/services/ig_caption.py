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

import json
import os
import random
import re
import threading
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 500
EST_USD = 0.02

IG_CAPTION_MAX_LEN = 2200  # жёсткий лимит длины caption в Instagram
DEFAULT_HASHTAGS_COUNT = 8
DEFAULT_LANG = "uk"
DEFAULT_TONE: Tuple[str, ...] = ("дружній", "живий")

# ── хэштег-ротация (brand.md §hashtag_baskets, skill smm-instagram §6) ────────
HASHTAG_BROAD_COUNT = 2
HASHTAG_NICHE_COUNT = 3
HASHTAG_BRAND_COUNT = 1
HASHTAG_HISTORY_DEPTH = 2  # не повторять комбинацию N последних постов

_DEFAULT_HASHTAG_HISTORY_FILE = Path("state") / "ig_hashtag_history.json"
_HASHTAG_HISTORY_LOCK = threading.RLock()

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


_BRAND_OVERRIDE_KEYS = (
    "business", "tone", "lang", "cta", "forbidden", "hashtags_count", "hashtag_baskets",
)


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


def pick_hashtags(
    baskets: Dict[str, Any],
    seed: str,
    avoid: Optional[List[List[str]]] = None,
) -> List[str]:
    """Детерминированный (сид ``seed``) микс ``HASHTAG_BROAD_COUNT`` широких +
    ``HASHTAG_NICHE_COUNT`` нишевых + ``HASHTAG_BRAND_COUNT`` брендовый хэштег
    из корзин ``hashtag_baskets`` (``clients/<name>/brand.md``, skill
    smm-instagram §6). Один и тот же ``seed`` -> один и тот же первый вариант
    (воспроизводимо). Если он совпадает (без учёта порядка) с одной из
    комбинаций ``avoid`` (недавние посты клиента), детерминированно берёт
    следующий вариант из того же ``random.Random(seed)`` до 50 попыток —
    сама последовательность попыток тоже целиком определяется ``seed``.
    """
    broad = list(baskets.get("broad") or [])
    niche = list(baskets.get("niche") or [])
    brand = list(baskets.get("brand") or [])
    avoid_set = {tuple(sorted(c)) for c in (avoid or [])}
    rng = random.Random(seed)
    combo: List[str] = []
    for _ in range(50):
        combo = (
            rng.sample(broad, min(HASHTAG_BROAD_COUNT, len(broad)))
            + rng.sample(niche, min(HASHTAG_NICHE_COUNT, len(niche)))
            + rng.sample(brand, min(HASHTAG_BRAND_COUNT, len(brand)))
        )
        if tuple(sorted(combo)) not in avoid_set:
            break
    return combo


def _hashtag_history_file() -> Path:
    raw = os.getenv("IG_HASHTAG_HISTORY_FILE", "").strip()
    return Path(raw) if raw else _DEFAULT_HASHTAG_HISTORY_FILE


def load_hashtag_history(client: Optional[str]) -> List[List[str]]:
    """Последние (до ``HASHTAG_HISTORY_DEPTH``) комбинации хэштегов клиента.

    Читает ``state/ig_hashtag_history.json`` (env override
    ``IG_HASHTAG_HISTORY_FILE`` для тестовой изоляции — тот же паттерн, что
    ``app.services.ig_schedule``). Нет клиента/файла/записи/битый JSON ->
    честный ``[]``."""
    key = client or "_default"
    f = _hashtag_history_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    combos = data.get(key)
    return combos if isinstance(combos, list) else []


def save_hashtag_combo(client: Optional[str], combo: List[str]) -> None:
    """Дописать выбранную комбинацию в историю клиента (атомарная запись —
    ``.tmp`` + ``os.replace``, ``RLock``), храня только последние
    ``HASHTAG_HISTORY_DEPTH``."""
    key = client or "_default"
    with _HASHTAG_HISTORY_LOCK:
        f = _hashtag_history_file()
        data: Dict[str, Any] = {}
        if f.exists():
            try:
                loaded = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                data = {}
        recent = data.get(key)
        recent = recent if isinstance(recent, list) else []
        recent = (recent + [list(combo)])[-HASHTAG_HISTORY_DEPTH:]
        data[key] = recent
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, f)


def rotate_hashtags(
    baskets: Optional[Dict[str, Any]],
    *,
    client: Optional[str],
    date: Optional[_date] = None,
) -> Optional[List[str]]:
    """Ротация хэштегов поста: сид от ``date`` (дефолт — сегодня) + ``client``,
    не повторяет комбинацию последних постов клиента (state на диске, см.
    :func:`load_hashtag_history`/:func:`save_hashtag_combo`). Нет ``baskets``
    (клиент без секции ``hashtag_baskets`` в ``brand.md``, или клиент вообще
    без ``brand.md``) -> ``None`` — старое поведение (хэштеги решает LLM)."""
    if not baskets:
        return None
    day = date or _date.today()
    seed = f"{client or '_default'}:{day.isoformat()}"
    history = load_hashtag_history(client)
    combo = pick_hashtags(baskets, seed, avoid=history)
    if combo:
        save_hashtag_combo(client, combo)
    return combo


def replace_hashtags(caption: str, hashtags: List[str]) -> str:
    """Заменить хэштеги в готовом ``caption`` на ``hashtags`` (ротация корзин).

    Текст без тегов остаётся как есть; новые теги — единым блоком в конец
    (структура caption: хук -> тело -> CTA -> хэштеги, skill smm-instagram §3).
    """
    body = _HASHTAG_RE.sub("", caption or "")
    body = re.sub(r"[ \t]+(?=\n)", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    tags = " ".join(hashtags)
    if not body:
        return tags
    return f"{body}\n\n{tags}" if tags else body


def generate_caption(
    brief: Dict[str, Any],
    ask_llm: Callable[[str, List[dict]], str],
    *,
    client: Optional[str] = None,
    date: Optional[_date] = None,
) -> str:
    """Сгенерировать IG caption по ``brief`` через ``ask_llm`` (билл-путь бота).

    ``ask_llm`` обязателен — модуль намеренно не лезет в сеть сам (money-safety
    зеркалит ``app/services/devtask/suggest.py``): пропуск аргумента — баг
    вызывающего кода, а не повод тихо потратить реальные деньги.

    Если ``brief["hashtag_baskets"]`` присутствует (см. :func:`apply_brand`),
    хэштеги LLM-ответа заменяются детерминированной ротацией корзин
    (:func:`rotate_hashtags`) — ``client``/``date`` определяют сид и историю
    клиента. Нет корзин -> хэштеги как решил LLM (старое поведение).
    """
    system, messages = build_prompt(brief)
    raw = ask_llm(system, messages)
    caption = enforce_length(clean_caption(raw))
    combo = rotate_hashtags(brief.get("hashtag_baskets"), client=client, date=date)
    if combo:
        caption = enforce_length(replace_hashtags(caption, combo))
    return caption
