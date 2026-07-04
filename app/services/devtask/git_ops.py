# -*- coding: utf-8 -*-
"""Dev-task git worktree lifecycle — MUTATING git, strictly bounded.

Unlike the observation console (read-only), this module mutates git: it creates
and removes worktrees/branches and does FF-only merges. To keep the blast radius
tiny, every git call goes through ``_git`` which asserts the verb is in
``_ALLOWED_VERBS`` — no ``push``/``reset``/``clean``/``add``/``commit`` can slip
in. ``run`` is injectable so tests never touch real git.
"""
from __future__ import annotations

import subprocess
from typing import Optional

PROD_REPO = "C:/jarvis"
WT_ROOT = "C:/jarvis_worktrees"

# only these git verbs may ever run from here
_ALLOWED_VERBS = {"worktree", "rev-parse", "merge-base", "merge", "branch"}


def _git(root: str, run, *verb_and_args, check: bool = False):
    verb = verb_and_args[0]
    assert verb in _ALLOWED_VERBS, f"non-allowed git verb: {verb}"
    res = run(["git", "-C", root, *verb_and_args], capture_output=True, text=True)
    if check and getattr(res, "returncode", 0) != 0:
        raise RuntimeError(f"git {verb} failed: {getattr(res, 'stderr', '')}")
    return res


def _wt_path(task_id: str, wt_root: str) -> str:
    return f"{wt_root}/devtask-{task_id}"


def create_worktree(task_id: str, base: str, *, run=subprocess.run,
                    root: str = PROD_REPO, wt_root: str = WT_ROOT) -> str:
    """`git worktree add <wt>/devtask-<id> -b devtask-<id> <base>`; returns path."""
    wt = _wt_path(task_id, wt_root)
    branch = f"devtask-{task_id}"
    _git(root, run, "worktree", "add", wt, "-b", branch, base, check=True)
    return wt


def prod_head(*, run=subprocess.run, root: str = PROD_REPO) -> str:
    return (_git(root, run, "rev-parse", "HEAD").stdout or "").strip()


def is_ff_clean(branch: str, base_head: str, *, run=subprocess.run,
                root: str = PROD_REPO) -> bool:
    """FF is safe only if prod HEAD hasn't moved since the task started AND the
    branch is a descendant of prod HEAD (prod is an ancestor of branch)."""
    if prod_head(run=run, root=root) != base_head:
        return False
    res = _git(root, run, "merge-base", "--is-ancestor", base_head, branch)
    return getattr(res, "returncode", 1) == 0


def ff_merge(branch: str, *, run=subprocess.run, root: str = PROD_REPO):
    """`git merge --ff-only <branch>` — never a non-FF/no-ff merge commit."""
    return _git(root, run, "merge", "--ff-only", branch, check=True)


def remove_worktree(task_id: str, *, run=subprocess.run, root: str = PROD_REPO,
                    wt_root: str = WT_ROOT) -> None:
    wt = _wt_path(task_id, wt_root)
    branch = f"devtask-{task_id}"
    _git(root, run, "worktree", "remove", "--force", wt)
    _git(root, run, "branch", "-D", branch)
