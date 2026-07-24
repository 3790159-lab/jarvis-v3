# -*- coding: utf-8 -*-
"""Фаза 0 арки «кэш классификатора»: разбивка cache_creation на 5m/1h.

Зачем: код ставит `ttl:1h` (write ×2 = $6/M), а тарифная модель и
`obligations_cost_delta.py` считают по $3.75/M (ставка 5m). По БД различить
нельзя — мы пишем ТОЛЬКО суммарный `cache_creation_input_tokens`. Разница на
1000 диалогов — сотни долларов в месяц, поэтому вопрос закрывается фактом
из API, а не рассуждением.

API отдаёт разбивку в `usage.cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens`.
Здесь: извлечение (устойчивое к её отсутствию), запись в БД, миграция старой
базы и сходимость разбивки с суммой.

SDK мокается через sys.modules — сеть и ключ не нужны.
"""
from __future__ import annotations

import sqlite3
import sys
import types

import pytest

from chatter.core.llm import AnthropicLLM
from chatter.storage.db import Store, usage_sink_for


# --- фейковый SDK: usage с разбивкой и без -----------------------------------
class _CacheCreation:
    def __init__(self, m5: int = 0, h1: int = 0):
        self.ephemeral_5m_input_tokens = m5
        self.ephemeral_1h_input_tokens = h1


class _Usage:
    def __init__(self, *, total_creation: int = 0, split: _CacheCreation | None = None,
                 read: int = 0):
        self.input_tokens = 1973
        self.output_tokens = 263
        self.cache_read_input_tokens = read
        self.cache_creation_input_tokens = total_creation
        if split is not None:
            self.cache_creation = split


class _Block:
    type = "text"
    text = "{}"


class _Response:
    def __init__(self, usage):
        self.content = [_Block()]
        self.usage = usage
        self.stop_reason = "end_turn"


def _llm_with(usage, sink):
    """AnthropicLLM без __init__ (он бы полез в SDK за клиентом и ключом)."""
    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm._model = "claude-sonnet-5"
    llm._usage_sink = sink
    llm.last_stop_reason = None
    llm._record_usage(_Response(usage), "classifier")
    return llm


# --- извлечение разбивки -----------------------------------------------------


def test_record_usage_extracts_1h_split():
    """Ставка 1h — та, что реально стоит в llm.py. Её и надо увидеть в записи."""
    rec = {}
    _llm_with(_Usage(total_creation=7770, split=_CacheCreation(h1=7770)), rec.update)
    assert rec["cache_creation_input_tokens"] == 7770
    assert rec["cache_creation_1h"] == 7770
    assert rec["cache_creation_5m"] == 0


def test_record_usage_extracts_5m_split():
    rec = {}
    _llm_with(_Usage(total_creation=4000, split=_CacheCreation(m5=4000)), rec.update)
    assert rec["cache_creation_5m"] == 4000
    assert rec["cache_creation_1h"] == 0


def test_split_sums_to_total_when_both_ttls_present():
    """Сходимость — приёмочный критерий фазы 0: разбивка обязана давать сумму."""
    rec = {}
    _llm_with(_Usage(total_creation=7000, split=_CacheCreation(m5=3000, h1=4000)),
              rec.update)
    assert rec["cache_creation_5m"] + rec["cache_creation_1h"] == \
        rec["cache_creation_input_tokens"] == 7000


def test_missing_cache_creation_object_degrades_to_zeros_without_crash():
    """Старый ответ/мок без .cache_creation не должен ронять ход: сумма важнее
    разбивки, а её мы по-прежнему знаем."""
    rec = {}
    _llm_with(_Usage(total_creation=5000), rec.update)   # split не задан
    assert rec["cache_creation_input_tokens"] == 5000
    assert rec["cache_creation_5m"] == 0
    assert rec["cache_creation_1h"] == 0


def test_broken_split_fields_do_not_break_the_record():
    """None вместо чисел (видели у SDK на пустых usage) → 0, а не TypeError."""
    rec = {}
    bad = _CacheCreation()
    bad.ephemeral_5m_input_tokens = None
    bad.ephemeral_1h_input_tokens = None
    _llm_with(_Usage(total_creation=100, split=bad), rec.update)
    assert rec["cache_creation_5m"] == 0 and rec["cache_creation_1h"] == 0


# --- запись в БД -------------------------------------------------------------


def test_store_persists_split():
    store = Store(":memory:")
    store.add_llm_usage(tag="classifier", model="m", input_tokens=1, output_tokens=2,
                        cache_read_input_tokens=0, cache_creation_input_tokens=7770,
                        cache_creation_5m=0, cache_creation_1h=7770, ts=1.0)
    row = store._conn.execute("SELECT * FROM llm_usage").fetchone()
    assert row["cache_creation_1h"] == 7770
    assert row["cache_creation_5m"] == 0


def test_store_defaults_split_to_zero_for_legacy_callers():
    """Существующие вызовы (и тесты) не передают новых полей — не ломаемся."""
    store = Store(":memory:")
    store.add_llm_usage(tag="brain", model="m", input_tokens=1, output_tokens=2,
                        cache_read_input_tokens=8801, cache_creation_input_tokens=0)
    row = store._conn.execute("SELECT * FROM llm_usage").fetchone()
    assert row["cache_creation_5m"] == 0 and row["cache_creation_1h"] == 0


def test_usage_sink_passes_split_through():
    """Шов llm → sink → store: sink делает add_llm_usage(**rec), поэтому новые
    ключи обязаны совпадать с именами параметров."""
    store = Store(":memory:")
    sink = usage_sink_for(store)
    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm._model = "claude-sonnet-5"
    llm._usage_sink = sink
    llm.last_stop_reason = None
    llm._record_usage(_Response(_Usage(total_creation=7770,
                                       split=_CacheCreation(h1=7770))), "classifier")
    row = store._conn.execute("SELECT * FROM llm_usage").fetchone()
    assert row["cache_creation_1h"] == 7770 and row["tag"] == "classifier"


# --- миграция живой базы -----------------------------------------------------

_OLD_LLM_USAGE = """
CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tag TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_input_tokens INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL
);
"""


def test_migration_adds_split_columns_to_existing_db(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_LLM_USAGE)
    conn.execute("INSERT INTO llm_usage(ts,tag,model,input_tokens,output_tokens,"
                 "cache_read_input_tokens,cache_creation_input_tokens) "
                 "VALUES (1.0,'classifier','m',100,10,0,7770)")
    conn.commit(); conn.close()

    store = Store(db)
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(llm_usage)")}
    assert {"cache_creation_5m", "cache_creation_1h"} <= cols
    # Историческая строка жива, разбивки у неё нет (NULL) — это ЧЕСТНО:
    # мы не знаем, по какой ставке она оплачена, и не выдумываем ноль.
    row = store._conn.execute("SELECT * FROM llm_usage").fetchone()
    assert row["cache_creation_input_tokens"] == 7770
    assert row["cache_creation_1h"] is None
    store.close()


def test_migration_is_idempotent_across_restarts(tmp_path):
    """Гардиан перезапускает раннер постоянно: миграция, падающая на втором
    прогоне, = краш-петля, которую он будет вечно поддерживать."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_LLM_USAGE)
    conn.commit(); conn.close()
    Store(db).close()
    Store(db).close()          # второй старт не должен бросить
    store = Store(db)
    cols = [r["name"] for r in store._conn.execute("PRAGMA table_info(llm_usage)")]
    assert cols.count("cache_creation_1h") == 1
    store.close()
