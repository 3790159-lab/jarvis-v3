# -*- coding: utf-8 -*-
"""Ш1 яруса 2 — детектор «незапушенная работа висит дольше порога».

Смысл не в «ahead > 0» (это норма в момент работы), а во ВРЕМЕНИ: коммит,
который сутки не уехал на origin, живёт ровно в одной копии — на диске,
который может умереть. Порог конфигом, дефолт 12 часов.

Три класса молчания:
  * чистое дерево (`ahead=0`) — нечего терять;
  * свежий ahead (в пределах порога) — человек ещё работает, это не дыра;
  * upstream'а нет вовсе (`feat/autonomy-shadow-tier2` именно такая) — мы не
    знаем, сколько работы не уехало; «не знаем» ≠ «дыра».
"""
from __future__ import annotations

from app.services import autonomy_detectors as det

HOUR = 3600.0


def _snapshot(**kwargs):
    git = dict(branch="phase-4.0-unified-jarvis",
               upstream="origin/phase-4.0-unified-jarvis",
               ahead=3, behind=0,
               oldest_unpushed_age_sec=20 * HOUR,
               oldest_unpushed_sha="ee8b39a1")
    git.update(kwargs)
    return {"git": git}


def test_stale_unpushed_work_is_a_proposal():
    found = det.detect_unpushed_branch(_snapshot())

    assert len(found) == 1
    assert found[0].kind == "branch_unpushed_too_long"
    assert found[0].subject == "phase-4.0-unified-jarvis"
    assert found[0].evidence["oldest_unpushed_sha"] == "ee8b39a1"


def test_fresh_unpushed_work_is_silent():
    """Два часа назад закоммичено — человек ещё за клавиатурой."""
    assert det.detect_unpushed_branch(_snapshot(oldest_unpushed_age_sec=2 * HOUR)) == []


def test_clean_tree_is_silent():
    assert det.detect_unpushed_branch(_snapshot(
        ahead=0, oldest_unpushed_age_sec=None, oldest_unpushed_sha=None)) == []


def test_branch_without_upstream_is_silent_because_unknown_is_not_a_hole():
    """Ветка worktree без origin — обычное дело; сколько на ней не уехало,
    мы не знаем, и выдумывать не будем."""
    assert det.detect_unpushed_branch(_snapshot(
        upstream=None, ahead=0, oldest_unpushed_age_sec=None,
        oldest_unpushed_sha=None)) == []


def test_unpushed_without_upstream_never_proposes_a_push_to_nowhere():
    """Найдено мутацией: при согласованном снимке (`upstream=None` ⇒ `ahead=0`)
    проверка upstream недостижима, и мутация «снять её» выживала. Детектор —
    чистая функция над ЧУЖИМ словарём и на инвариант сборщика полагаться не
    может: иначе однажды родится карточка «сделай push» в никуда."""
    assert det.detect_unpushed_branch(_snapshot(
        upstream=None, ahead=3, oldest_unpushed_age_sec=99 * HOUR)) == []


def test_threshold_comes_from_config():
    """N — конфигом. При пороге 24ч те же 20 часов молчат."""
    assert det.detect_unpushed_branch(
        _snapshot(), config={"unpushed_max_age_sec": 24 * HOUR}) == []
    assert det.DEFAULT_CONFIG["unpushed_max_age_sec"] == 12 * HOUR


def test_evidence_carries_no_volatile_fact():
    """Возраст растёт каждую секунду, счётчик ahead — с каждым коммитом.
    Любой из них в evidence = новая карточка на каждом прогоне."""
    evidence = det.detect_unpushed_branch(_snapshot())[0].evidence

    assert "age_sec" not in evidence and "ahead" not in evidence
    assert set(evidence) == {"branch", "upstream", "oldest_unpushed_sha",
                             "threshold_hours"}


def test_action_level_is_fail_closed():
    """`git push` — исходящее действие. Без политики уровень 4."""
    found = det.detect_unpushed_branch(_snapshot())

    assert found[0].action_level == det.FAIL_CLOSED_LEVEL
    assert found[0].proposed_action["action"] == "push_branch"
