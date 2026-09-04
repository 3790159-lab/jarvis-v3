# -*- coding: utf-8 -*-
"""Неполный набор — НАЙДЕННЫЙ ДЕФЕКТ, а не «проверить нечем».

Сегодня дрил, увидев манифест без `counts`, отвечает `rc=2` «дрил не
состоялся» и НЕ пишет вердикт. Для старого формата это верно: без величин
сверять действительно нечем. Но с тех пор манифест научился объявлять
ожидаемый состав (`expected`) и полноту (`complete`), и тогда «набор неполон»
— это ЗНАНИЕ, а не незнание: мы точно знаем, что часть объектов не описана.
Такое обязано давать `rc=1` с ЗАПИСАННЫМ вердиктом `ok=false`, иначе лампа на
хосте увидит `drill_never`/`drill_stale` и прочтёт аварию как «ноутбук был
выключен».

🔴 КАКУЮ ВЕТКУ ЭТИ СТОРОЖА ИСПОЛНЯЮТ: разбор `expected`/`complete` в
`parse_manifest` и превращение расхождения в СПИСОК ПРИЧИН, который вызывающий
кладёт в `problems` (то есть в вердикт), вместо отказа `DrillNotRun`.

Сети нет: `parse_manifest` — чистая функция над текстом манифеста.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_drill.py"


def _drill():
    spec = importlib.util.spec_from_file_location("restore_drill_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["restore_drill_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


FP = "a" * 64
DB = "aaaa1111bbbb2222/db"
REQ = "aaaa1111bbbb2222/requisites.yaml"
DB2 = "cccc3333dddd4444/db"


def _entry(rel: str, counts: dict | None = None) -> dict:
    e = {"rel_path": rel, "sha256": "b" * 64, "size": 10}
    if counts is not None:
        e["counts"] = counts
    return e


def _text(files: list[dict], **extra) -> str:
    body = {"generated_at": "2026-09-04T01:01:00+00:00",
            "key_fingerprint": FP, "count": len(files), "files": files}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


# ── 1. полный набор с величинами — претензий нет ──────────────────────────

def test_a_complete_set_has_no_incompleteness_reasons():
    mod = _drill()
    raw = _text([_entry(DB, {"dialogs": 1}), _entry(REQ)],
                expected=[DB, REQ], complete=True)

    got = mod.parse_manifest(raw)
    assert got.get("incomplete") == [], got.get("incomplete")


# ── 2. набор объявил себя неполным ────────────────────────────────────────

def test_a_manifest_that_calls_itself_incomplete_yields_a_reason():
    """СЕГОДНЯ ЭТО КРАСНОЕ. `complete: false` — это ЗНАНИЕ о дефекте."""
    mod = _drill()
    raw = _text([_entry(REQ)], expected=[DB, REQ, DB2], complete=False)

    got = mod.parse_manifest(raw)
    reasons = got.get("incomplete")
    assert reasons, "манифест объявил себя неполным, а дрил этого не заметил"
    assert any("complete" in r or "полн" in r.lower() for r in reasons), reasons


# ── 3. ожидаемое не описано — даже если complete соврал ───────────────────

def test_expected_entries_missing_from_files_are_named(monkeypatch):
    """Признаку `complete` одного мало: он может соврать, а список — нет.

    Здесь `complete: true`, но два ожидаемых объекта в `files` отсутствуют.
    Сверять надо СПИСКИ, иначе полнота держится на честном слове писателя."""
    mod = _drill()
    raw = _text([_entry(REQ)], expected=[DB, REQ, DB2], complete=True)

    got = mod.parse_manifest(raw)
    reasons = got.get("incomplete")
    assert reasons, "расхождение expected и files не замечено"
    joined = " ".join(reasons)
    assert DB in joined and DB2 in joined, (
        "причина не называет ПОИМЁННО, чего не хватает: %r" % (reasons,))


# ── 4. старый манифест — поведение прежнее ────────────────────────────────

def test_an_old_manifest_without_counts_is_still_not_run():
    """Обратная совместимость. Без `expected`/`complete` мы действительно НЕ
    ЗНАЕМ, полон ли набор, и «проверить нечем» остаётся честным ответом.

    Смешать эти два случая значило бы записать «бэкап негоден» там, где мы
    ничего не мерили ([[jarvis-absence-is-not-contradiction]])."""
    mod = _drill()
    raw = _text([_entry(REQ)])          # ни expected, ни complete, ни counts

    with pytest.raises(mod.DrillNotRun):
        mod.parse_manifest(raw)


# ── 5. неполнота ПЕРЕВЕШИВАЕТ отсутствие величин ──────────────────────────

def test_incompleteness_wins_over_the_missing_counts_refusal():
    """СЕГОДНЯ ЭТО КРАСНОЕ, и это главный порядок в этой правке.

    Ровно сегодняшний боевой случай: манифест затёрт частичным прогоном, в
    нём одна запись без `counts` И объявленная неполнота. Отказать «нечем
    проверить» значило бы промолчать о найденном дефекте."""
    mod = _drill()
    raw = _text([_entry(REQ)], expected=[DB, REQ, DB2], complete=False)

    got = mod.parse_manifest(raw)       # не должно бросить DrillNotRun
    assert got.get("incomplete"), got
