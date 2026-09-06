"""Цепочка зависимых DevTask — сторожа от спеки 2026-09-07-devtask-chain.md.

Написаны ДО кода. Нумерация соответствует §8 спеки; каждый сторож обязан падать
на СВОЁМ ассерте, чтобы красное называло причину, а не факт.

Форма — вариант C: шаг цепочки это обычная DevTask, единственная разница —
база worktree (ветка предыдущего шага вместо prod_head).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.devtask import chain as ch  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return ch.ChainStore(base_dir=tmp_path)


@pytest.fixture()
def three(store):
    return store.create(title="три шага", descs=["первый", "второй", "третий"])


# ── §8.9 план объявляется заранее и целиком ────────────────────────────────

def test_plan_is_declared_up_front(three):
    assert [s["step_no"] for s in three["steps"]] == [1, 2, 3]
    assert all(s["task_id"] is None for s in three["steps"])
    assert all(s["status"] == ch.STEP_PENDING for s in three["steps"])
    assert three["status"] == ch.CHAIN_RUNNING


def test_chain_longer_than_the_cap_is_refused(store):
    with pytest.raises(ValueError):
        store.create(title="слишком длинная", descs=["ш"] * (ch.MAX_STEPS + 1))


def test_empty_chain_is_refused(store):
    with pytest.raises(ValueError):
        store.create(title="пустая", descs=[])


def test_appending_a_step_to_a_running_chain_is_refused(store, three):
    with pytest.raises(ch.ChainClosedError):
        store.append_step(three["id"], "четвёртый")


# ── §8.1 база шага N — ветка шага N−1, а не prod_head ──────────────────────

def test_first_step_starts_from_prod_head(three):
    assert ch.base_for_step(three, 1, prod_head="abc1234") == "abc1234"


def test_second_step_starts_from_the_previous_branch(store, three):
    store.attach_task(three["id"], 1, "20260907_000001_aaa")
    chain = store.get(three["id"])
    assert ch.base_for_step(chain, 2, prod_head="abc1234") == "devtask-20260907_000001_aaa"


def test_base_for_step_refuses_when_previous_has_no_task(three):
    """Шаг 2 без задачи шага 1 — это дыра в плане, а не повод взять prod_head."""
    with pytest.raises(ch.ChainStateError):
        ch.base_for_step(three, 2, prod_head="abc1234")


# ── §8.2 следующий шаг только из awaiting_review и только по зелёному ──────

def test_next_step_is_none_while_current_is_running(store, three):
    store.attach_task(three["id"], 1, "t1")
    assert ch.next_step(store.get(three["id"])) is None


def test_next_step_appears_after_awaiting_review(store, three):
    store.attach_task(three["id"], 1, "t1")
    store.set_step_status(three["id"], 1, ch.STEP_AWAITING_REVIEW)
    nxt = ch.next_step(store.get(three["id"]))
    assert nxt is not None and nxt["step_no"] == 2


def test_chain_is_done_when_the_declared_plan_is_exhausted(store):
    chain = store.create(title="один шаг", descs=["единственный"])
    store.attach_task(chain["id"], 1, "t1")
    store.set_step_status(chain["id"], 1, ch.STEP_AWAITING_REVIEW)
    assert ch.next_step(store.get(chain["id"])) is None
    assert ch.is_exhausted(store.get(chain["id"])) is True


# ── §8.3 каждое условие остановки — свой сторож и своя причина ─────────────

def _ok(**over):
    """Вердикт шага, на котором цепочка ОБЯЗАНА ехать дальше."""
    base = dict(gate_ok=True, merge_conflict=False, step_elapsed_s=60,
                chain_elapsed_s=60, has_deletions=False, rate_limited=False,
                preflight_ok=True, worktree_ok=True, process_alive=True)
    base.update(over)
    return base


def test_green_step_does_not_stop_the_chain():
    assert ch.stop_reason(_ok()) is None


def test_red_gate_stops():
    assert ch.stop_reason(_ok(gate_ok=False)) == ch.STOP_GATE_RED


def test_merge_conflict_stops():
    assert ch.stop_reason(_ok(merge_conflict=True)) == ch.STOP_MERGE_CONFLICT


def test_step_timeout_stops():
    assert ch.stop_reason(_ok(step_elapsed_s=ch.STEP_TIMEOUT_S + 1)) == ch.STOP_STEP_TIMEOUT


def test_step_just_under_the_timeout_does_not_stop():
    """Граница названа явно: 90 минут ровно ещё едут."""
    assert ch.stop_reason(_ok(step_elapsed_s=ch.STEP_TIMEOUT_S)) is None


def test_chain_timeout_stops():
    assert ch.stop_reason(_ok(chain_elapsed_s=ch.CHAIN_TIMEOUT_S + 1)) == ch.STOP_CHAIN_TIMEOUT


def test_rate_limit_stops():
    assert ch.stop_reason(_ok(rate_limited=True)) == ch.STOP_RATE_LIMIT


def test_failed_preflight_stops():
    assert ch.stop_reason(_ok(preflight_ok=False)) == ch.STOP_PREFLIGHT


def test_worktree_failure_stops():
    assert ch.stop_reason(_ok(worktree_ok=False)) == ch.STOP_WORKTREE


def test_vanished_process_stops():
    assert ch.stop_reason(_ok(process_alive=False)) == ch.STOP_PROCESS_GONE


# ── §8.4 удаление останавливает даже при зелёном гейте ─────────────────────

def test_deletion_stops_even_on_a_green_gate():
    verdict = _ok(gate_ok=True, has_deletions=True)
    assert ch.stop_reason(verdict) == ch.STOP_DELETION


def test_deletion_wins_over_other_reasons():
    """Приоритет назван: про удаление владелец должен узнать поимённо."""
    verdict = _ok(gate_ok=False, has_deletions=True)
    assert ch.stop_reason(verdict) == ch.STOP_DELETION


# ── §8.5–8.7 откат ─────────────────────────────────────────────────────────

@pytest.fixture()
def built(store, three):
    for n in (1, 2, 3):
        store.attach_task(three["id"], n, "t%d" % n)
        store.set_step_status(three["id"], n, ch.STEP_AWAITING_REVIEW)
    return store.get(three["id"])


def test_rollback_goes_from_the_last_step_to_the_first(built):
    plan = ch.rollback_plan(built, from_step=1, is_merged=lambda b: False)
    assert [item["step_no"] for item in plan] == [3, 2, 1]


def test_partial_rollback_keeps_the_earlier_steps(built):
    plan = ch.rollback_plan(built, from_step=2, is_merged=lambda b: False)
    assert [item["step_no"] for item in plan] == [3, 2]


def test_rollback_refuses_when_a_branch_is_already_in_trunk(built):
    with pytest.raises(ch.AlreadyMergedError):
        ch.rollback_plan(built, from_step=1,
                         is_merged=lambda b: b == "devtask-t1")


def test_rollback_names_worktree_and_branch_only(built):
    plan = ch.rollback_plan(built, from_step=1, is_merged=lambda b: False)
    for item in plan:
        assert set(item) == {"step_no", "task_id", "branch"}


def test_rollback_never_lists_reports_or_journals(built):
    """Откат убирает КОД, но не улики — без них разбор аварии невозможен."""
    plan = ch.rollback_plan(built, from_step=1, is_merged=lambda b: False)
    blob = repr(plan)
    for forbidden in ("report.md", "log.jsonl", "gate", ".log"):
        assert forbidden not in blob


# ── §8.8 цепочка не мержит в транк никогда ─────────────────────────────────

def test_chain_module_never_calls_a_merge():
    """Литеральная проверка исходника: в модуле цепочки нет вызовов мержа.

    Сторож намеренно грубый. Тонкий (замокать git_ops и проверить, что не
    позвали) зеленел бы на любой ветке, которую тест не прошёл; этот краснеет
    от самого появления вызова в файле.
    """
    src = Path(ch.__file__).read_text(encoding="utf-8")
    for forbidden in ("ff_merge", "merge_no_ff", "git merge", "merge_prod_into_worktree"):
        assert forbidden not in src, f"цепочка не смеет мержить: найдено {forbidden!r}"


def test_stop_reasons_are_all_distinct():
    reasons = [ch.STOP_GATE_RED, ch.STOP_MERGE_CONFLICT, ch.STOP_STEP_TIMEOUT,
               ch.STOP_CHAIN_TIMEOUT, ch.STOP_DELETION, ch.STOP_RATE_LIMIT,
               ch.STOP_PREFLIGHT, ch.STOP_WORKTREE, ch.STOP_PROCESS_GONE]
    assert len(set(reasons)) == len(reasons)


def test_caps_match_the_spec():
    """Литеральные числа из спеки, утверждённой владельцем 07.09."""
    assert ch.MAX_STEPS == 10
    assert ch.STEP_TIMEOUT_S == 90 * 60
    assert ch.CHAIN_TIMEOUT_S == 5 * 60 * 60


def test_stopped_chain_records_the_reason(store, three):
    store.stop(three["id"], ch.STOP_GATE_RED)
    chain = store.get(three["id"])
    assert chain["status"] == ch.CHAIN_STOPPED
    assert chain["stopped_reason"] == ch.STOP_GATE_RED


def test_stopped_chain_yields_no_next_step(store, three):
    store.stop(three["id"], ch.STOP_RATE_LIMIT)
    assert ch.next_step(store.get(three["id"])) is None
