# -*- coding: utf-8 -*-
"""Detached regress watchdog (Master-Plan Этап 1, хвост #6).

A full regress spawns pytest from a daemon thread of the bot. The subprocess
timeout is enforced *inside that thread* — if the bot dies (crash/restart) the
thread dies with it, but the pytest child survives orphaned and can grind for
30+ minutes / 11 GB RAM (live incident 2026-07-09).

Fix: at spawn, persist the pytest PID-group + a wall-clock deadline to
``state/regress_watch.json``. Two independent watchdogs — the bot's periodic
loop (:func:`sweep`) AND the standalone guardian (``scripts/regress_watch_check.py``)
— read this marker; a regress past its deadline, or one whose parent bot PID is
dead (orphan), is killed as a whole PID-group (``taskkill /T /F``), logged and
the admin is notified. A regress that finishes normally clears the marker.

The pytest invocation itself is UNCHANGED (same command, args, env, cwd,
creationflags, timeout) — only its supervision is added. Time is injected and
process ops are pluggable so the whole thing is unit-tested on mocks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

WATCH_NAME = "regress_watch.json"


class RegressRamKilled(Exception):
    """A batch was tree-killed mid-flight because free RAM crossed the hard floor.

    Distinct from :class:`subprocess.TimeoutExpired` so the batched runner can
    tell "this batch ate the machine" (mark it failed, keep going) apart from a
    wall-clock hang (abort). Carries the offending PID-group leader and the free
    RAM (GiB) measured at the kill."""

    def __init__(self, pid, free_gb):
        super().__init__(
            "regress batch pid=%s killed: free RAM %.2f GB below floor"
            % (pid, float(free_gb)))
        self.pid = pid
        self.free_gb = float(free_gb)


# ── durable marker (state/regress_watch.json) ───────────────────────────────
def watch_path(state_dir) -> Path:
    return Path(state_dir) / WATCH_NAME


def record(state_dir, *, pid: int, bot_pid: int, deadline_epoch: int,
           label: str = "regress", started_epoch: Optional[int] = None) -> None:
    """Persist the running regress: its pytest PID (group leader), the parent
    bot PID (so an orphan is detectable) and the wall-clock deadline."""
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    data = {
        "pid": int(pid),
        "bot_pid": int(bot_pid),
        "deadline_epoch": int(deadline_epoch),
        "started_epoch": int(started_epoch) if started_epoch is not None else int(deadline_epoch),
        "label": label,
    }
    watch_path(state_dir).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read(state_dir) -> Optional[dict]:
    p = watch_path(state_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def clear(state_dir) -> None:
    p = watch_path(state_dir)
    if p.exists():
        p.unlink()


# ── pure decisions (unit-tested) ────────────────────────────────────────────
def is_expired(watch: dict, now: float) -> bool:
    return float(now) > float(watch.get("deadline_epoch", 0))


def is_orphaned(watch: dict, parent_alive: bool) -> bool:
    return not bool(parent_alive)


def ram_breach(free_gb, floor_gb) -> bool:
    """True when free RAM has crossed BELOW the hard floor (kill the batch now).

    Strict ``<`` — the boundary itself is NOT a breach (mirrors :func:`ram_ok`'s
    boundary in the batched runner, kept the opposite sense on purpose)."""
    return float(free_gb) < float(floor_gb)


def runaway_reason(watch: dict, now: float, parent_alive: bool) -> Optional[str]:
    """Why this regress is a runaway, or None if it is running legitimately.

    Orphan (dead parent bot) takes precedence over an expired deadline — it is
    the more severe / more certain condition (the supervisor is gone)."""
    if is_orphaned(watch, parent_alive):
        return "orphan"
    if is_expired(watch, now):
        return "deadline"
    return None


_REASON_RU = {
    "deadline": "истёк дедлайн (wall-clock)",
    "orphan": "родитель-бот мёртв (сирота)",
}


def kill_notice(info: dict) -> str:
    return (
        "🛑 Runaway-регресс убит: PID-группа %s (%s), причина: %s. "
        "Оставшиеся pytest-процессы прибиты (taskkill /T /F), state очищен."
        % (info.get("pid"), info.get("label") or "regress",
           _REASON_RU.get(info.get("reason"), info.get("reason")))
    )


# ── process ops (Windows tree-kill; pluggable for tests) ────────────────────
def kill_process_group(pid) -> bool:
    """Kill the pytest PID and its whole child tree (the "PID-group").

    ``taskkill /T /F /PID`` terminates the target and every descendant, so an
    orphaned pytest plus any workers it spawned all die. Non-Windows uses a
    best-effort SIGKILL of the leader."""
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))],
                               capture_output=True, text=True, timeout=15,
                               encoding="utf-8", errors="replace")
            return r.returncode == 0
        import signal
        os.kill(int(pid), signal.SIGKILL)
        return True
    except Exception:
        return False


# ── in-process sweep (called from the bot's periodic heartbeat loop) ────────
def sweep(state_dir, *, now: float,
          parent_alive_fn: Callable[[int], bool],
          kill_fn: Callable[[int], bool],
          notify_fn: Optional[Callable[[str], None]] = None,
          log_fn: Optional[Callable[[str], None]] = None) -> Optional[dict]:
    """One watchdog pass. Reads the marker; if the regress is a runaway, kills
    its PID-group, notifies+logs, clears the marker, and returns an info dict.
    Returns None when there is no marker or the regress is healthy."""
    watch = read(state_dir)
    if not watch:
        return None
    parent_alive = bool(parent_alive_fn(watch.get("bot_pid")))
    reason = runaway_reason(watch, now, parent_alive)
    if reason is None:
        return None
    pid = watch.get("pid")
    killed = bool(kill_fn(pid))
    info = {"pid": pid, "bot_pid": watch.get("bot_pid"),
            "label": watch.get("label"), "reason": reason, "killed": killed}
    msg = kill_notice(info)
    if log_fn:
        log_fn(msg)
    if notify_fn:
        notify_fn(msg)
    clear(state_dir)
    return info


# ── guarded runner (records marker around an UNCHANGED pytest spawn) ────────
def run_guarded(cmd, *, cwd, env, timeout_s: int, creationflags: int,
                state_dir, label: str = "regress",
                bot_pid: Optional[int] = None,
                now_fn: Callable[[], float] = time.time,
                popen_factory: Optional[Callable[..., object]] = None,
                kill_fn: Optional[Callable[[int], bool]] = None,
                free_gb_fn: Optional[Callable[[], float]] = None,
                kill_free_gb: Optional[float] = None,
                sample_s: float = 2.0):
    """Spawn ``cmd`` exactly as the callers' ``subprocess.run`` did (same args,
    cwd, env, creationflags, timeout) but record a watch marker with the child
    PID + deadline before waiting, and clear it in ``finally``.

    Returns a :class:`subprocess.CompletedProcess` on success. On timeout it
    tree-kills the PID-group, then re-raises :class:`subprocess.TimeoutExpired`
    so callers keep their existing ``except TimeoutExpired`` handling.

    Intra-batch RAM guard: when both ``free_gb_fn`` and ``kill_free_gb`` are
    given, a daemon sampler watches free RAM every ``sample_s`` seconds while the
    batch runs; the first time it drops BELOW ``kill_free_gb`` the PID-group is
    tree-killed and :class:`RegressRamKilled` is raised — so a batch that
    balloons memory from the inside (parent alive, deadline not reached: the
    watchdog's blind spot) is contained instead of taking the host down. The
    sampler is inert (no thread) when the knobs are absent, so existing callers
    are byte-for-byte unchanged."""
    if popen_factory is None:
        popen_factory = subprocess.Popen
    if kill_fn is None:
        kill_fn = kill_process_group
    proc = popen_factory(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=cwd, env=env, creationflags=creationflags,
        encoding="utf-8", errors="replace")
    now = int(now_fn())
    record(state_dir, pid=proc.pid,
           bot_pid=bot_pid if bot_pid is not None else os.getpid(),
           deadline_epoch=now + int(timeout_s), label=label, started_epoch=now)

    ram = {"killed": False, "free": None}
    stop_evt = threading.Event()
    sampler: Optional[threading.Thread] = None

    def _sample() -> None:
        while not stop_evt.is_set():
            try:
                free = float(free_gb_fn())
            except Exception:
                free = None
            if free is not None and ram_breach(free, kill_free_gb):
                ram["killed"] = True
                ram["free"] = free
                kill_fn(proc.pid)        # tree-kill → communicate() unblocks
                return
            stop_evt.wait(sample_s)

    if free_gb_fn is not None and kill_free_gb is not None:
        sampler = threading.Thread(target=_sample, name="regress-ram-guard",
                                   daemon=True)
        sampler.start()

    try:
        out, err = proc.communicate(timeout=timeout_s)
        if ram["killed"]:
            raise RegressRamKilled(proc.pid, ram["free"])
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        kill_fn(proc.pid)          # kill the whole group, not just the leader
        try:
            proc.communicate(timeout=10)   # reap
        except Exception:
            pass
        raise
    finally:
        stop_evt.set()
        if sampler is not None:
            sampler.join(timeout=5)
        clear(state_dir)
