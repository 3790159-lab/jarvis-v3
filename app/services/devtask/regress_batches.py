# -*- coding: utf-8 -*-
"""Batched full-regress runner with a RAM-guard (Master-Plan Этап 1, хвост #4).

Running the whole ``pytest tests/`` suite in a single process OOMs / swaps on
16 GB (live incident 2026-07-09: 11 GB / 30+ min). This module chops it into
deterministic sequential batches and, before each batch, checks free RAM: if
memory is below the threshold it pauses and retries, and after N failed retries
it fails the regress HONESTLY ("недостаточно RAM") instead of launching a batch
into starvation. The per-batch pytest summaries are aggregated into a single
verdict, the baseline is refreshed after the first clean full run, and every
batch is spawned through the existing detached watchdog
(:mod:`app.services.devtask.regress_watch`) so a hung batch is still reaped and
``state/regress_watch.json`` reflects the batch in flight.

Design mirrors the sibling modules: the decisions are pure functions and every
side effect (running a batch, reading RAM, sleeping, computing the verdict) is
injected, so the whole thing is unit-tested on mocks — no real pytest, no real
psutil, no network.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# ── env defaults ────────────────────────────────────────────────────────────
DEFAULT_BATCH_SIZE = 40      # test files per batch (deterministic chunk)
DEFAULT_MIN_FREE_GB = 3.0    # do not start a batch below this free RAM
DEFAULT_RAM_RETRIES = 6      # pause+recheck this many times before honest fail
DEFAULT_RAM_PAUSE_S = 10     # seconds between RAM rechecks


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").strip()


# ── batch planning (pure, deterministic) ────────────────────────────────────
def plan_batches(test_files: Iterable[str], batch_size: int) -> List[List[str]]:
    """Sorted, de-duplicated test paths chunked into batches of ``batch_size``.

    Order is deterministic (sorted) so a rerun hits the same batch boundaries.
    A non-positive ``batch_size`` collapses to a single batch (i.e. "no
    batching" — the historical one-shot behaviour, still available by config).
    """
    files = sorted({_norm(f) for f in test_files if _norm(f)})
    if not files:
        return []
    if batch_size is None or int(batch_size) <= 0:
        return [files]
    n = int(batch_size)
    return [files[i:i + n] for i in range(0, len(files), n)]


# ── aggregation (pure) ──────────────────────────────────────────────────────
def aggregate_summaries(summaries: Iterable[dict]) -> Dict[str, int]:
    """Sum ``failed``/``passed``/``errors`` across per-batch pytest summaries."""
    agg = {"failed": 0, "passed": 0, "errors": 0}
    for s in summaries:
        for k in agg:
            agg[k] += int((s or {}).get(k, 0) or 0)
    return agg


# ── RAM gate (pure decision + injected retry loop) ──────────────────────────
def ram_ok(free_gb_val: float, min_free_gb: float) -> bool:
    """True when free RAM is at/above the threshold (boundary counts as OK)."""
    return float(free_gb_val) >= float(min_free_gb)


def free_gb(vm_fn: Optional[Callable[[], object]] = None) -> float:
    """Free (available) RAM in GiB. Uses ``psutil.virtual_memory().available``.

    Fails OPEN (returns ``inf``) if RAM cannot be measured — a missing/broken
    psutil must NOT wedge the regress; it only means the guard is inert."""
    try:
        if vm_fn is None:
            import psutil  # local import: optional dependency
            vm_fn = psutil.virtual_memory
        return float(vm_fn().available) / (1024 ** 3)
    except Exception:
        return float("inf")


def wait_for_ram(min_free_gb: float, *,
                 free_gb_fn: Callable[[], float],
                 sleep_fn: Callable[[float], None],
                 retries: int, pause_s: float) -> Tuple[bool, float]:
    """Check free RAM; while below threshold, pause and re-check up to ``retries``
    times. Returns ``(ok, last_free_gb)``. ``ok`` is False only after all retries
    are spent still starved — the caller then fails the regress honestly instead
    of starting a batch into swap."""
    last = free_gb_fn()
    if ram_ok(last, min_free_gb):
        return True, last
    for _ in range(max(0, int(retries))):
        sleep_fn(pause_s)
        last = free_gb_fn()
        if ram_ok(last, min_free_gb):
            return True, last
    return False, last


# ── orchestrator ────────────────────────────────────────────────────────────
def run_batched_regress(test_files: Iterable[str], *,
                        batch_size: int, min_free_gb: float,
                        run_batch_fn: Callable[[List[str], str], Optional[dict]],
                        free_gb_fn: Callable[[], float],
                        sleep_fn: Callable[[float], None],
                        ram_retries: int, ram_pause_s: float,
                        log_fn: Optional[Callable[[str], None]] = None) -> dict:
    """Run the suite as sequential RAM-guarded batches; aggregate to one verdict.

    ``run_batch_fn(batch_paths, label)`` runs one batch (in prod: a watchdog-
    guarded ``pytest <paths>`` spawn) and returns its parsed summary dict, or
    ``None`` if that batch timed out. ``label`` is ``"regress-batch-i/total"`` so
    the watchdog marker names the batch in flight.

    Result status:
      - ``complete``      — every batch ran; ``summary`` is the aggregate;
      - ``ram_exhausted`` — RAM stayed below threshold; batches were NOT started
                            into starvation (honest failure);
      - ``timeout``       — a batch exceeded its wall-clock (watchdog killed it).
    """
    batches = plan_batches(test_files, batch_size)
    total = len(batches)
    summaries: List[dict] = []
    for idx, batch in enumerate(batches, start=1):
        ok, free = wait_for_ram(min_free_gb, free_gb_fn=free_gb_fn,
                                sleep_fn=sleep_fn, retries=ram_retries,
                                pause_s=ram_pause_s)
        if not ok:
            if log_fn:
                log_fn("🛑 RAM-guard: свободно %.1f ГБ < порог %.1f ГБ после %d "
                       "ретраев — регресс остановлен на батче %d/%d (не голодаю)"
                       % (free, min_free_gb, ram_retries, idx, total))
            return {"status": "ram_exhausted",
                    "summary": aggregate_summaries(summaries),
                    "batches_run": idx - 1, "batches_total": total,
                    "free_gb": free, "min_free_gb": min_free_gb,
                    "ram_retries": ram_retries}
        label = "regress-batch-%d/%d" % (idx, total)
        if log_fn:
            log_fn("▶ %s (%d файлов, RAM %.1f ГБ)" % (label, len(batch), free))
        summ = run_batch_fn(batch, label)
        if summ is None:
            return {"status": "timeout",
                    "summary": aggregate_summaries(summaries),
                    "batches_run": idx - 1, "batches_total": total,
                    "min_free_gb": min_free_gb}
        summaries.append(summ)
    return {"status": "complete",
            "summary": aggregate_summaries(summaries),
            "batches_run": total, "batches_total": total,
            "min_free_gb": min_free_gb}


# ── baseline refresh ────────────────────────────────────────────────────────
def should_update_baseline(status: str, summary: dict,
                           existing_baseline: Optional[dict]) -> bool:
    """Refresh the baseline only after a COMPLETE run (never a partial/aborted
    one — that would enshrine a half-measured floor). First complete run with no
    baseline establishes it; later complete runs refresh it while not worse than
    the stored failed-count (an improved/equal floor)."""
    if status != "complete":
        return False
    if existing_baseline is None:
        return True
    return int(summary.get("failed", 0)) <= int(existing_baseline.get("failed", 0))


def write_baseline(path, summary: dict) -> Dict[str, int]:
    """Persist ``{failed,passed,errors}`` counts to the baseline JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"failed": int(summary.get("failed", 0)),
            "passed": int(summary.get("passed", 0)),
            "errors": int(summary.get("errors", 0))}
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


# ── env knobs ───────────────────────────────────────────────────────────────
def _int_env(env, name: str, default: int, *, positive: bool = False) -> int:
    try:
        v = int(env.get(name, default))
    except (TypeError, ValueError):
        return default
    if positive and v <= 0:
        return default
    return v


def _float_env(env, name: str, default: float) -> float:
    try:
        return float(env.get(name, default))
    except (TypeError, ValueError):
        return default


def batch_size_from_env(env=None) -> int:
    return _int_env(os.environ if env is None else env,
                    "REGRESS_BATCH_SIZE", DEFAULT_BATCH_SIZE, positive=True)


def min_free_gb_from_env(env=None) -> float:
    return _float_env(os.environ if env is None else env,
                      "REGRESS_MIN_FREE_GB", DEFAULT_MIN_FREE_GB)


def ram_retries_from_env(env=None) -> int:
    return _int_env(os.environ if env is None else env,
                    "REGRESS_RAM_RETRIES", DEFAULT_RAM_RETRIES)


def ram_pause_s_from_env(env=None) -> float:
    return _float_env(os.environ if env is None else env,
                      "REGRESS_RAM_PAUSE_S", DEFAULT_RAM_PAUSE_S)


# ── result → human verdict (verdict_fn injected to stay decoupled) ──────────
def summarize_result(result: dict, baseline: Optional[dict], *,
                     verdict_fn: Callable[[dict, Optional[dict]], str]) -> dict:
    """Turn a :func:`run_batched_regress` result into ``{ok, text}``.

    ``verdict_fn`` (in prod ``jarvis_observe.regress_verdict``) compares the
    aggregate summary against the baseline. RAM-exhaustion and batch-timeout are
    honest failures (``ok=False``) — never silently treated as green."""
    status = result.get("status")
    run, tot = result.get("batches_run", 0), result.get("batches_total", 0)
    if status == "ram_exhausted":
        return {"ok": False,
                "text": ("🛑 регресс прерван: недостаточно RAM (свободно %.1f ГБ "
                         "< порог %.1f ГБ) после %d ретраев — батчи не запускались "
                         "в голодание (прогнано %d/%d)"
                         % (result.get("free_gb", 0.0), result.get("min_free_gb", 0.0),
                            result.get("ram_retries", 0), run, tot))}
    if status == "timeout":
        return {"ok": False,
                "text": "⏱ батч регресса превысил таймаут (прогнано %d/%d)" % (run, tot)}
    verdict = verdict_fn(result.get("summary", {}), baseline)
    return {"ok": verdict.startswith("✅"),
            "text": "🧪 регресс батчами (%d/%d):\n%s" % (run, tot, verdict)}
