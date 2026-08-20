# -*- coding: utf-8 -*-
"""Имя отметки живости раннера собирается В ОДНОМ месте.

Повод. Имя `chatter_heartbeat[_<slug>].txt` жило ШЕСТЬЮ независимыми
литералами, и каждое новое место наследовало догадку предыдущего. Цена
догадки измерена: 16.08 проба `ops_watchdog` знала только легаси-форму, а
раннер писал клиентскую — «heartbeat 49398с тому» при ЖИВОМ процессе, `fail`
дорос до 1625 и не восстановился бы никогда.

Главный сторож здесь — НЕ «имена совпадают», а `test_no_seventh_place`:
он краснеет, когда КТО-ТО ЗАВОДИТ СЕДЬМОЕ МЕСТО со своим литералом. Сверка
шести известных мест защищает от дрейфа известного; список файлов —
от появления неизвестного.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from chatter.runtime_paths import (
    CHATTER_BEAT_CLIENT_GLOB, CHATTER_BEAT_LEGACY_NAME, CHATTER_BEAT_PREFIX,
    CHATTER_BEAT_SUFFIX, chatter_beat_name, chatter_beat_path,
    chatter_beat_slug)

ROOT = Path(__file__).resolve().parents[2]

# Значения ЛИТЕРАЛЬНЫЕ. Выведи я их из модуля — сторож согласился бы с любой
# опечаткой по определению и промолчал ровно там, где имя поехало.
LEGACY = "chatter_heartbeat.txt"
PREFIX = "chatter_heartbeat_"
SUFFIX = ".txt"
GLOB = "chatter_heartbeat_*.txt"


def test_canonical_values_are_what_prod_writes():
    assert CHATTER_BEAT_LEGACY_NAME == LEGACY
    assert CHATTER_BEAT_PREFIX == PREFIX
    assert CHATTER_BEAT_SUFFIX == SUFFIX
    assert CHATTER_BEAT_CLIENT_GLOB == GLOB


@pytest.mark.parametrize("slug,expected", [
    (None, LEGACY),
    ("", LEGACY),
    ("volska", "chatter_heartbeat_volska.txt"),
    ("yarina", "chatter_heartbeat_yarina.txt"),
    ("demo2", "chatter_heartbeat_demo2.txt"),
])
def test_name_for_slug(slug, expected):
    assert chatter_beat_name(slug) == expected


@pytest.mark.parametrize("slug", ["volska", "yarina", "demo", "demo2"])
def test_slug_round_trip(slug):
    """Разбор обратно обязан давать ТОТ ЖЕ слаг: читатели, перечисляющие
    файлы маской, узнают клиента именно так."""
    assert chatter_beat_slug(chatter_beat_name(slug)) == slug


def test_legacy_name_has_no_slug():
    assert chatter_beat_slug(LEGACY) is None


@pytest.mark.parametrize("alien", [
    "bot_heartbeat.txt", "chatter_watch_alert_volska.json",
    "chatter_heartbeat_volska.json", "heartbeat_volska.txt",
    "chatter_heartbeat_.txt",
])
def test_alien_names_do_not_produce_a_slug(alien):
    """Обратная половина: чужое имя НЕ обязано притворяться клиентским.
    `chatter_heartbeat_.txt` — пустой слаг, и он тоже не клиент."""
    assert chatter_beat_slug(alien) is None


def test_path_is_relative_by_default_and_absolute_with_root(tmp_path):
    """Раннер пишет относительно cwd (он живёт в корне), панель — по
    абсолютному корню. Обе формы обязаны давать ОДНО имя файла."""
    rel = chatter_beat_path("volska")
    absolute = chatter_beat_path("volska", root=tmp_path)
    assert not rel.is_absolute()
    assert absolute.is_absolute()
    assert rel.name == absolute.name == "chatter_heartbeat_volska.txt"
    assert absolute.parent == tmp_path / "state"


def test_runner_resolver_agrees_with_the_canonical_one():
    """Раннер ПИШЕТ отметку — если он разойдётся с читателями, слепыми
    станут все сразу."""
    from chatter import telethon_run

    for slug in (None, "volska", "yarina"):
        assert telethon_run.heartbeat_path_for(slug) == chatter_beat_path(slug)
    assert telethon_run.HEARTBEAT_PATH == chatter_beat_path(None)


# ── Места, которые НЕ импортируют резолвер, и почему ────────────────────────
# `ops_watchdog` исполняется планировщиком каждые 30 с и держит импорт-граф
# из одного stdlib; `chatter_watch_check` объявлен standalone и обязан
# работать при сломанном пакете `chatter` (это записано в нём самом);
# два `.ps1` — другой язык. Импорт им не навяжешь, поэтому их пришпиливает
# ЭТОТ сторож: разойдутся — покраснеет.
PINNED = {
    "scripts/ops_watchdog.py": (LEGACY, GLOB),
    "scripts/chatter_watch_check.py": (LEGACY, PREFIX),
    "scripts/chatter_guardian_detached.ps1": (PREFIX,),
    "scripts/healthchecks_ping.ps1": (LEGACY, GLOB),
}


@pytest.mark.parametrize("rel,needles", sorted(PINNED.items()))
def test_pinned_places_still_agree(rel, needles):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    for needle in needles:
        assert needle in text, (
            f"{rel}: имя отметки разошлось с каноном — не найдено {needle!r}. "
            f"Либо верни имя, либо перенеси место на chatter.runtime_paths.")


# Файлы, которым ПОЗВОЛЕНО упоминать имя отметки. Список ЛИТЕРАЛЬНЫЙ и
# закрытый: седьмое место должно требовать осознанного решения, а не
# появляться молча.
# ⚠️ `chatter/telethon_run.py` и `scripts/panels_demo.py` в списке НЕТ, и это
# не забывчивость: после переезда на резолвер литерала в них не осталось
# вовсе. Их исчезновение из списка — и есть доказательство, что место
# действительно рассоединено, а не «зовёт канон и на всякий случай держит
# своё имя рядом».
ALLOWED = {
    "chatter/runtime_paths.py",          # сам канон
    "scripts/run_panel_client.py",       # имя осталось только в докстринге
    "scripts/ops_watchdog.py",           # пришпилен выше
    "scripts/chatter_watch_check.py",    # пришпилен выше
    "scripts/chatter_guardian_detached.ps1",
    "scripts/healthchecks_ping.ps1",
    "scripts/ask_owner.py",              # только в тексте комментария
}

_SCAN_DIRS = ("chatter", "scripts", "tools", "panels")
_NEEDLE = re.compile(r"chatter_heartbeat")


def test_no_seventh_place():
    """ГЛАВНЫЙ сторож раздела: новое место со своим литералом = красный.

    Сверка известных мест ловит дрейф известного. А эта проверка ловит то, из
    чего дефект 16.08 и вырос: кто-то дописал ЕЩЁ ОДНО место и угадал имя по
    памяти. Список файлов литеральный — выведенный согласился бы с любым
    новичком по определению.
    """
    found = set()
    for d in _SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in (".py", ".ps1"):
                continue
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _NEEDLE.search(text):
                found.add(path.relative_to(ROOT).as_posix())

    unexpected = sorted(found - ALLOWED)
    assert not unexpected, (
        "имя отметки живости появилось в НОВОМ месте: " + ", ".join(unexpected)
        + ". Зови chatter.runtime_paths.chatter_beat_path вместо своего "
        "литерала — шесть независимых догадок уже стоили слепой пробы 16.08.")

    # Обратная половина: список не должен разрастаться мёртвыми записями —
    # запись, которой больше нет в коде, скрывает, что место исчезло.
    stale = sorted(ALLOWED - found)
    assert not stale, (
        "в списке разрешённых есть места, где имени БОЛЬШЕ НЕТ: "
        + ", ".join(stale) + ". Убери их из ALLOWED — иначе список перестаёт "
        "описывать код и однажды разрешит чужое.")
