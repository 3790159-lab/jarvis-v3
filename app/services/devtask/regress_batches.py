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
DEFAULT_BATCH_SIZE = 40         # test files per batch (deterministic chunk)
DEFAULT_MIN_FREE_GB = 3.0       # do not START a batch below this free RAM
DEFAULT_RAM_RETRIES = 6         # pause+recheck this many times before honest fail
DEFAULT_RAM_PAUSE_S = 10        # seconds between RAM rechecks
DEFAULT_BATCH_KILL_FREE_GB = 2.5  # kill a RUNNING batch if free RAM drops below this
DEFAULT_BATCH_SAMPLE_S = 2.0      # sample free RAM this often while a batch runs


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
def aggregate_summaries(summaries: Iterable[dict]) -> Dict[str, object]:
    """Сложить ``failed``/``passed``/``errors`` и СОБРАТЬ имена по батчам.

    Имена объединяются, а не суммируются: один и тот же тест не может упасть
    в двух батчах, но объединение честнее конкатенации, если план батчей
    когда-нибудь начнёт перекрываться.

    Ключ ``failed_names`` появляется ТОЛЬКО когда хотя бы один батч его
    принёс. Пустой список означал бы «падений по именам нет», а это не то же
    самое, что «имён не собирали» — и вердикт эти случаи различает.
    """
    agg: Dict[str, object] = {"failed": 0, "passed": 0, "errors": 0}
    names: set = set()
    saw_names = False
    for s in summaries:
        for k in ("failed", "passed", "errors"):
            agg[k] = int(agg[k]) + int((s or {}).get(k, 0) or 0)
        got = (s or {}).get("failed_names")
        if got is not None:
            saw_names = True
            names.update(got)
    if saw_names:
        agg["failed_names"] = sorted(names)
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
    ram_killed: List[dict] = []
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
                    "ram_killed": ram_killed, "min_free_gb": min_free_gb}
        if summ.get("ram_killed"):
            # Batch ballooned mid-flight and the intra-batch guard tree-killed it.
            # Mark it failed, name its files, and KEEP GOING with the rest —
            # one poisoned batch must not blind the whole regress.
            killed_free = summ.get("free_gb")
            ram_killed.append({"idx": idx, "free_gb": killed_free,
                               "files": list(batch)})
            if log_fn:
                log_fn("🛑 RAM-guard: батч %d/%d убит на лету (свободно %.1f ГБ) — "
                       "помечен failed, продолжаю остальные"
                       % (idx, total, float(killed_free or 0.0)))
            continue
        summaries.append(summ)
    if ram_killed:
        return {"status": "ram_killed",
                "summary": aggregate_summaries(summaries),
                "batches_run": total - len(ram_killed), "batches_total": total,
                "ram_killed": ram_killed, "min_free_gb": min_free_gb}
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


def write_baseline(path, summary: dict) -> Dict[str, object]:
    """Обновить эталон, СОХРАНИВ всё, что в нём уже лежит.

    🔴 Раньше эта функция писала ровно три числа — то есть затирала бы
    поимённый список и метаданные при первом же зелёном прогоне. Эталон,
    который сам себя обедняет, хуже отсутствующего: он выглядит свежим.

    Поэтому существующий файл читается, и новые значения ЛОЖАТСЯ ПОВЕРХ него;
    незнакомые ключи (`taken_at`, `composition`, `note`, `replaces`, …)
    остаются нетронутыми. Имена пишутся только если прогон их принёс.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, object] = {}
    try:
        existing = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            data.update(existing)
    except (OSError, ValueError):
        pass
    data["failed"] = int(summary.get("failed", 0))
    data["passed"] = int(summary.get("passed", 0))
    data["errors"] = int(summary.get("errors", 0))
    names = summary.get("failed_names")
    if names is not None:
        data["known_failures"] = sorted(names)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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


def batch_kill_free_gb_from_env(env=None) -> float:
    return _float_env(os.environ if env is None else env,
                      "REGRESS_BATCH_KILL_FREE_GB", DEFAULT_BATCH_KILL_FREE_GB)


def batch_sample_s_from_env(env=None) -> float:
    return _float_env(os.environ if env is None else env,
                      "REGRESS_BATCH_SAMPLE_S", DEFAULT_BATCH_SAMPLE_S)


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
    if status == "ram_killed":
        killed = result.get("ram_killed", [])
        lines = "\n".join(
            "  • батч %s/%d (свободно %.1f ГБ): %s"
            % (k.get("idx"), tot, float(k.get("free_gb") or 0.0),
               ", ".join(k.get("files", [])))
            for k in killed)
        verdict = verdict_fn(result.get("summary", {}), baseline)
        return {"ok": False,
                "text": ("🛑 регресс: %d батч(ей) убито RAM-guard'ом на лету "
                         "(память ниже порога) — помечены failed:\n%s\n"
                         "остальные прогнаны (%d/%d):\n%s"
                         % (len(killed), lines, run, tot, verdict))}
    verdict = verdict_fn(result.get("summary", {}), baseline)
    return {"ok": verdict.startswith("✅"),
            "text": "🧪 регресс батчами (%d/%d):\n%s" % (run, tot, verdict)}
