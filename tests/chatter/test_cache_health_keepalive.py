# -*- coding: utf-8 -*-
"""К8 — детектор регрессии кэша под keep-alive.
Спека `2026-08-09-chatter-cache-keepalive.md` §5, блок «🔴 Keep-alive ломает
`cache_health.consecutive_misses`».

Требование спеки дословно: **для тега под keep-alive разрыв ≥ TTL уликой
ЯВЛЯЕТСЯ**, и без этой правки арку не принимать.

Почему это отдельный файл, а не пара строк в `test_cache_health`: здесь
сторожится не поведение функции, а СВОЙСТВО АРКИ — «оптимизация не ослепила
сторожа, который ловил то, что она оптимизирует». Класс дефекта редкий и
дорогой: арка снижает число промахов И одновременно выключает детектор
промахов, после чего он честно молчит. Ровно так 23.07 кэш умер и сутки этого
никто не видел — при зелёных тестах и довольном лиде.

Три способа этой правке стать зелёной ширмой, все сторожатся поимённо:

  1. **Ничего не изменилось** — детектор по-прежнему обрывает цепочку на
     разрыве. Сторожит К8-1: та же история БЕЗ пингов даёт 1, С пингами — 3.
  2. **Изменилось слишком сильно** — под keep-alive уликой считается ЛЮБОЙ
     разрыв, включая тот, где пинг не пришёл (сеть, 5xx, кончилось окно
     суток). Тогда каждый пропущенный пинг превращается в ложный алерт про
     сломанный префикс. Сторожит К8-2.
  3. **Сломалось прежнее поведение** у клиентов БЕЗ keep-alive. Сторожит
     К8-4: умолчание обязано быть байт-в-байт прежним.
"""
from __future__ import annotations

import pytest

from chatter.core import cache_health as ch
from chatter.core.cache_health import (
    CACHE_TTL_SEC, consecutive_misses, is_regression, keepalive_touches)

HOUR = CACHE_TTL_SEC


def row(ts, *, hit=False, tag="classifier"):
    return {"tag": tag, "ts": ts,
            "cache_read_input_tokens": 5000 if hit else 0,
            "cache_creation_input_tokens": 0 if hit else 5000}


def ping(ts, tag="classifier"):
    return {"tag": ch.KEEPALIVE_TAG_PREFIX + tag, "ts": ts,
            "cache_read_input_tokens": 5000, "cache_creation_input_tokens": 0}


# Три боевых промаха, разнесённые НА БОЛЬШЕ ЧЕМ TTL: без пингов это три
# независимых холодных старта, с пингами — три улики подряд.
SPARSE_MISSES = [row(0.0), row(3 * HOUR), row(6 * HOUR)]


# ── К8-1. ГЛАВНОЕ: под пингами разрыв ≥ TTL — улика ─────────────────────────

def test_k8_1_gap_covered_by_pings_no_longer_breaks_the_chain():
    """Требование §5. Пинги идут каждые 50 мин, значит запись обязана была
    жить; три промаха подряд на живой записи — сигнатура сломанного префикса,
    а не редкого трафика."""
    pings = [ping(t * 0.5 * HOUR) for t in range(1, 13)]     # каждые 30 мин
    assert consecutive_misses(SPARSE_MISSES + pings, "classifier",
                              keepalive_ts=[p["ts"] for p in pings]) == 3


def test_k8_1_b_and_that_raises_the_alert():
    """Считать промахи и не поднять алерт — половина работы. Порог 3."""
    pings = [ping(t * 0.5 * HOUR) for t in range(1, 13)]
    assert is_regression(SPARSE_MISSES + pings, "classifier") is True


def test_k8_1_c_without_pings_the_same_history_is_silent():
    """Контроль к К8-1: та же история без пингов НЕ улика. Без него тест выше
    зеленел бы и на детекторе, который просто считает все промахи подряд."""
    assert consecutive_misses(SPARSE_MISSES, "classifier") == 1
    assert is_regression(SPARSE_MISSES, "classifier") is False


# ── К8-2. Пропущенный пинг = кэш умер ЗАКОННО ───────────────────────────────

def test_k8_2_a_gap_the_pings_did_not_cover_still_breaks_the_chain():
    """Пинг не ушёл (сеть, 5xx, кончилось окно суток) — запись умерла законно,
    и промах после неё уликой НЕ является.

    Иначе арка меняет одну слепоту на другую: вместо молчащего детектора мы
    получаем детектор, кричащий на каждой сетевой икоте, — а такой сторож
    перестают читать за неделю."""
    # пинги есть, но в разрыве между 3ч и 6ч дыра длиннее TTL
    pings = [ping(0.5 * HOUR), ping(1.0 * HOUR), ping(1.5 * HOUR),
             ping(2.0 * HOUR), ping(2.5 * HOUR)]
    assert consecutive_misses(SPARSE_MISSES + pings, "classifier") == 1


def test_k8_2_b_partial_coverage_counts_only_the_covered_tail():
    """Разрыв 3ч→6ч прикрыт, разрыв 0→3ч нет: улик две, а не три и не одна."""
    pings = [ping(3.5 * HOUR), ping(4.0 * HOUR), ping(4.5 * HOUR),
             ping(5.0 * HOUR), ping(5.5 * HOUR)]
    assert consecutive_misses(SPARSE_MISSES + pings, "classifier") == 2


@pytest.mark.parametrize("p,expect", [
    (0.49, 1),    # второй подпромежуток 1.01 TTL → запись умерла
    (0.50, 1),    # второй ровно TTL → умерла (граница строгая: `>=`)
    (0.51, 2),    # 0.51 и 0.99 — оба меньше TTL → жива, промах = улика
    (0.75, 2),    # середина живого диапазона
    (0.99, 2),    # 0.99 и 0.51 — всё ещё жива
    (1.00, 1),    # первый ровно TTL → умерла
    (1.01, 1),    # первый 1.01 TTL → умерла
])
def test_k8_2_c_boundary_is_the_SUBGAP_not_the_whole_gap(p, expect):
    """Граница проверяется НА ГРАНИЦЕ и на ТОЙ величине, которая решает.

    Решает не длина разрыва между боевыми вызовами, а самый длинный
    ПОДПРОМЕЖУТОК между соседними касаниями внутри него: кэш продлевается
    каждым касанием. Два вызова разнесены на 1.5×TTL, между ними ОДИН пинг в
    момент `p` — значит подпромежутки равны `p` и `1.5 − p`, и запись жива
    только когда оба строго меньше TTL, то есть при 0.5 < p < 1.0.

    Первая версия этого теста ставила один пинг в трёхчасовой разрыв и ждала,
    что он его прикроет. Не прикроет — и тест справедливо покраснел: фикстура
    описывала не ту величину, которую проверяет.
    """
    misses = [row(0.0), row(1.5 * HOUR)]
    got = consecutive_misses(misses + [ping(p * HOUR)], "classifier")
    assert got == expect, f"подпромежутки {p} и {1.5 - p:.2f} TTL → ждали {expect}"


# ── К8-3. Пинги берутся ИЗ ТЕХ ЖЕ строк и по ОТДЕЛЬНОМУ тегу ────────────────

def test_k8_3_a_touches_are_read_from_the_separate_tag():
    pings = [ping(0.5 * HOUR), ping(1.0 * HOUR)]
    assert keepalive_touches(SPARSE_MISSES + pings, "classifier") == (
        0.5 * HOUR, 1.0 * HOUR)


def test_k8_3_b_pings_do_not_leak_into_hit_rate():
    """§5 п.3: пинг под общим тегом изнутри неотличим от боевого вызова.
    Проверяем, что раздельность реальная, а не на словах."""
    pings = [ping(0.5 * HOUR), ping(1.0 * HOUR)]
    hits, total = ch.hit_rate(SPARSE_MISSES + pings, "classifier")
    assert (hits, total) == (0, 3), "пинги подмешались в статистику попаданий"


def test_k8_3_c_touches_of_another_prefix_do_not_count():
    """У brain и классификатора РАЗНЫЕ префиксы и разные кэш-записи. Пинг
    brain'а не продлевает запись классификатора — считать их вместе значит
    объявить живой запись, которую никто не грел."""
    brain_pings = [ping(t * 0.5 * HOUR, tag="brain") for t in range(1, 13)]
    assert keepalive_touches(SPARSE_MISSES + brain_pings, "classifier") == ()
    assert consecutive_misses(SPARSE_MISSES + brain_pings, "classifier") == 1


def test_k8_3_d_is_regression_finds_touches_itself():
    """Вызывающий, забывший передать касания, получил бы ослеплённый детектор
    МОЛЧА — а молчащий сторож здесь и есть предмет §5. Поэтому `is_regression`
    достаёт пинги сам, а не полагается на аккуратность вызывающего."""
    pings = [ping(t * 0.5 * HOUR) for t in range(1, 13)]
    assert is_regression(SPARSE_MISSES + pings, "classifier") is True
    # и явный пустой кортеж по-прежнему означает «пингов не было»
    assert is_regression(SPARSE_MISSES + pings, "classifier",
                         keepalive_ts=()) is False


# ── К8-4. Клиент БЕЗ keep-alive: поведение прежнее ──────────────────────────

def test_k8_4_a_default_behaviour_is_unchanged():
    """Пингов нет ни одного — детектор обязан вести себя как до арки."""
    assert consecutive_misses(SPARSE_MISSES, "classifier") == 1
    dense = [row(0.0), row(60.0), row(120.0)]
    assert consecutive_misses(dense, "classifier") == 3
    assert consecutive_misses([row(0.0), row(60.0, hit=True), row(120.0)],
                              "classifier") == 1


def test_k8_4_b_config_change_still_neutral_under_keepalive():
    """Смена конфига законно меняет префикс, и платой остаётся ровно один
    промах — keep-alive этого не отменяет. Ловит правку, которая под пингами
    начала бы считать уликой ещё и `/reload`."""
    pings = [ping(t * 0.5 * HOUR) for t in range(1, 13)]
    rows = SPARSE_MISSES + pings
    assert consecutive_misses(rows, "classifier", config_change_ts=[2.9 * HOUR],
                              keepalive_ts=[p["ts"] for p in pings]) == 2
