# app/services/vizir/task_acceptance.py
# -*- coding: utf-8 -*-
"""Детерминированная приёмка произвольной /task — ловит структурные галлюцинации/пустышки.

Слой 1: указатель/отсрочка (1a completion+location co-occurrence; 1b уточнение/невозможность).
Слой 2: build-task из goal → ответ ДОЛЖЕН содержать реальный код, не прозу о нём.
$0, без платного судьи (задел под опц. судью — off, отдельной аркой). См.
docs/superpowers/specs/2026-07-02-vizir-honest-acceptance-design.md.
"""
from __future__ import annotations

import re
from pathlib import Path

from .hermes_acceptance import AcceptanceResult

# ---- Слой 1: маркеры указателя/отсрочки (RU+EN, low-case сравнение) ----
_COMPLETION = ("готово", "создан", "создана", "создал", "сохран", "записал",
               "сделал", "done", "created", "saved", "generated", "ready", "built")
_LOCATION_PHRASES = ("по адресу", "по пути", "файл находится", "файл на",
                     "на рабочем столе", "открой этот файл", "открой файл",
                     "открой в браузере", "saved to", "saved at", "file is located",
                     "located at", "created at", "open the file", "open this file")
# файловый путь: диск-буква + \ или / (со \b, чтобы "http://" НЕ матчилось), либо POSIX-каталоги
_PATH_RE = re.compile(r"\b[a-z]:[\\/]|/home/|/root/|/mnt/|/tmp/|~[\\/]", re.I)
_DEFERRAL = ("не могу найти", "не удалось найти", "не могу определить",
             "укажите путь", "укажите папку", "укажите где", "предоставьте доступ",
             "предоставьте путь", "куда сохранить", "где находится", "уточните",
             "пожалуйста, укажите", "please provide", "please specify",
             "could not find", "couldn't find", "unable to locate", "where is",
             "where should i", "provide the path")

# ---- Слой 2: build-task (из goal) требует реальный код в ответе ----
_BUILD_VERBS = ("сдела", "созда", "напиши", "сгенерир", "свёрстай", "сверстай",
                "собери", "запили", "build", "make", "create", "write",
                "generate", "code", "implement")
_ARTIFACT_NOUNS = ("игр", "сайт", "страниц", "html", "css", "скрипт", "код",
                   "приложени", "компонент", "виджет", "форм", "калькулятор",
                   "таблиц", "бот", "лендинг", "game", "app", "page", "site",
                   "script", "component", "widget", "form", "landing", "file")
_CODE_MARKERS = ("<html", "<!doctype html", "<script", "<style", "<body", "<div",
                 "</", "function ", "def ", "class ", "=>", "const ", "let ",
                 "var ", "import ", "return ")
_CODE_FENCE = re.compile(r"```[^\n]*\n.+?```", re.S)


def _is_build_task(goal_low: str) -> bool:
    return (any(v in goal_low for v in _BUILD_VERBS)
            and any(n in goal_low for n in _ARTIFACT_NOUNS))


def _has_code(low: str, raw: str) -> bool:
    return any(m in low for m in _CODE_MARKERS) or bool(_CODE_FENCE.search(raw))


def _text(value: dict) -> str:
    html = value.get("final_response") or ""
    if not html:
        path = value.get("artifact_path")
        if path and Path(path).exists():
            html = Path(path).read_text(encoding="utf-8", errors="replace")
    return html or ""


def _layer1(low: str, raw: str, reasons: list) -> None:
    # 1a: completion-токен СО-ВСТРЕЧАЕТСЯ с external-location → "ложное готово"
    if any(c in low for c in _COMPLETION) and (
            bool(_PATH_RE.search(raw)) or any(p in low for p in _LOCATION_PHRASES)):
        reasons.append("вернул ссылку/путь на файл вместо самого результата — "
                       "верни полный артефакт (код) прямо в ответе, не ссылку и не описание")
    # 1b: уточнение/невозможность → агент не сделал, попросил ввод
    if any(d in low for d in _DEFERRAL):
        reasons.append("задача не выполнена: агент попросил ввод / не смог продолжить "
                       "вместо результата")


def accept_task(value: dict, goal: str) -> AcceptanceResult:
    """v1 детерминированная приёмка. Собираем ВСЕ причины (не короткое замыкание)."""
    reasons: list[str] = []
    # Gate 0 — run завершился
    stopped = value.get("stopped_reason")
    if stopped and stopped != "completed":
        reasons.append(f"run did not complete (stopped_reason={stopped})")
    # Gate 1 — непустой вывод
    raw = _text(value)
    if not raw:
        reasons.append("empty output (no final_response/artifact produced)")
        return AcceptanceResult(accepted=False, reasons=reasons)
    low = raw.lower()
    # Gate 2 — Слой 1
    _layer1(low, raw, reasons)
    # Gate 3 — Слой 2: если задача просит артефакт, ответ обязан содержать реальный код
    if _is_build_task((goal or "").lower()) and not _has_code(low, raw):
        reasons.append("вернул описание/текст вместо реального кода — "
                       "верни сам артефакт (полный код) инлайн в ответе")
    return AcceptanceResult(accepted=not reasons, reasons=reasons)
