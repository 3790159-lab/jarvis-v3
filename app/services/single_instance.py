# -*- coding: utf-8 -*-
"""OS-level single-instance guard.

An exclusive, non-blocking lock held for the whole process lifetime. Unlike a
pid file — which the guardian wipes before every relaunch, defeating it as a
backstop — an OS lock is enforced by the kernel and independent of any file the
guardian touches: a second bot process simply cannot acquire it and must exit.
It is cross-process by construction, which is exactly the failure the double
poller race needed (two bot instances polling one token → duplicate callbacks).

Hold the handle returned by :func:`acquire` for the process lifetime. If it is
garbage-collected or closed the lock releases, so ``main()`` stashes it in a
module global. On crash the OS closes the handle and frees the lock for the
next start.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


class AlreadyRunning(Exception):
    """Raised when the single-instance lock is already held by another holder."""


def acquire(lock_path) -> int:
    """Acquire an exclusive, non-blocking lock at ``lock_path``.

    Returns an open file descriptor to hold for the process lifetime. Raises
    :class:`AlreadyRunning` if another process (or handle) already holds it.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    try:
        _lock(fd)
    except OSError as exc:
        os.close(fd)
        raise AlreadyRunning(str(path)) from exc
    return fd


def release(handle: int) -> None:
    """Release a lock acquired by :func:`acquire` and close its descriptor.

    Best-effort — a failure to unlock/close must never crash shutdown; the OS
    releases the lock on process exit regardless.
    """
    try:
        _unlock(handle)
    except OSError:
        pass
    try:
        os.close(handle)
    except OSError:
        pass


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)   # non-blocking exclusive on byte 0

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
