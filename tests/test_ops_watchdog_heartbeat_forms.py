# -*- coding: utf-8 -*-
"""Проба chatter_runner обязана знать ОБЕ формы отметки живости.

Инцидент 16.08. `_chatter_snapshot` читал ровно один файл —
`state/chatter_heartbeat.txt`. Мультиклиентный раннер пишет per-slug форму
`state/chatter_heartbeat_<slug>.txt` и легаси-файл не трогает вовсе. Итог:
проба показывала «heartbeat 49398с тому» при ЖИВОМ раннере, счётчик `fail`
дорос до 1625 и не восстановился бы НИКОГДА — сторож, всегда красный при
нормальной работе, это не сторож, а фон.

Сторожа ниже пишутся ОТ КОНТРАКТА пробы, а не от текущей реализации:
проба меряет «есть ли у chatter СВЕЖАЯ отметка живости», а в какой из форм
она лежит — деталь, которая менялась и ещё поменяется.
"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_hb",
    Path(__file__).resolve().parents[1] / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ow)

ROOT = "C:/jarvis"
RUNNER_CMD = r"C:\jarvis\.venv\Scripts\python.exe -u -m chatter.telethon_run --llm real --client volska"


def _proc(pid=111, name="python.exe", cmdline=RUNNER_CMD):
    return {"pid": pid, "name": name, "cmdline": cmdline}


def _touch(path: Path, age_s: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("0", encoding="ascii")
    stamp = time.time() - age_s
    import os
    os.utime(path, (stamp, stamp))


# ── чистая функция выбора возраста ────────────────────────────────────────

def test_per_client_form_alone_is_a_valid_liveness_mark():
    """РЕГРЕССИЯ 16.08. Мультиклиентный раннер пишет ТОЛЬКО per-slug форму.
    Если проба её не знает, она врёт «мёртв» о живом — и врёт навсегда."""
    age, src = ow.chatter_beat_age({"chatter_heartbeat.txt": None,
                                    "chatter_heartbeat_volska.txt": 12.0})
    assert age == 12.0
    assert src == "chatter_heartbeat_volska.txt", "проба обязана назвать источник"


def test_legacy_form_alone_still_works():
    """До мержа арки живёт легаси-раннер с единственным файлом. Починка одной
    формы не имеет права сломать другую."""
    age, src = ow.chatter_beat_age({"chatter_heartbeat.txt": 20.0})
    assert age == 20.0
    assert src == "chatter_heartbeat.txt"


def test_freshest_form_wins_so_an_orphan_file_cannot_pin_the_probe_red():
    """Осознанный выбор: берём САМУЮ СВЕЖУЮ отметку, а не самую старую.

    Смена формы оставляет файл-сироту (после отката 16.08 таким остался
    `chatter_heartbeat_volska.txt`). Правило «красим по самому старому»
    превратило бы каждого выведенного клиента в вечный красный — то есть
    заменило бы одно «врёт навсегда» другим."""
    age, src = ow.chatter_beat_age({"chatter_heartbeat.txt": 5.0,
                                    "chatter_heartbeat_volska.txt": 99999.0})
    assert age == 5.0
    assert src == "chatter_heartbeat.txt"


def test_no_form_at_all_is_no_heartbeat():
    age, src = ow.chatter_beat_age({"chatter_heartbeat.txt": None,
                                    "chatter_heartbeat_volska.txt": None})
    assert age is None and src is None


def test_empty_input_is_no_heartbeat():
    assert ow.chatter_beat_age({}) == (None, None)


# ── сквозь пробу ──────────────────────────────────────────────────────────

def test_probe_is_green_on_a_live_multiclient_runner():
    """Тот самый инцидентный расклад: процесс жив, свежа ТОЛЬКО per-slug
    отметка. До починки проба отдавала stale_heartbeat."""
    age, src = ow.chatter_beat_age({"chatter_heartbeat.txt": 49398.0,
                                    "chatter_heartbeat_volska.txt": 11.0})
    p = ow.probe_chatter_runner([_proc()], beat_age=age, root=ROOT, beat_source=src)
    assert p["ok"] is True, p
    assert "chatter_heartbeat_volska.txt" in p["detail"], (
        "оператор обязан видеть, ПО КАКОМУ файлу вынесен вердикт: "
        "иначе разбор следующей аварии снова начнётся с догадки")


def test_probe_stays_red_when_every_form_is_stale():
    age, src = ow.chatter_beat_age({"chatter_heartbeat.txt": 4000.0,
                                    "chatter_heartbeat_volska.txt": 500.0})
    p = ow.probe_chatter_runner([_proc()], beat_age=age, root=ROOT, beat_source=src)
    assert p["ok"] is False and p["reason"] == "stale_heartbeat"


def test_probe_signature_stays_backward_compatible():
    """Прежние вызовы без источника обязаны работать: сигнатура пробы —
    контракт с уже написанными сторожами."""
    p = ow.probe_chatter_runner([_proc()], beat_age=10.0, root=ROOT)
    assert p["ok"] is True


# ── снимок собирает обе формы с диска ─────────────────────────────────────

def test_snapshot_finds_the_per_client_form_on_disk(tmp_path, monkeypatch):
    """Чистая функция может быть права, а снимок всё равно не подать ей
    per-slug файл — здесь проверяется именно СБОР, по настоящим файлам."""
    monkeypatch.setattr(ow, "ROOT", tmp_path)
    _touch(tmp_path / "state" / "chatter_heartbeat_volska.txt", 8.0)

    snap = ow._chatter_snapshot()
    assert snap is not None, "psutil недоступен — снимок не собрался"
    assert snap["runner_beat_age"] is not None and snap["runner_beat_age"] < 120, snap
    assert snap["runner_beat_source"] == "chatter_heartbeat_volska.txt", snap


def test_snapshot_ignores_files_that_are_not_heartbeats(tmp_path, monkeypatch):
    """Шаблон обязан быть узким: `chatter_watch_alert_volska.json` и
    `chatter_clients.json` лежат в той же папке и живостью не являются."""
    monkeypatch.setattr(ow, "ROOT", tmp_path)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    _touch(state / "chatter_watch_alert_volska.json", 1.0)
    _touch(state / "chatter_clients.json", 1.0)
    _touch(state / "chatter_guardian_heartbeat.txt", 1.0)

    snap = ow._chatter_snapshot()
    assert snap["runner_beat_source"] is None, (
        f"взят посторонний файл как отметка раннера: {snap['runner_beat_source']}")
    assert snap["runner_beat_age"] is None


# ── названное ограничение ─────────────────────────────────────────────────

def test_known_limit_one_live_client_masks_a_dead_sibling():
    """ЗАФИКСИРОВАННОЕ ограничение, а не недосмотр.

    Проба — ОДИН булев сигнал про chatter в целом и роста реестра не знает
    (её проверка процесса точно так же «любой раннер», а не «каждый»). Пока
    жив хоть один клиент, смерть соседа она не покажет: это работа
    per-client сторожа гардиана, а не независимого канала.

    Тест стоит здесь, чтобы ограничение было НАЗВАНО ВСЛУХ и всплыло при
    подключении второго клиента (9b), а не выяснилось следующей аварией."""
    age, src = ow.chatter_beat_age({"chatter_heartbeat_volska.txt": 10.0,
                                    "chatter_heartbeat_yarina.txt": 99999.0})
    assert age == 10.0 and src == "chatter_heartbeat_volska.txt"
    p = ow.probe_chatter_runner([_proc()], beat_age=age, root=ROOT, beat_source=src)
    assert p["ok"] is True, "поведение сознательное: сосед не покрыт этим каналом"
