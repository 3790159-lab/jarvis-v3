# -*- coding: utf-8 -*-
"""IR-1: offline intent router — обычный текст → команда реестра меню.

Чистый слой ($0, без сети): строит матч-корпус из готового реестра
``tools/jarvis_menu.py`` (110 команд: подпись + native-описание + токены самой
команды + рукописные алиасы) и матчит фразу keyword+fuzzy (stdlib ``difflib``,
без внешних зависимостей). НЕ импортирует основной control-файл и НЕ ходит в
сеть — симметрия изоляции с ``jarvis_menu.py``. LLM-слой (IR-2, Haiku) и
telegram-обвязка живут снаружи.

Спека: docs/superpowers/plans/2026-07-06-intent-router-arc.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, FrozenSet, Optional, Tuple

from tools import jarvis_menu as _menu


# ── Рукописные алиасы — главный рычаг точности ─────────────────────────────
# Фразы обычной речи → команда. Расширяются по логам реальных промахов.
ALIASES: Dict[str, Tuple[str, ...]] = {
    "/health": ("что с ботом", "глянь бота", "как бот", "бот жив",
                "здоровье", "статус систем", "проверь бота"),
    "/git_status": ("что с гитом", "статус кода", "статус гита",
                    "незакоммиченное", "что в гите"),
    "/costs": ("что я потратил", "сколько потратил", "мои траты",
               "траты сегодня", "расходы", "сколько денег ушло"),
    "/my_stats": ("моя статистика", "мой лимит", "сколько я потратил",
                  "мои расходы"),
    "/menu_photo": ("фото блюда", "сфоткай блюдо", "фото для меню",
                    "снять блюдо"),
    "/regress": ("прогони тесты", "запусти тесты", "регресс", "прогон тестов"),
    "/logs_tail": ("покажи логи", "хвост логов", "последние логи"),
    "/smart_health": ("здоровье систем", "как системы", "статус провайдеров"),
}


@dataclass(frozen=True)
class Entry:
    cmd: str                 # "/health"
    friend: bool             # видна ли роли friend
    tokens: FrozenSet[str]   # объединение токенов всех фраз + токенов cmd
    phrases: Tuple[str, ...] # нормализованные фразы (label, native, aliases)


_WORD_RE = re.compile(r"[^0-9a-zA-Zа-яё]+")


def _normalize(text: str) -> str:
    """lower, ё→е, пунктуация→пробел, схлопнуть пробелы."""
    t = (text or "").lower().replace("ё", "е")
    t = _WORD_RE.sub(" ", t)
    return " ".join(t.split())


def _tokens(norm: str) -> FrozenSet[str]:
    """Токены нормализованной строки (слова длиной ≥ 2)."""
    return frozenset(w for w in norm.split() if len(w) >= 2)


def _native_descriptions() -> Dict[str, str]:
    """cmd (со слешем) → native-описание из setMyCommands-списков."""
    out: Dict[str, str] = {}
    for name, desc in (_menu.NATIVE_ADMIN_COMMANDS + _menu.NATIVE_FRIEND_COMMANDS):
        out.setdefault("/" + name.lstrip("/"), desc)
    return out


_CORPUS: Optional[Dict[str, Entry]] = None


def build_corpus() -> Dict[str, Entry]:
    """Матч-корпус: cmd → Entry. Кешируется на модуле (реестр статичен)."""
    global _CORPUS
    if _CORPUS is not None:
        return _CORPUS

    native = _native_descriptions()
    corpus: Dict[str, Entry] = {}
    for cat in _menu.MENU:
        for it in cat.items:
            phrases_raw = []
            if it.label:
                phrases_raw.append(it.label)
            if it.cmd in native:
                phrases_raw.append(native[it.cmd])
            phrases_raw.extend(ALIASES.get(it.cmd, ()))

            phrases = tuple(p for p in (_normalize(x) for x in phrases_raw) if p)

            toks = set()
            for p in phrases:
                toks |= _tokens(p)
            # токены самой команды: "/menu_photo" → {menu, photo}
            toks |= _tokens(it.cmd.lstrip("/").replace("_", " "))

            corpus[it.cmd] = Entry(
                cmd=it.cmd, friend=it.friend,
                tokens=frozenset(toks), phrases=phrases,
            )
    _CORPUS = corpus
    return corpus
