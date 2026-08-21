# -*- coding: utf-8 -*-
"""ЖИВОЙ дрил ремня по памяти. По умолчанию НЕ выполняется.

Спека §7.2: без живого прогона два зуба остаются рассуждением. Единичные
тесты проверяют решающую логику на числах, но не проверяют главного —
что `_thread.interrupt_main()` действительно разматывает pytest, а `os._exit`
действительно срабатывает там, где прерывание не берётся.

Оба дрила НАРОЧНО едят память, поэтому включаются явным флагом и гоняются
ПООДИНОЧКЕ, своим прогоном, с заниженными порогами:

    # зуб 1 — прерывание, pytest разматывается и НАЗЫВАЕТ тест
    $env:SUITE_RAM_BELT_DRILL=1; $env:SUITE_KILL_RSS_GB=0.8
    $env:SUITE_SAMPLE_S=0.5;     $env:SUITE_HARD_GRACE_S=30
    python -m pytest tests/test_zz_ram_belt_live_drill.py -k python_loop -q

    # зуб 2 — аварийный выход: главный поток внутри C-вызова, сигналы там
    # не проверяются, прерывание не берётся
    $env:SUITE_RAM_BELT_DRILL=1; $env:SUITE_KILL_RSS_GB=0.8
    $env:SUITE_SAMPLE_S=0.5;     $env:SUITE_HARD_GRACE_S=3
    python -m pytest tests/test_zz_ram_belt_live_drill.py -k c_level -q
    # ожидаемый код выхода: 77
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("SUITE_RAM_BELT_DRILL") != "1",
    reason="живой дрил ремня — включается SUITE_RAM_BELT_DRILL=1",
)


def test_balloon_in_a_python_loop_is_interrupted():
    """Зуб 1. Чистая петля на Python — байт-код исполняется, сигнал доходит.

    Это форма течи 21.08: `while True` в `r2_storage.list_objects`, который
    рос на 14.7 КБ за оборот и молчал.
    """
    ballast = []
    while True:
        ballast.append(bytearray(8 * 1024 * 1024))


def test_balloon_inside_a_c_level_call_needs_the_hard_exit():
    """Зуб 2. Один C-вызов, который не возвращает управление интерпретатору.

    `interrupt_main` тут бессилен: сигналы проверяются между байт-кодами, а
    внутри C-вызова их нет. Ремень обязан дожать аварийным выходом.

    Простое `bytearray(64 ГБ)` для этого НЕ годится: один malloc такого
    размера падает сразу, петли не выходит. Нужен вызов, который растёт
    ПОСТЕПЕННО и долго — распаковка нулей подходит: полезная нагрузка
    сжимается в килобайты, а разворачивается в гигабайты.
    """
    import zlib

    co = zlib.compressobj(1)
    parts = []
    for _ in range(12 * 1024):          # 12 ГБ нулей -> считанные килобайты
        parts.append(co.compress(bytes(1024 * 1024)))
    parts.append(co.flush())
    payload = b"".join(parts)

    # Здесь интерпретатор перестаёт исполнять байт-код до самого конца.
    zlib.decompress(payload)
