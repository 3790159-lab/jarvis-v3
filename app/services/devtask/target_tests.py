# -*- coding: utf-8 -*-
"""Targeted-test selection for the dev_task merge gate.

Maps a branch's diff (``git diff --name-only <base_head>``) onto the test files
under ``tests/`` so the merge gate can run *only* the tests touching the changed
paths — a conscious bypass of the full regress while the full suite is being
repaired (Master-Plan Этап 1). The full regress remains the default; this module
only supplies the narrower target set.

The path→test mapping is a pure function (``map_paths_to_tests``) so it is fully
unit-testable without git or the filesystem. The heuristic is deliberately
simple and best-effort:

- a changed file that IS an existing test file → run it directly;
- a changed source file ``…/<stem>.py`` → run every present ``tests/**/test_<stem>.py``.

Both halves look at ``tests/`` RECURSIVELY: the suite has long lived in packages
(``tests/chatter/…``), and a flat ``tests/test_*.py`` scan made them invisible —
task 0f24fd (``/allow``) shipped three ``tests/chatter/`` files and the gate still
reported "дифф не маппится ни на один тест".

It is intentionally conservative: no fuzzy import-graph analysis. A source file
whose test isn't name-derivable maps to nothing — the caller must treat an empty
target set as "cannot verify", never as "passed".
"""
from __future__ import annotations

import os
import subprocess
from pathlib import PurePosixPath
from typing import Callable, Iterable, List


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").strip()


def map_paths_to_tests(changed_paths: Iterable[str],
                       existing_tests: Iterable[str]) -> List[str]:
    """Pure: changed repo paths + existing test files → subset of tests to run.

    ``existing_tests`` is the set of test files actually present on disk
    (repo-relative, e.g. ``tests/test_queue.py``); a changed test file only maps
    to itself if it still exists (a deleted test can't be run).
    """
    present = {_norm(t) for t in existing_tests}
    by_name: dict = {}
    for t in present:
        by_name.setdefault(PurePosixPath(t).name, []).append(t)
    selected = set()
    for raw in changed_paths:
        c = _norm(raw)
        if not c.endswith(".py"):
            continue
        if c in present:                       # changed test file, still on disk
            selected.add(c)
            continue
        stem = PurePosixPath(c).stem           # queue.py → queue
        # every depth, not just tests/test_<stem>.py: tests/chatter/test_db.py
        # is as valid a target as tests/test_db.py, and when both exist we run
        # both (a name collision is not a reason to verify only half).
        selected.update(by_name.get(f"test_{stem}.py", ()))
    return sorted(selected)


def _is_doc_path(path: str) -> bool:
    return (path.endswith(".md")
            or path.startswith("docs/")
            or path.startswith("research/")
            or path.startswith("artifacts/"))


def is_docs_only_diff(changed_paths: Iterable[str]) -> bool:
    """True iff every changed path is documentation (``*.md``, ``docs/``,
    ``research/``, ``artifacts/**``) — no code/config/test/script changes.

    ``artifacts/**`` counts as docs-only regardless of a file's own extension
    (e.g. archived ``.py`` patch backups under ``artifacts/patch_backups/``).
    An empty diff is NOT docs-only — there is nothing to classify, so the
    caller falls back to its normal "no targets" handling.
    """
    paths = [_norm(p) for p in changed_paths if _norm(p)]
    if not paths:
        return False
    return all(_is_doc_path(p) for p in paths)


def changed_paths(worktree: str, base_head: str, *,
                  run: Callable = subprocess.run) -> List[str]:
    """``git diff --name-only <base_head>`` inside ``worktree`` → changed paths.

    Compares the branch's committed work against the task's base commit. Returns
    ``[]`` on any git failure (the caller falls back to reporting "no targets").
    """
    res = run(["git", "-C", worktree, "diff", "--name-only", base_head],
              capture_output=True, text=True)
    if getattr(res, "returncode", 1) != 0:
        return []
    return [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]


def list_test_files(worktree: str) -> List[str]:
    """Repo-relative paths of every ``tests/**/test_*.py`` in the worktree.

    Recursive on purpose: most of the suite lives in packages under ``tests/``
    (``tests/chatter/…``), and a flat scan hid them from the merge gate.
    ``__pycache__`` is skipped — stale ``.py`` copies there are not runnable
    targets.
    """
    root = os.path.join(worktree, "tests")
    if not os.path.isdir(root):
        return []
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        rel = os.path.relpath(dirpath, worktree).replace("\\", "/")
        for name in filenames:
            if name.startswith("test_") and name.endswith(".py"):
                out.append(f"{rel}/{name}")
    return sorted(out)
