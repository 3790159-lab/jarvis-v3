# -*- coding: utf-8 -*-
"""Dev-task git worktree lifecycle — MUTATING git, strictly bounded.

Unlike the observation console (read-only), this module mutates git: it creates
and removes worktrees/branches and does FF-only merges. To keep the blast radius
tiny, every git call goes through ``_git`` which asserts the verb is in
``_ALLOWED_VERBS`` — no ``push``/``reset``/``clean``/``add``/``commit`` can slip
in. ``run`` is injectable so tests never touch real git.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional

PROD_REPO = "C:/jarvis"
WT_ROOT = "C:/jarvis_worktrees"

# only these git verbs may ever run from here (checkout only populates a fresh,
# empty worktree — no data loss — and is spawned detached, see _spawn_detached_checkout)
_ALLOWED_VERBS = {"worktree", "rev-parse", "merge-base", "merge", "branch", "checkout"}

# The mass checkout (~10k files) is killed when it runs as a direct child of the
# LIVE bot process (proven: fails 2/2 from the bot, succeeds in every standalone
# repro). Variant A sidesteps it: instant `--no-checkout` add + a DETACHED
# checkout decoupled from the bot's console/job.
CHECKOUT_TIMEOUT_S = int(os.getenv("DEVTASK_CHECKOUT_TIMEOUT_S", "300"))
CHECKOUT_POLL_S = float(os.getenv("DEVTASK_CHECKOUT_POLL_S", "2"))


def _git(root: str, run, *verb_and_args, check: bool = False):
    verb = verb_and_args[0]
    assert verb in _ALLOWED_VERBS, f"non-allowed git verb: {verb}"
    res = run(["git", "-C", root, *verb_and_args], capture_output=True, text=True)
    if check and getattr(res, "returncode", 0) != 0:
        raise RuntimeError(f"git {verb} failed: {getattr(res, 'stderr', '')}")
    return res


def _wt_path(task_id: str, wt_root: str) -> str:
    return f"{wt_root}/devtask-{task_id}"


def _spawn_detached_checkout(wt: str):
    """`git -C <wt> checkout` DETACHED from the bot's console/job group so the
    mass checkout is decoupled from the process context that kills it. Returns a
    Popen-like handle exposing ``poll()``/``kill()``. Windows-only creationflags;
    falls back if the job forbids breakaway."""
    if sys.platform != "win32":
        return subprocess.Popen(["git", "-C", wt, "checkout"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    base_flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    for flags in (base_flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, base_flags):
        try:
            return subprocess.Popen(["git", "-C", wt, "checkout"], creationflags=flags,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            continue  # job doesn't permit breakaway -> retry without that flag
    raise RuntimeError("could not spawn detached checkout")


def create_worktree(task_id: str, base: str, *, run=subprocess.run, spawn_checkout=None,
                    root: str = PROD_REPO, wt_root: str = WT_ROOT,
                    timeout_s: Optional[int] = None, poll_s: float = CHECKOUT_POLL_S,
                    sleep=time.sleep, now=time.monotonic) -> str:
    """Variant A worktree setup, meant to run in the dev-task DAEMON THREAD (the
    detached checkout can take ~30-50s; never call this from the poll loop):

    1. `git worktree add --no-checkout <wt> -b devtask-<id> <base>` — instant
       (~0.05s), does NO mass checkout, so the bot-context killer never triggers.
    2. populate the worktree via a DETACHED `git checkout` decoupled from the bot.
    3. block until the detached checkout exits 0 (or raise on failure/timeout).
    """
    wt = _wt_path(task_id, wt_root)
    branch = f"devtask-{task_id}"
    _git(root, run, "worktree", "add", "--no-checkout", wt, "-b", branch, base, check=True)

    spawn_checkout = spawn_checkout or _spawn_detached_checkout
    timeout_s = CHECKOUT_TIMEOUT_S if timeout_s is None else timeout_s
    handle = spawn_checkout(wt)
    deadline = now() + timeout_s
    while now() < deadline:
        rc = handle.poll()
        if rc is not None:
            if rc == 0:
                return wt
            raise RuntimeError(f"detached checkout failed rc={rc} for {wt}")
        sleep(poll_s)
    try:
        handle.kill()
    except Exception:
        pass
    raise RuntimeError(f"worktree checkout did not complete within {timeout_s}s for {wt}")


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


def is_merged(branch: str, *, run=subprocess.run, root: str = PROD_REPO) -> bool:
    """True iff ``<branch>`` is already fully contained in prod HEAD (branch is an
    ancestor of HEAD) — the merge already happened out-of-band, so the card should
    simply be closed as merged, NOT re-tested or flagged as "prod moved" (Этап 1)."""
    res = _git(root, run, "merge-base", "--is-ancestor", branch, "HEAD")
    return getattr(res, "returncode", 1) == 0


def merge_no_ff(branch: str, *, run=subprocess.run, root: str = PROD_REPO):
    """`git merge --no-ff <branch>` — a real merge COMMIT (never fast-forward),
    for when prod moved but the branch still needs integrating (Этап 1)."""
    return _git(root, run, "merge", "--no-ff", "--no-edit", branch, check=True)


def merge_prod_into_worktree(worktree: str, prod_head: str, *,
                             run=subprocess.run) -> bool:
    """Merge current prod HEAD INTO the branch worktree to materialise the
    COMBINED code (prod + branch) so the merge gate can test it before we create
    the real merge commit in prod. Returns True on a clean merge; on a conflict
    (rc != 0) we ``git merge --abort`` so the worktree stays usable and return
    False (the human must resolve it manually)."""
    res = _git(worktree, run, "merge", "--no-edit", prod_head)
    if getattr(res, "returncode", 0) != 0:
        _git(worktree, run, "merge", "--abort")
        return False
    return True


def remove_worktree(task_id: str, *, run=subprocess.run, root: str = PROD_REPO,
                    wt_root: str = WT_ROOT) -> None:
    wt = _wt_path(task_id, wt_root)
    branch = f"devtask-{task_id}"
    _git(root, run, "worktree", "remove", "--force", wt)
    _git(root, run, "branch", "-D", branch)
