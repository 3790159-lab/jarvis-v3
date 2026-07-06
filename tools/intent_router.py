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


@dataclass(frozen=True)
class Candidate:
    cmd: str
    score: float
    arg: str = ""            # извлечённый параметр (IR-3; в IR-1 пусто)


@dataclass(frozen=True)
class IRResult:
    decision: str            # "route" | "clarify" | "uncertain" | "none"
    candidates: Tuple[Candidate, ...] = ()
    reason: str = ""


# Пороги (стартовые; калибруются вживую на реальных фразах).
ROUTE_FLOOR = 0.72       # ниже — не уверенная команда
CLARIFY_GAP = 0.15       # зазор топ-1 vs топ-2, чтобы не гадать
UNCERTAIN_FLOOR = 0.45   # 0.45..0.72 → отдать LLM-слою (IR-2, admin)
CLARIFY_MAX = 3          # сколько кандидатов показать на уточнении


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


def _score(qn: str, qtok: FrozenSet[str], entry: Entry) -> float:
    """Оценка соответствия нормализованной фразы команде (0..1)."""
    best = 0.0
    for p in entry.phrases:
        if not p:
            continue
        if qn == p:
            return 1.0
        if p in qn or qn in p:
            best = max(best, 0.9)
        r = SequenceMatcher(None, qn, p).ratio()
        if r > best:
            best = r
    if qtok and entry.tokens:
        overlap = len(qtok & entry.tokens) / len(qtok)
        if overlap > best:
            best = overlap
    return best


def resolve(text: str, role: str,
            friend_allowed: Optional[FrozenSet[str]] = None) -> IRResult:
    """Офлайн-резолв фразы → решение (route/clarify/uncertain/none). $0, чисто.

    ``friend`` матчится только против ``friend_allowed`` (source-of-truth прав
    из bot.py, инъекция — модуль не импортирует control-файл). Если для friend
    список не передан, откат на menu-видимость (``entry.friend``).
    """
    qn = _normalize(text)
    if not qn:
        return IRResult("none")
    qtok = _tokens(qn)

    corpus = build_corpus()
    if role == "friend":
        if friend_allowed is not None:
            entries = [e for e in corpus.values() if e.cmd in friend_allowed]
        else:
            entries = [e for e in corpus.values() if e.friend]
    else:
        entries = list(corpus.values())

    scored = sorted(
        ((e.cmd, _score(qn, qtok, e)) for e in entries),
        key=lambda cs: (-cs[1], cs[0]),
    )
    if not scored:
        return IRResult("none")

    best = scored[0][1]
    second = scored[1][1] if len(scored) > 1 else 0.0

    if best >= ROUTE_FLOOR:
        if best - second >= CLARIFY_GAP:
            return IRResult("route", (Candidate(scored[0][0], best),))
        near = [Candidate(c, s) for c, s in scored
                if best - s < CLARIFY_GAP][:CLARIFY_MAX]
        return IRResult("clarify", tuple(near))
    if best >= UNCERTAIN_FLOOR:
        cands = tuple(Candidate(c, s) for c, s in scored[:CLARIFY_MAX])
        return IRResult("uncertain", cands)
    return IRResult("none")
