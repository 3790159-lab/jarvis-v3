# -*- coding: utf-8 -*-
"""Восьмая проверка ops_watchdog: живое дерево C:\\jarvis на транке и чисто.

Зачем сторож, если это «просто дисциплина»: рабочее дерево — ДЕПЛОЙ-ПУТЬ
гардиана (`git checkout` = тихий деплой через ~90 с). 2026-08-10 оно дважды
оказалось не в том состоянии: сначала осталось на feature-ветке после сессии,
потом мутационный харнесс откатом стёр незакоммиченную правку. Оба раза это
поймало внимание — то есть не поймало бы, случись оно ночью.

Проверка красная, если HEAD не на транке ИЛИ есть модифицированные tracked-файлы.
Untracked — НЕ повод: в живом дереве их дюжина (артефакты, отчёты), и считать их
поломкой значит выдать вечно красную лампу, которую перестанут читать.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ops_watchdog_tree", Path(__file__).resolve().parents[1] / "scripts" / "ops_watchdog.py")
ow = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ow)

TRUNK = "phase-4.0-unified-jarvis"


def _snap(branch=TRUNK, dirty=(), error=None):
    return {"branch": branch, "dirty": list(dirty), "error": error}


# ── зелёное ────────────────────────────────────────────────────────────────

def test_trunk_and_clean_is_ok():
    got = ow.probe_worktree(_snap())
    assert got["ok"] is True
    assert TRUNK in got["detail"]


# ── красное: не тот HEAD ───────────────────────────────────────────────────

def test_feature_branch_is_down():
    """Ровно вчерашний случай: дерево осталось на ветке арки после сессии."""
    got = ow.probe_worktree(_snap(branch="feat/payments-phase0"))
    assert got["ok"] is False
    assert "feat/payments-phase0" in got["detail"] and TRUNK in got["detail"], (
        "в тексте обязаны быть ОБЕ ветки: иначе непонятно, куда возвращать")


def test_detached_head_is_down():
    """`git rev-parse --abbrev-ref HEAD` в detached-состоянии отдаёт 'HEAD'.
    Это не транк, и гардиан задеплоит произвольный коммит."""
    assert ow.probe_worktree(_snap(branch="HEAD"))["ok"] is False


def test_unknown_branch_is_down_and_says_so_readably():
    """Пустая ветка — это «не смогли определить», а не «наверное транк».

    Проверяется ТЕКСТ, а не только красный цвет: без явной ветки пустая строка
    всё равно не равна транку и алерт бы загорелся — но с сообщением
    «HEAD на «», ожидался …», которое в три часа ночи читается как поломка
    самого сторожа. Мутация «снять обработку» этот тест переживала, пока он
    смотрел только на ok (DEV-26)."""
    for branch in (None, "", "   "):
        got = ow.probe_worktree(_snap(branch=branch))
        assert got["ok"] is False
        assert "не определена" in got["detail"], got["detail"]
        assert "«»" not in got["detail"], "в алерте пустые кавычки вместо причины"


# ── красное: грязные tracked-файлы ─────────────────────────────────────────

def test_modified_tracked_files_are_down():
    got = ow.probe_worktree(_snap(dirty=[" M chatter/run.py"]))
    assert got["ok"] is False
    assert "chatter/run.py" in got["detail"]


def test_detail_names_the_count_and_does_not_dump_everything():
    """Алерт уходит в Telegram: список из сорока файлов там нечитаем."""
    got = ow.probe_worktree(_snap(dirty=[f" M f{i}.py" for i in range(40)]))
    assert got["ok"] is False
    assert "40" in got["detail"]
    assert len(got["detail"]) < 400, "алерт разросся в простыню"


def test_both_problems_are_reported_together():
    """Одна причина из двух вводит в заблуждение сильнее, чем отсутствие
    алерта: вернёшь ветку — и решишь, что починил."""
    got = ow.probe_worktree(_snap(branch="feat/x", dirty=[" M a.py"]))
    assert got["ok"] is False
    assert "feat/x" in got["detail"] and "a.py" in got["detail"]


# ── красное: не смогли посмотреть ──────────────────────────────────────────

def test_git_failure_is_down_not_green():
    """«Не удалось спросить git» на машине, где git И ЕСТЬ механизм деплоя, —
    само по себе аномалия. Зелёное здесь означало бы слепую зону."""
    got = ow.probe_worktree(_snap(error="git not found"))
    assert got["ok"] is False
    assert "git not found" in got["detail"]


# ── untracked не считаются ─────────────────────────────────────────────────

def test_untracked_files_are_not_a_failure():
    """В живом дереве дюжина untracked-артефактов. Считать их поломкой —
    значит выдать вечно красную лампу, которую перестанут читать."""
    assert ow.probe_worktree(_snap(dirty=[]))["ok"] is True


def test_snapshot_command_excludes_untracked():
    """Сторож на САМ СБОР, а не только на решение: без
    `--untracked-files=no` проверка была бы красной всегда (12 untracked
    записей в живом дереве на момент внедрения), и цена этого — не ложный
    алерт, а привычка его игнорировать."""
    assert "--untracked-files=no" in ow.GIT_STATUS_ARGS
    assert "--porcelain" in ow.GIT_STATUS_ARGS


def test_live_tree_path_is_the_deploy_path_not_the_script_root():
    """ROOT — это каталог скрипта, а в worktree он указывает НА WORKTREE.
    Проверять надо конкретное дерево, из которого деплоит гардиан."""
    assert str(ow.LIVE_TREE).replace("\\", "/").lower().rstrip("/") == "c:/jarvis"
    assert ow.TRUNK_BRANCH == TRUNK


# ── подключение к боевому циклу ────────────────────────────────────────────

def test_probe_all_includes_the_tree_check_when_snapshot_supplied():
    probes = ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30),
                          worktree_snapshot=_snap())
    assert probes["worktree"]["ok"] is True


def test_probe_all_without_snapshot_keeps_old_behaviour():
    """Обратная совместимость: watchdog на машине без этого дерева не имеет
    права слать DOWN о том, чего не мерил."""
    probes = ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30))
    assert "worktree" not in probes


def test_tree_probe_goes_through_the_existing_debounce():
    """Деплой гардиана длится ~90 с; одиночный тик не должен пейджить."""
    down = {"worktree": {"ok": False, "detail": "HEAD на feat/x"}}
    alerts, st = ow.evaluate({}, down)
    assert alerts == [], "алерт на первом же цикле — дебаунс не применился"
    alerts, st = ow.evaluate(st, down)
    assert len(alerts) == 1 and "DOWN" in alerts[0]
    alerts, st = ow.evaluate(st, {"worktree": {"ok": True, "detail": "ok"}})
    assert len(alerts) == 1 and "Восстановлено" in alerts[0]


def test_check_has_a_human_label():
    """Без метки алерт приходит с сырым ключом проверки."""
    assert "worktree" in ow.LABELS
    assert "jarvis" in ow.LABELS["worktree"].lower()


def test_it_is_the_eighth_check():
    probes = ow.probe_all(lambda p: 200, lambda p: (100 * 2**30, 0, 50 * 2**30),
                          chatter_snapshot={"processes": [], "runner_beat_age": None,
                                            "guardian_beat_age": None,
                                            "guardian_lock_pid": None, "root": "x"},
                          worktree_snapshot=_snap())
    assert len(probes) == 8, sorted(probes)


# ── причина, а не только факт (дефект alerted, 2026-08-11) ─────────────────

def test_a_second_dirty_file_is_a_different_reason():
    """Ровно тот случай, ради которого чек написан. 11.08 дерево было красным
    ~14 часов из-за законной правки тумблера пультом; приехавший следом
    недеплоенный код второго алерта не дал бы — alerted уже стоял."""
    one = ow.probe_worktree(_snap(dirty=[" M chatter/clients/volska/settings.yaml"]))
    two = ow.probe_worktree(_snap(dirty=[" M chatter/clients/volska/settings.yaml",
                                         " M chatter/run.py"]))
    assert one["reason"] != two["reason"]
    assert "run.py" in two["detail"]


def test_the_same_dirty_file_keeps_the_same_reason():
    """Обратная сторона: дерево, стоящее в одном и том же состоянии, не имеет
    права алертить каждые 30 секунд."""
    a = ow.probe_worktree(_snap(dirty=[" M settings.yaml"]))
    b = ow.probe_worktree(_snap(dirty=["M  settings.yaml"]))
    assert a["reason"] == b["reason"], "статус-буквы git сделали причину нестабильной"


def test_the_file_count_alone_is_not_the_reason():
    """Один файл сменился другим — количество то же, авария другая."""
    a = ow.probe_worktree(_snap(dirty=[" M settings.yaml"]))
    b = ow.probe_worktree(_snap(dirty=[" M chatter/run.py"]))
    assert a["reason"] != b["reason"]


def test_wrong_branch_and_dirty_tree_are_different_reasons():
    assert (ow.probe_worktree(_snap(branch="feat/x"))["reason"]
            != ow.probe_worktree(_snap(dirty=[" M a.py"]))["reason"])


def test_the_reason_survives_more_files_than_the_alert_shows():
    """DIRTY_SHOWN=3 режет ТЕКСТ. Если резать ещё и причину, четвёртый файл
    станет невидимым — а именно он и будет недеплоенным кодом."""
    base = [" M a.py", " M b.py", " M c.py"]
    assert (ow.probe_worktree(_snap(dirty=base))["reason"]
            != ow.probe_worktree(_snap(dirty=base + [" M d.py"]))["reason"])
