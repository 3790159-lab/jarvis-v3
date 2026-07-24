# -*- coding: utf-8 -*-
"""Тесты замера дельты стоимости слота обязательств (scripts/obligations_cost_delta.py).

Два живых урока Д-10 (2026-07-24), ради которых скрипт вообще правился:

1. `--since 0` (старый дефолт) тянул в baseline ходы прошлых дней с ДРУГИМ
   промптом/персоной → печатал +15.3% там, где сопоставимый замер даёт +2.1%.
   Дефолт окна = ТЕКУЩИЙ ДЕНЬ.
2. Первый ход после рестарта раннера платит cache_creation вместо cache_read
   (brain $0.04 вместо $0.011) — один такой ход тянул ON-среднее на +13%.
   Cold-start ходы исключаются из ОБЕИХ сторон и пересчитываются в отчёте
   (никаких молчаливых отбрасываний).

БД собирается в tmp — никакого чтения .secrets/demo.db.
"""
from __future__ import annotations

import datetime
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "obligations_cost_delta.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("obligations_cost_delta_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["obligations_cost_delta_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mkdb(path: Path, rows) -> str:
    """rows = [(ts, tag, in, out, cache_read, cache_creation[, c5m, c1h]), ...]

    Хвост (c5m, c1h) необязателен: без него строка изображает ИСТОРИЧЕСКУЮ
    запись без разбивки по TTL (NULL), как в живой базе до фазы 0.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, "
        "tag TEXT, model TEXT, input_tokens INT, output_tokens INT, "
        "cache_read_input_tokens INT, cache_creation_input_tokens INT, "
        "cache_creation_5m INT, cache_creation_1h INT)")
    conn.executemany(
        "INSERT INTO llm_usage (ts, tag, model, input_tokens, output_tokens, "
        "cache_read_input_tokens, cache_creation_input_tokens, cache_creation_5m, "
        "cache_creation_1h) VALUES (?,?,'m',?,?,?,?,?,?)",
        [tuple(r) + (None, None) if len(r) == 6 else tuple(r) for r in rows])
    conn.commit()
    conn.close()
    return str(path)


def _ts(h: int, m: int = 0, day: int = 0) -> float:
    """epoch сегодня в HH:MM (day=-1 — вчера)."""
    base = datetime.date.today() + datetime.timedelta(days=day)
    return datetime.datetime.combine(base, datetime.time(h, m)).timestamp()


# ── окно по умолчанию = текущий день ─────────────────────────────────────────


def test_default_since_is_today_midnight_not_epoch_zero():
    mod = _load_module()
    expected = datetime.datetime.combine(
        datetime.date.today(), datetime.time.min).timestamp()
    assert mod.default_since() == expected


def test_yesterday_turns_excluded_from_baseline_by_default(tmp_path):
    """Вчерашний ДЕШЁВЫЙ ход не должен занижать baseline и раздувать дельту."""
    mod = _load_module()
    warm = dict(cr=8801, cw=0)
    rows = [
        # вчера: дешёвый ход (другой промпт) — НЕ должен попасть в baseline
        (_ts(12, 0, day=-1), "brain", 2000, 50, warm["cr"], warm["cw"]),
        (_ts(12, 1, day=-1), "classifier", 2000, 100, 0, 3000),
        # сегодня OFF
        (_ts(15, 0), "brain", 2000, 200, 8801, 0),
        (_ts(15, 1), "classifier", 2000, 180, 0, 7000),
        # сегодня ON
        (_ts(18, 0), "brain", 2000, 200, 8801, 0),
        (_ts(18, 1), "classifier", 2000, 180, 0, 7000),
    ]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0))

    assert rep.off_turns == 1, "вчерашний ход просочился в baseline"
    assert rep.on_turns == 1
    assert rep.delta == pytest.approx(0.0, abs=1e-9)
    assert rep.since == mod.default_since()


def test_explicit_since_zero_still_pulls_full_history(tmp_path):
    """Дефолт сузили — но осознанный `--since 0` обязан работать как раньше."""
    mod = _load_module()
    rows = [
        (_ts(12, 0, day=-1), "brain", 2000, 50, 8801, 0),
        (_ts(15, 0), "brain", 2000, 200, 8801, 0),
        (_ts(18, 0), "brain", 2000, 200, 8801, 0),
    ]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0), since=0.0)
    assert rep.off_turns == 2


# ── cold-start ходы ──────────────────────────────────────────────────────────


def test_cold_start_turn_excluded_and_counted(tmp_path):
    """Ход с cache_creation на BRAIN = первый после рестарта → вне замера, но в отчёте."""
    mod = _load_module()
    rows = [
        (_ts(15, 0), "brain", 2000, 200, 8801, 0),        # OFF тёплый
        (_ts(15, 1), "classifier", 2000, 180, 0, 7000),
        (_ts(18, 0), "brain", 2000, 200, 0, 8801),        # ON ХОЛОДНЫЙ
        (_ts(18, 1), "classifier", 2000, 180, 0, 7000),
        (_ts(18, 30), "brain", 2000, 200, 8801, 0),       # ON тёплый
        (_ts(18, 31), "classifier", 2000, 180, 0, 7000),
    ]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0))

    assert rep.on_turns == 1, "холодный ход посчитан как обычный"
    assert rep.on_cold == 1
    assert rep.off_cold == 0
    assert rep.delta == pytest.approx(0.0, abs=1e-9)
    # отброшенное обязано быть видно в тексте отчёта (правило «no silent caps»)
    assert "cold-start" in rep.render()


def test_include_cold_restores_old_behaviour(tmp_path):
    mod = _load_module()
    rows = [
        (_ts(15, 0), "brain", 2000, 200, 8801, 0),
        (_ts(18, 0), "brain", 2000, 200, 0, 8801),
        (_ts(18, 30), "brain", 2000, 200, 8801, 0),
    ]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0), include_cold=True)
    assert rep.on_turns == 2 and rep.on_cold == 1
    assert rep.delta > 10, "холодный ход обязан задирать дельту — ради этого его и режем"


def test_classifier_cache_creation_is_not_cold_start(tmp_path):
    """У классификатора cache_read=0 ВСЕГДА — это не признак рестарта."""
    mod = _load_module()
    rows = [
        (_ts(15, 0), "brain", 2000, 200, 8801, 0),
        (_ts(15, 1), "classifier", 2000, 180, 0, 7000),
        (_ts(18, 0), "brain", 2000, 200, 8801, 0),
        (_ts(18, 1), "classifier", 2000, 180, 0, 7000),
    ]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0))
    assert rep.off_cold == 0 and rep.on_cold == 0
    assert rep.off_turns == 1 and rep.on_turns == 1


# ── привязка классификатора к ходу ───────────────────────────────────────────


def test_classifier_and_retry_attach_to_preceding_brain_turn(tmp_path):
    mod = _load_module()
    rows = [
        (_ts(18, 0), "brain", 1_000_000, 0, 0, 0),              # $3.00
        (_ts(18, 1), "classifier", 1_000_000, 0, 0, 0),         # $3.00
        (_ts(18, 2), "classifier_retry", 1_000_000, 0, 0, 0),   # $3.00
        (_ts(15, 0), "brain", 1_000_000, 0, 0, 0),              # baseline $3.00
    ]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0))
    assert rep.on_avg == pytest.approx(9.0)
    assert rep.off_avg == pytest.approx(3.0)
    assert rep.delta == pytest.approx(200.0)


def test_gate_verdict_flips_at_ten_percent(tmp_path):
    mod = _load_module()
    rows = [
        (_ts(15, 0), "brain", 1_000_000, 0, 0, 0),        # $3.00
        (_ts(18, 0), "brain", 1_200_000, 0, 0, 0),        # $3.60 = +20%
    ]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0))
    assert rep.delta == pytest.approx(20.0)
    assert rep.passed is False
    assert "ПРЕВЫШЕН" in rep.render()


# ── ставка записи кэша: 5m ($3.75/M) против 1h ($6/M) ────────────────────────


def test_write_rate_is_1h_when_split_says_1h(tmp_path):
    """ttl:1h в llm.py = write x2. Пока скрипт считал всё по 5m, он занижал
    счёт классификатора на треть."""
    mod = _load_module()
    rows = [(_ts(18, 0), "brain", 0, 0, 0, 1_000_000, 0, 1_000_000)]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0), include_cold=True)
    assert rep.on_total == pytest.approx(6.0)      # 1M x $6/M, а не $3.75


def test_write_rate_is_5m_when_split_says_5m(tmp_path):
    mod = _load_module()
    rows = [(_ts(18, 0), "brain", 0, 0, 0, 1_000_000, 1_000_000, 0)]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0), include_cold=True)
    assert rep.on_total == pytest.approx(3.75)


def test_mixed_ttl_row_is_billed_per_part(tmp_path):
    mod = _load_module()
    rows = [(_ts(18, 0), "brain", 0, 0, 0, 2_000_000, 1_000_000, 1_000_000)]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0), include_cold=True)
    assert rep.on_total == pytest.approx(3.75 + 6.0)


def test_legacy_rows_without_split_fall_back_and_are_flagged_as_estimate(tmp_path):
    """Историческую строку не выдумываем: считаем по старой ставке, но ГОВОРИМ,
    что это оценка — иначе замер ДО/ПОСЛЕ молча смешает факт с догадкой."""
    mod = _load_module()
    rows = [(_ts(15, 0), "brain", 0, 0, 0, 1_000_000),                    # NULL-разбивка
            (_ts(18, 0), "brain", 0, 0, 0, 1_000_000, 0, 1_000_000)]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0), include_cold=True)
    assert rep.off_total == pytest.approx(3.75)    # legacy → старая ставка
    assert rep.on_total == pytest.approx(6.0)      # факт → 1h
    assert rep.legacy_write_rows == 1
    assert "оценка" in rep.render()


def test_no_estimate_note_when_every_row_has_split(tmp_path):
    mod = _load_module()
    rows = [(_ts(15, 0), "brain", 0, 0, 0, 1_000_000, 1_000_000, 0),
            (_ts(18, 0), "brain", 0, 0, 0, 1_000_000, 1_000_000, 0)]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0), include_cold=True)
    assert rep.legacy_write_rows == 0
    assert "оценка" not in rep.render()


def test_cache_read_rows_are_unaffected_by_rate_choice(tmp_path):
    """Ставка чтения одна ($0.30/M) — экономия арки не должна зависеть от того,
    разобрались мы со ставкой записи или нет."""
    mod = _load_module()
    rows = [(_ts(18, 0), "brain", 0, 0, 1_000_000, 0)]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0))
    assert rep.on_total == pytest.approx(0.30)


def test_empty_baseline_reports_honestly_instead_of_nan(tmp_path):
    mod = _load_module()
    rows = [(_ts(18, 0), "brain", 2000, 200, 8801, 0)]
    db = _mkdb(tmp_path / "t.db", rows)
    rep = mod.report(db, split_ts=_ts(17, 0))
    assert rep.off_turns == 0
    assert rep.passed is None
    assert "нет baseline" in rep.render()
