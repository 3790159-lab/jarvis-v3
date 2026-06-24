# -*- coding: utf-8 -*-
"""Turn raw per-frame animate failure reasons into user-facing text.

``animate_batch`` records a raw engine reason per failed index (e.g. Seedance
``prediction failed: ... flagged as sensitive (E005)``). These helpers classify
that reason and render it two ways:

* :func:`friendly_failure_lines` — grouped by category with counts, plain
  language, NO raw codes — for the friend who just wants to know what happened.
* :func:`detailed_failure_lines` — per-index, raw reason kept — for the admin
  who needs to diagnose.
"""
from __future__ import annotations

_FRIENDLY = {
    "censored": (
        "контент отклонён цензурой движка (Seedance). "
        "Попробуй движок WaveSpeed или другое фото."
    ),
    "timeout": "движок не успел обработать (таймаут) — попробуй ещё раз.",
    "overloaded": "движок перегружен — попробуй чуть позже.",
    "engine_error": "ошибка движка.",
}

# Order used when rendering multiple categories together.
_ORDER = ["censored", "timeout", "overloaded", "engine_error"]

_MAX_RAW = 220


def classify_failure(reason: str) -> str:
    """Map a raw engine reason to a category key."""
    r = (reason or "").lower()
    if "e005" in r or "sensitive" in r or "flagged" in r:
        return "censored"
    if "timed out" in r or "timeout" in r:
        return "timeout"
    if "429" in r or "too many requests" in r:
        return "overloaded"
    return "engine_error"


def friendly_failure_lines(errors: dict[int, str]) -> list[str]:
    """Friend-facing: one line per category, ``⚠️ {n}: {plain text}``."""
    if not errors:
        return []
    counts: dict[str, int] = {}
    for reason in errors.values():
        cat = classify_failure(reason)
        counts[cat] = counts.get(cat, 0) + 1
    lines: list[str] = []
    for cat in _ORDER:
        if cat in counts:
            lines.append(f"  ⚠️ {counts[cat]}: {_FRIENDLY[cat]}")
    return lines


def detailed_failure_lines(errors: dict[int, str]) -> list[str]:
    """Admin-facing: header + one ``• #{1-based}: {raw reason}`` per index."""
    if not errors:
        return []
    lines = [f"  ⚠️ Провалы ({len(errors)}):"]
    for idx in sorted(errors):
        raw = (errors[idx] or "").strip()
        if len(raw) > _MAX_RAW:
            raw = raw[:_MAX_RAW] + "…"
        lines.append(f"    • #{idx + 1}: {raw}")
    return lines
