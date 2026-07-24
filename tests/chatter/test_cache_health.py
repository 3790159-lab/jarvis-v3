# -*- coding: utf-8 -*-
"""Фаза 2 арки «кэш классификатора»: видимость и сигнатура регрессии.

Регрессия 2026-07-23 18:23 прожила сутки незамеченной: тесты зелёные, бот
отвечал верно, счёт вырос на треть — и ни одна метрика об этом не сказала.
Здесь машинный аналог того наблюдателя, которого не было:

* строка `llm-usage` на каждый вызов (PII-free, как §8 `prompt-shape`);
* hit-rate за сутки РАЗДЕЛЬНО по тегам и обязательно с базой (100% на двух
  вызовах и 90% на пятидесяти — разные утверждения);
* сигнатура регрессии: N промахов подряд ПРИ ЖИВОМ кэше (интервал < 1ч),
  считая интервал между вызовами ОДНОГО тега, с подавлением легитимных
  промахов после смены конфига.
"""
from __future__ import annotations

import logging

import pytest

from chatter.core.cache_health import (
    REGRESSION_STREAK, consecutive_misses, hit_rate, is_regression,
)
from chatter.core.prompt_log import log_usage_shape

HOUR = 3600.0


def _row(ts, tag="classifier", *, hit=False, cw=7770, m5=0, h1=None):
    return {"ts": ts, "tag": tag, "model": "claude-sonnet-5",
            "input_tokens": 1900, "output_tokens": 200,
            "cache_read_input_tokens": cw if hit else 0,
            "cache_creation_input_tokens": 0 if hit else cw,
            "cache_creation_5m": m5,
            "cache_creation_1h": (cw if h1 is None and not hit else h1) or 0}


# ── hit-rate: всегда с базой ─────────────────────────────────────────────────


def test_hit_rate_is_reported_per_tag():
    rows = [_row(1, "brain", hit=True), _row(2, "brain", hit=True),
            _row(3, "classifier"), _row(4, "classifier")]
    assert hit_rate(rows, "brain") == (2, 2)
    assert hit_rate(rows, "classifier") == (0, 2)


def test_hit_rate_carries_the_base_not_just_a_percent():
    """100% на двух вызовах != 100% на пятидесяти. Возвращаем (hits, total),
    процент считает тот, кто печатает."""
    assert hit_rate([_row(1, hit=True)], "classifier") == (1, 1)
    assert hit_rate([], "classifier") == (0, 0)


def test_retry_calls_counted_under_their_own_tag():
    rows = [_row(1, "classifier"), _row(2, "classifier_retry", hit=True)]
    assert hit_rate(rows, "classifier") == (0, 1)
    assert hit_rate(rows, "classifier_retry") == (1, 1)


# ── сигнатура регрессии ──────────────────────────────────────────────────────


def test_streak_counts_consecutive_misses_at_short_intervals():
    rows = [_row(0), _row(600), _row(1200)]          # 3 промаха по 10 минут
    assert consecutive_misses(rows, "classifier") == 3
    assert is_regression(rows, "classifier") is True


def test_hit_breaks_the_streak():
    rows = [_row(0), _row(600), _row(1200, hit=True), _row(1800)]
    assert consecutive_misses(rows, "classifier") == 1
    assert is_regression(rows, "classifier") is False


def test_long_gap_breaks_the_streak_because_cache_died_legitimately():
    """Промах после 3 часов тишины — это истёкший TTL, а не сломанный префикс.
    Считать его уликой = алертить на каждом медленном диалоге."""
    rows = [_row(0), _row(600), _row(600 + 3 * HOUR)]
    assert consecutive_misses(rows, "classifier") == 1


def test_interval_is_measured_within_the_same_tag():
    """brain между двумя вызовами классификатора не должен «сокращать»
    интервал: у каждого тега свой кэш и своя история попаданий."""
    rows = [_row(0), _row(HOUR / 2, "brain", hit=True), _row(2 * HOUR)]
    assert consecutive_misses(rows, "classifier") == 1


def test_streak_of_two_is_not_yet_a_regression():
    rows = [_row(0), _row(600)]
    assert consecutive_misses(rows, "classifier") == 2
    assert is_regression(rows, "classifier") is False
    assert REGRESSION_STREAK == 3


# ── подавление легитимной смены префикса ─────────────────────────────────────


def test_miss_right_after_config_change_is_not_evidence():
    """`/reload` плейбука меняет префикс законно: один промах — это плата за
    новый кэш, а не поломка."""
    rows = [_row(0), _row(600), _row(1200)]
    # конфиг сменили между вторым и третьим вызовом
    assert consecutive_misses(rows, "classifier", config_change_ts=[900]) == 2
    assert is_regression(rows, "classifier", config_change_ts=[900]) is False


def test_config_change_suppresses_only_the_first_call_after_it():
    """Три reload'а подряд — три законных промаха; а вот промах, которому не
    предшествовала смена конфига, снова улика."""
    rows = [_row(0), _row(600), _row(1200), _row(1800)]
    assert consecutive_misses(
        rows, "classifier", config_change_ts=[300, 900, 1500]) == 1


def test_config_change_outside_the_window_does_not_suppress():
    rows = [_row(10 * HOUR), _row(10 * HOUR + 600), _row(10 * HOUR + 1200)]
    assert consecutive_misses(rows, "classifier", config_change_ts=[0]) == 3


def test_regression_of_the_real_incident_is_detected():
    """Данные 23.07: после 18:23 профиль въехал в кэшируемый блок, и промахи
    пошли подряд при интервалах в минуты. Алерт обязан сработать на третьем."""
    rows = [_row(0), _row(190 * 60), _row(190 * 60 + 100), _row(190 * 60 + 200)]
    assert is_regression(rows, "classifier") is True


# ── строка llm-usage (PII-free) ──────────────────────────────────────────────


def test_usage_line_is_structural_and_pii_free(caplog):
    with caplog.at_level(logging.INFO):
        log_usage_shape({"tag": "classifier", "model": "claude-sonnet-5",
                         "input_tokens": 1942, "output_tokens": 217,
                         "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 7767,
                         "cache_creation_5m": 0, "cache_creation_1h": 7767})
    line = caplog.text
    assert "llm-usage" in line and "tag=classifier" in line
    assert "cr=0" in line and "cw=7767" in line and "cached=miss" in line
    assert "ttl=1h" in line


def test_usage_line_marks_a_hit(caplog):
    # Через caplog, а НЕ подменой pl.log.info + importlib.reload: тот приём
    # оставляет подменённый логгер в глобалях старого модуля, и следующий тест
    # ловит пустой лог (ровно класс test-pollution из jarvis-oom-root-cause).
    with caplog.at_level(logging.INFO):
        log_usage_shape({"tag": "brain", "model": "m", "input_tokens": 2172,
                         "output_tokens": 190, "cache_read_input_tokens": 8801,
                         "cache_creation_input_tokens": 0,
                         "cache_creation_5m": 0, "cache_creation_1h": 0})
    assert "cached=hit" in caplog.text and "cr=8801" in caplog.text
    assert "tag=brain" in caplog.text


def test_usage_line_never_carries_text_fields():
    """Никакого профиля/переписки: только теги и числа (стандарт §8)."""
    import inspect
    from chatter.core import prompt_log
    src = inspect.getsource(prompt_log.log_usage_shape)
    for forbidden in ("profile", "suffix", "system", "text", "detail"):
        assert forbidden not in src, f"в строку usage просочилось поле {forbidden}"
