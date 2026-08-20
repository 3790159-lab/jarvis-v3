# -*- coding: utf-8 -*-
"""Имена рантайм-файлов chatter — ОДНО определение на всех.

Отметка живости раннера существует в двух формах, и знать нужно обе:

  легаси          `state/chatter_heartbeat.txt`         — безымянный раннер
  мультиклиентная `state/chatter_heartbeat_<slug>.txt`  — раннер клиента

Почему это отдельный модуль, а не константа по месту. Имя собиралось В ШЕСТИ
местах независимо, и каждое новое место наследовало ДОГАДКУ предыдущего. Цена
догадки измерена: 16.08 проба `ops_watchdog` знала только легаси-форму, а
раннер писал клиентскую — «heartbeat 49398с тому» при ЖИВОМ процессе, `fail`
дорос до 1625 и не восстановился бы никогда. Сторож, всегда красный при
нормальной работе, — это фон, а не сторож.

Модуль намеренно БЕЗ ЗАВИСИМОСТЕЙ (только stdlib) и без импортов из `chatter`:
его читают и раннер, и панель, и служебные скрипты, и ни один из них не должен
тащить ради имени файла остальной мир.
"""
from __future__ import annotations

from pathlib import Path

# Каталог рантайм-состояния относительно корня репозитория.
STATE_DIR_NAME = "state"

# Легаси-форма: один безымянный раннер. Остаётся живой — ручной запуск без
# `--client` пишет именно её, и читатели обязаны её понимать.
CHATTER_BEAT_LEGACY_NAME = "chatter_heartbeat.txt"

# Клиентская форма. Префикс и суффикс объявлены ОТДЕЛЬНО, потому что по ним
# собирается и имя (запись), и маска (чтение), и разбирается слаг обратно —
# три операции, которые обязаны согласовываться.
CHATTER_BEAT_PREFIX = "chatter_heartbeat_"
CHATTER_BEAT_SUFFIX = ".txt"
CHATTER_BEAT_CLIENT_GLOB = CHATTER_BEAT_PREFIX + "*" + CHATTER_BEAT_SUFFIX


def chatter_beat_name(client: str | None) -> str:
    """Имя файла отметки. `None`/пусто → легаси-форма."""
    if not client:
        return CHATTER_BEAT_LEGACY_NAME
    return f"{CHATTER_BEAT_PREFIX}{client}{CHATTER_BEAT_SUFFIX}"


def chatter_beat_path(client: str | None, *, root: Path | None = None) -> Path:
    """Путь отметки. Без `root` — относительный, как у раннера (он живёт в
    корне репозитория и исторически пишет `state/...` относительно cwd)."""
    base = Path(STATE_DIR_NAME) if root is None else Path(root) / STATE_DIR_NAME
    return base / chatter_beat_name(client)


def chatter_beat_slug(name: str) -> str | None:
    """Слаг из имени файла, либо None для легаси-формы и чужих имён.

    Обратная операция к `chatter_beat_name`, и она здесь не для симметрии:
    читатели, перечисляющие файлы маской, обязаны узнавать КЛИЕНТА, и делали
    это разбором строки по месту — то есть ещё одной догадкой.
    """
    if name == CHATTER_BEAT_LEGACY_NAME:
        return None
    if not (name.startswith(CHATTER_BEAT_PREFIX)
            and name.endswith(CHATTER_BEAT_SUFFIX)):
        return None
    slug = name[len(CHATTER_BEAT_PREFIX):-len(CHATTER_BEAT_SUFFIX)]
    return slug or None
