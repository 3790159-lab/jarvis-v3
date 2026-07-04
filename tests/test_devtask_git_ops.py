# -*- coding: utf-8 -*-
"""Dev-task git worktree lifecycle — injected `run`, $0, no real git."""
import types

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


def test_create_worktree_form_and_verbs():
    run = _fake()
    wt = g.create_worktree("T1", base="dead", run=run, root="C:/jarvis", wt_root="C:/wt")
    assert wt == "C:/wt/devtask-T1"
    joined = " ".join(run.calls[0])
    assert "worktree add" in joined and "-b devtask-T1" in joined and "dead" in joined
    assert all(v in g._ALLOWED_VERBS for v in _verbs(run.calls))


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
