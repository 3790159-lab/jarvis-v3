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
- a changed source file ``…/<stem>.py`` → run ``tests/test_<stem>.py`` if present.

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
    selected = set()
    for raw in changed_paths:
        c = _norm(raw)
        if not c.endswith(".py"):
            continue
        if c in present:                       # changed test file, still on disk
            selected.add(c)
            continue
        stem = PurePosixPath(c).stem           # queue.py → queue
        candidate = f"tests/test_{stem}.py"
        if candidate in present:
            selected.add(candidate)
    return sorted(selected)


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
    """Repo-relative paths of every ``tests/test_*.py`` present in the worktree."""
    root = os.path.join(worktree, "tests")
    out: List[str] = []
    try:
        for name in os.listdir(root):
            if name.startswith("test_") and name.endswith(".py"):
                out.append(f"tests/{name}")
    except OSError:
        return []
    return sorted(out)
