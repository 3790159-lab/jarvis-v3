# -*- coding: utf-8 -*-
"""Weekly garbage-cleanup: classify + (optionally) delete stale disk debris.

Four categories, mirroring the spec:
  * ``state/incoming_files`` entries older than 14 days
  * ``artifacts/*_tmp`` dirs/files (min-age guard to dodge a mid-write race)
  * rotated log files (``*.log.1``, ``*.log.2026-01-01``, ``*.log.gz`` — never
    the live ``*.log`` itself) older than 30 days
  * ``devtask-*`` worktree remnants older than 7 days whose branch status is
    ``merged``/``rolled_back`` (anything else — running/queued/failed/unknown —
    is left alone; this is the only category that touches a directory outside
    the caller's own worktree, so it is the most conservative classifier)

Classifiers are pure (no I/O) so they get direct boundary tests. Scanners take
an injectable base dir and do real I/O but never decide the base dir
themselves (the caller wires real vs. tmp_path paths). Deletion is a separate,
explicit step gated by ``dry_run`` — the caller decides whether to call it.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "INCOMING_FILES_MAX_AGE_DAYS",
    "LOG_ROTATION_MAX_AGE_DAYS",
    "WORKTREE_MIN_AGE_DAYS",
    "TMP_ARTIFACT_MIN_AGE_DAYS",
    "CATEGORY_INCOMING",
    "CATEGORY_TMP_ARTIFACT",
    "CATEGORY_LOG_ROTATION",
    "CATEGORY_WORKTREE",
    "is_incoming_file_stale",
    "is_tmp_artifact_name",
    "is_rotated_log_name",
    "is_log_rotation_stale",
    "is_worktree_remnant_deletable",
    "scan_incoming_files",
    "scan_tmp_artifacts",
    "scan_log_rotation",
    "scan_worktree_remnants",
    "build_report",
    "format_report_message",
    "delete_candidates",
    "run_cleanup",
]

CATEGORY_INCOMING = "incoming_files"
CATEGORY_TMP_ARTIFACT = "tmp_artifacts"
CATEGORY_LOG_ROTATION = "log_rotation"
CATEGORY_WORKTREE = "worktree_remnants"

INCOMING_FILES_MAX_AGE_DAYS = 14
LOG_ROTATION_MAX_AGE_DAYS = 30
WORKTREE_MIN_AGE_DAYS = 7
# *_tmp artifacts have no age criterion in the spec; this small floor just
# avoids racing a concurrent process that is still writing into a fresh tmp dir.
TMP_ARTIFACT_MIN_AGE_DAYS = 1

_DELETABLE_BRANCH_STATUSES = frozenset({"merged", "rolled_back"})
_ROTATED_LOG_RE = re.compile(r"\.log(\.\d+|\.\d{4}-\d{2}-\d{2}|\.gz)$", re.IGNORECASE)

_CATEGORY_LABELS = {
    CATEGORY_INCOMING: f"📥 incoming_files (>{INCOMING_FILES_MAX_AGE_DAYS}д)",
    CATEGORY_TMP_ARTIFACT: "🗑 artifacts/*_tmp",
    CATEGORY_LOG_ROTATION: f"📜 логи, ротация (>{LOG_ROTATION_MAX_AGE_DAYS}д)",
    CATEGORY_WORKTREE: f"🌳 worktree-остатки (>{WORKTREE_MIN_AGE_DAYS}д, merged/rolled_back)",
}


def _age_days(mtime: datetime, now: datetime) -> float:
    return (now - mtime).total_seconds() / 86400.0


def _mtime_of(path: Path) -> datetime:
    return datetime.utcfromtimestamp(path.stat().st_mtime)


def _path_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


# ─── pure classifiers ─────────────────────────────────────────────────────────

def is_incoming_file_stale(mtime: datetime, now: datetime,
                            max_age_days: int = INCOMING_FILES_MAX_AGE_DAYS) -> bool:
    return _age_days(mtime, now) > max_age_days


def is_tmp_artifact_name(name: str) -> bool:
    return name.endswith("_tmp")


def is_rotated_log_name(name: str) -> bool:
    return bool(_ROTATED_LOG_RE.search(name))


def is_log_rotation_stale(name: str, mtime: datetime, now: datetime,
                           max_age_days: int = LOG_ROTATION_MAX_AGE_DAYS) -> bool:
    if not is_rotated_log_name(name):
        return False
    return _age_days(mtime, now) > max_age_days


def is_worktree_remnant_deletable(branch_status: Optional[str], mtime: datetime, now: datetime,
                                   min_age_days: int = WORKTREE_MIN_AGE_DAYS) -> bool:
    if branch_status not in _DELETABLE_BRANCH_STATUSES:
        return False
    return _age_days(mtime, now) >= min_age_days


# ─── scanners (I/O, injectable base dirs) ─────────────────────────────────────

def scan_incoming_files(base_dir: Path, now: datetime,
                         max_age_days: int = INCOMING_FILES_MAX_AGE_DAYS) -> List[Dict[str, Any]]:
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for entry in base_dir.iterdir():
        mtime = _mtime_of(entry)
        if is_incoming_file_stale(mtime, now, max_age_days):
            out.append({
                "path": str(entry),
                "category": CATEGORY_INCOMING,
                "size_bytes": _path_size(entry),
                "reason": f"older than {max_age_days}d",
            })
    return out


def scan_tmp_artifacts(base_dir: Path, now: datetime,
                        min_age_days: int = TMP_ARTIFACT_MIN_AGE_DAYS) -> List[Dict[str, Any]]:
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for entry in base_dir.iterdir():
        if not is_tmp_artifact_name(entry.name):
            continue
        mtime = _mtime_of(entry)
        if _age_days(mtime, now) < min_age_days:
            continue
        out.append({
            "path": str(entry),
            "category": CATEGORY_TMP_ARTIFACT,
            "size_bytes": _path_size(entry),
            "reason": "*_tmp artifact",
        })
    return out


def scan_log_rotation(base_dir: Path, now: datetime,
                       max_age_days: int = LOG_ROTATION_MAX_AGE_DAYS) -> List[Dict[str, Any]]:
    base_dir = Path(base_dir)
    if not base_dir.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for entry in base_dir.iterdir():
        if not entry.is_file():
            continue
        mtime = _mtime_of(entry)
        if is_log_rotation_stale(entry.name, mtime, now, max_age_days):
            out.append({
                "path": str(entry),
                "category": CATEGORY_LOG_ROTATION,
                "size_bytes": _path_size(entry),
                "reason": f"rotated log older than {max_age_days}d",
            })
    return out


def scan_worktree_remnants(wt_root: Path, get_status: Callable[[str], Optional[str]], now: datetime,
                            min_age_days: int = WORKTREE_MIN_AGE_DAYS,
                            self_worktree: Optional[str] = None,
                            prefix: str = "devtask-") -> List[Dict[str, Any]]:
    wt_root = Path(wt_root)
    if not wt_root.is_dir():
        return []
    self_name = Path(self_worktree).name if self_worktree else None
    out: List[Dict[str, Any]] = []
    for entry in wt_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        if self_name and entry.name == self_name:
            continue  # never touch the worktree this scan is itself running in
        task_id = entry.name[len(prefix):]
        status = get_status(task_id)
        mtime = _mtime_of(entry)
        if is_worktree_remnant_deletable(status, mtime, now, min_age_days):
            out.append({
                "path": str(entry),
                "category": CATEGORY_WORKTREE,
                "size_bytes": _path_size(entry),
                "reason": f"branch status={status}",
                "task_id": task_id,
            })
    return out


# ─── report + formatting ───────────────────────────────────────────────────────

def build_report(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_category: Dict[str, Dict[str, int]] = {}
    for c in candidates:
        agg = by_category.setdefault(c["category"], {"count": 0, "size_bytes": 0})
        agg["count"] += 1
        agg["size_bytes"] += c.get("size_bytes", 0)
    total_bytes = sum(c.get("size_bytes", 0) for c in candidates)
    return {
        "categories": {
            cat: {"count": v["count"], "size_mb": round(v["size_bytes"] / (1024 * 1024), 2)}
            for cat, v in by_category.items()
        },
        "total_count": len(candidates),
        "total_mb": round(total_bytes / (1024 * 1024), 2),
    }


def format_report_message(report: Dict[str, Any], dry_run: bool) -> str:
    header = "🧹 Уборка мусора" + (" (DRY-RUN, ничего не удалено)" if dry_run else " — выполнено")
    lines = [header, ""]
    if not report["categories"]:
        lines.append("Мусора не найдено — диск чист.")
        return "\n".join(lines)

    for cat, label in _CATEGORY_LABELS.items():
        info = report["categories"].get(cat)
        if not info:
            continue
        lines.append(f"{label}: {info['count']} шт, {info['size_mb']:.2f} МБ")

    lines.append("")
    verb = "Освободится" if dry_run else "Освобождено"
    lines.append(f"{verb}: {report['total_mb']:.2f} МБ ({report['total_count']} объектов)")
    return "\n".join(lines)


# ─── deletion (explicit, separate from scanning) ───────────────────────────────

def delete_candidates(candidates: List[Dict[str, Any]], *,
                       remove_worktree_fn: Optional[Callable[[str], None]] = None
                       ) -> List[Dict[str, Any]]:
    """Delete each candidate. Worktree remnants go through ``remove_worktree_fn``
    (real git worktree remove + branch delete); everything else is unlinked or
    rmtree'd directly. Never raises — failures are captured per-item."""
    results: List[Dict[str, Any]] = []
    for c in candidates:
        result = dict(c)
        path = Path(c["path"])
        try:
            if c["category"] == CATEGORY_WORKTREE and remove_worktree_fn:
                remove_worktree_fn(c["task_id"])
                result["deleted"] = True
            elif path.is_dir():
                shutil.rmtree(path)
                result["deleted"] = True
            elif path.exists():
                path.unlink()
                result["deleted"] = True
            else:
                result["deleted"] = False
        except Exception as exc:
            result["deleted"] = False
            result["error"] = str(exc)
        results.append(result)
    return results


# ─── orchestrator ───────────────────────────────────────────────────────────────

def run_cleanup(*, incoming_dir: Path, artifacts_dir: Path, logs_dir: Path, wt_root: Path,
                 get_status: Callable[[str], Optional[str]], now: datetime, dry_run: bool,
                 self_worktree: Optional[str] = None,
                 remove_worktree_fn: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Scan all four categories and (unless ``dry_run``) delete what's found.
    Returns the aggregated report (``build_report`` output + ``dry_run`` flag)."""
    candidates: List[Dict[str, Any]] = []
    candidates += scan_incoming_files(incoming_dir, now)
    candidates += scan_tmp_artifacts(artifacts_dir, now)
    candidates += scan_log_rotation(logs_dir, now)
    candidates += scan_worktree_remnants(wt_root, get_status, now, self_worktree=self_worktree)

    report = build_report(candidates)
    if not dry_run and candidates:
        delete_candidates(candidates, remove_worktree_fn=remove_worktree_fn)
    report["dry_run"] = dry_run
    return report
