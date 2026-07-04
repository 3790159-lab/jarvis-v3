# -*- coding: utf-8 -*-
"""Dev-task git worktree lifecycle — injected `run`, $0, no real git."""
import types

import pytest

import app.services.devtask.git_ops as g


def _fake(stdout="", rc=0):
    def run(args, **k):
        run.calls.append(args)
        return types.SimpleNamespace(stdout=stdout, returncode=rc)
    run.calls = []
    return run


def _verbs(calls):
    # form: git -C <root> <verb> ...
    return [a[3] if len(a) > 3 and a[0] == "git" and a[1] == "-C" else a[1] for a in calls]


class _Handle:
    """Fake detached-checkout process handle."""
    def __init__(self, rc_sequence):
        self._seq = list(rc_sequence)
        self.killed = False

    def poll(self):
        return self._seq.pop(0) if self._seq else 0

    def kill(self):
        self.killed = True


# Variant A: instant `--no-checkout` add, then a DETACHED `git checkout` that
# populates the worktree off the bot process (the mass checkout is killed as a
# direct child of the live bot but succeeds when decoupled).
def test_create_worktree_uses_no_checkout_then_detached_checkout():
    run = _fake()
    spawned = []
    wt = g.create_worktree("T1", base="dead", run=run,
                           spawn_checkout=lambda w: spawned.append(w) or _Handle([0]),
                           root="C:/jarvis", wt_root="C:/wt",
                           poll_s=0, sleep=lambda s: None)
    assert wt == "C:/wt/devtask-T1"
    add_cmd = " ".join(run.calls[0])
    assert "worktree add --no-checkout" in add_cmd and "-b devtask-T1" in add_cmd and "dead" in add_cmd
    assert spawned == ["C:/wt/devtask-T1"]                       # detached checkout spawned
    assert all(v in g._ALLOWED_VERBS for v in _verbs(run.calls))


def test_create_worktree_detached_checkout_failure_raises():
    run = _fake()
    with pytest.raises(RuntimeError):
        g.create_worktree("T1", base="d", run=run,
                          spawn_checkout=lambda w: _Handle([1]),   # checkout died rc=1
                          root="C:/jarvis", wt_root="C:/wt",
                          poll_s=0, sleep=lambda s: None)


def test_create_worktree_checkout_timeout_kills_and_raises():
    run = _fake()
    h = _Handle([None, None, None])                               # never finishes
    times = iter([0, 0, 100, 100])                               # now() advances past deadline
    with pytest.raises(RuntimeError):
        g.create_worktree("T1", base="d", run=run, spawn_checkout=lambda w: h,
                          root="C:/jarvis", wt_root="C:/wt", timeout_s=10,
                          poll_s=0, sleep=lambda s: None, now=lambda: next(times))
    assert h.killed is True


def test_prod_head_reads_rev_parse():
    run = _fake(stdout="dde36a2\n")
    assert g.prod_head(run=run, root="C:/jarvis") == "dde36a2"
    assert "rev-parse" in " ".join(run.calls[0])


def test_is_ff_clean_true_when_prod_unmoved_and_branch_descends():
    # prod_head() -> same as base_head; merge-base --is-ancestor -> rc 0
    run = _fake(stdout="base1\n", rc=0)
    assert g.is_ff_clean("devtask-T1", base_head="base1", run=run, root="C:/jarvis") is True


def test_is_ff_clean_false_when_prod_moved():
    run = _fake(stdout="MOVED\n", rc=0)
    assert g.is_ff_clean("devtask-T1", base_head="base1", run=run, root="C:/jarvis") is False


def test_ff_merge_uses_ff_only():
    run = _fake()
    g.ff_merge("devtask-T1", run=run, root="C:/jarvis")
    assert "merge --ff-only devtask-T1" in " ".join(run.calls[-1])
    assert all(v in g._ALLOWED_VERBS for v in _verbs(run.calls))


def test_remove_worktree_removes_and_deletes_branch():
    run = _fake()
    g.remove_worktree("T1", run=run, root="C:/jarvis", wt_root="C:/wt")
    joined = " ".join(" ".join(c) for c in run.calls)
    assert "worktree remove --force C:/wt/devtask-T1" in joined
    assert "branch -D devtask-T1" in joined
    assert all(v in g._ALLOWED_VERBS for v in _verbs(run.calls))
