# -*- coding: utf-8 -*-
"""Этап 3, кирпич #1 — оркестратор первого живого IG-поста (`/ig_post`).

Склейка уже готовых кубиков в один флоу:

    локальный файл ──host_for_ig()──▶ public URL (R2)
                                         │
    ig_caption.generate_caption() ──▶ подпись
                                         │
    превью-карточка [📤 Опубликовать]/[✏️ Переген]/[Отмена]
                                         │  (тап [📤] — необратимо)
    InstagramAPI.publish_photo() ──▶ media_id + permalink

Этот модуль — ЧИСТАЯ логика (парсинг аргументов, резолв источника, тексты
карточек, классификация ошибок квоты). Ноль сети, $0. Необратимый публикующий
вызов живёт в :mod:`app.services.instagram_api` и гейтится тапом [📤] в обвязке
бота (семантика money-gate: fail-closed — любой сбой = НЕ публикуем + честная
ошибка). Обвязка/кнопки/guard_spend — ``tools/jarvis_smart_telegram_control.py``.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

__all__ = [
    "IGPostError",
    "IG_POST_PENDING_KEY",
    "LAST_TOKENS",
    "QUOTA_CODES",
    "QUOTA_SUBCODES",
    "parse_ig_post_args",
    "parse_ig_post_caption",
    "is_last_token",
    "resolve_source",
    "build_preview_text",
    "build_published_text",
    "is_quota_error",
    "format_publish_error",
]

# Ключ pending-состояния карточки в общем ``state`` бота (по образцу
# ``pending_confirm``): хранит {photo_url, caption, topic, source} между
# показом превью и тапом [📤]/[✏️]/[Отмена].
IG_POST_PENDING_KEY = "pending_ig_post"

# Токены-псевдонимы источника «взять последнюю генерацию» вместо пути к файлу.
LAST_TOKENS = frozenset({"last", "-", "последняя", "послед", "last_gen", "остання"})

# Сигналы лимита публикаций Graph API. 25/50 постов на аккаунт за 24ч
# (см. references/ig-api.md): publishing limit — code 9 / subcode 2207042;
# generic application rate limit — code 4/17/32/613.
QUOTA_CODES = frozenset({4, 9, 17, 32, 613})
QUOTA_SUBCODES = frozenset({2207042, 2207051})


class IGPostError(RuntimeError):
    """Честная ошибка подготовки поста (плохой источник/аргументы). $0."""


def parse_ig_post_args(query: Optional[str]) -> Tuple[str, str]:
    """``<источник> <тема...>`` → ``(source, topic)``.

    Источник — первый токен (путь к файлу или ключевое слово «last»), тема —
    весь остаток строки (может содержать пробелы). Пустой ввод → ``("", "")``.
    """
    parts = (query or "").strip().split(None, 1)
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (parts[0], parts[1].strip())


def parse_ig_post_caption(caption: Optional[str]) -> Tuple[bool, str]:
    """Разобрать подпись к фото как команду ``/ig_post``.

    Возвращает ``(is_ig_post, topic)``. Срабатывает, когда первый токен подписи —
    ровно ``/ig_post`` (без учёта регистра, с опциональным ``@botname``); тема —
    весь остаток. Иначе ``(False, "")``. Не матчит другие команды (например
    ``/ig_poster``), чтобы фото-подписи-вопросы шли в обычный анализ.
    """
    parts = (caption or "").strip().split(None, 1)
    if not parts:
        return (False, "")
    cmd = parts[0].split("@", 1)[0].lower()
    if cmd != "/ig_post":
        return (False, "")
    topic = parts[1].strip() if len(parts) > 1 else ""
    return (True, topic)


def is_last_token(source: Optional[str]) -> bool:
    """True, если ``source`` — псевдоним «последняя генерация», а не путь."""
    return (source or "").strip().lower() in LAST_TOKENS


def resolve_source(source: Optional[str], last_path: Optional[str]) -> str:
    """Свести источник к пути к файлу.

    Пустой источник → :class:`IGPostError`. Ключевое слово «last» → ``last_path``
    (или :class:`IGPostError`, если истории нет). Иначе — сам ``source`` как путь.
    """
    src = (source or "").strip()
    if not src:
        raise IGPostError("укажи путь к медиа или 'last' (последняя генерация)")
    if is_last_token(src):
        if not last_path:
            raise IGPostError(
                "нет сохранённой последней генерации — пришли путь к файлу"
            )
        return str(last_path)
    return src


def build_preview_text(photo_url: str, caption: str, topic: str) -> str:
    """Текст превью-карточки перед публикацией: URL медиа + подпись + тема."""
    topic = (topic or "").strip()
    header = "🖼 Превью IG-поста"
    if topic:
        header += f" · тема: {topic}"
    return (
        f"{header}\n\n"
        f"Медиа: {photo_url}\n\n"
        f"Подпись:\n{caption}\n\n"
        "Публикация необратима. Проверь и тапни [📤 Опубликовать]."
    )


def build_published_text(permalink: Optional[str], media_id: str) -> str:
    """Сообщение об успешной публикации: ссылка (или media_id, если её нет)."""
    if permalink:
        return f"✅ Опубликовано в Instagram:\n{permalink}"
    return (
        f"✅ Опубликовано в Instagram (media_id={media_id}).\n"
        "Ссылку (permalink) получить не удалось — пост уже в ленте."
    )


def is_quota_error(code: Any, subcode: Any = None) -> bool:
    """True, если Graph-код/subcode указывают на исчерпание лимита публикаций."""
    return code in QUOTA_CODES or subcode in QUOTA_SUBCODES


def format_publish_error(exc: Any) -> str:
    """Честное user-facing сообщение о провале публикации (fail-closed).

    Спец-кейс — лимит 25/50 постов/24ч: подсказываем подождать. Иначе отдаём
    саму ошибку Graph API. Публикация НЕ состоялась в любом случае.
    """
    code = getattr(exc, "code", None)
    subcode = getattr(exc, "subcode", None)
    detail = str(exc).strip() or "неизвестная ошибка"
    if is_quota_error(code, subcode):
        return (
            "🚫 Достигнут лимит публикаций Instagram (до 25–50 постов за 24ч). "
            f"Пост НЕ опубликован — подожди и повтори. ({detail})"
        )
    return f"🚫 Публикация не удалась — пост НЕ размещён. ({detail})"
