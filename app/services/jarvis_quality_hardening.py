from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path



def read_env_file(project_root: Path) -> Dict[str, str]:
    env_path = project_root / ".env"
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
def stable_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class QualityHardeningReport:
    status: str
    idempotent_memory: Dict[str, Any] = field(default_factory=dict)
    telegram_readiness: Dict[str, Any] = field(default_factory=dict)
    session_summary: Dict[str, Any] = field(default_factory=dict)
    retention: Dict[str, Any] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)


class JarvisQualityHardening:
    """
    Direction 3: Stability & Quality Hardening.

    Responsibilities:
    - dedupe memory events by stable source key
    - summarize Telegram readiness and exact missing config
    - generate richer unified night summaries
    - cleanup old artifacts with retention policy
    """

    def __init__(self, project_root: str | Path, artifacts_root: Optional[str | Path] = None) -> None:
        self.project_root = Path(project_root).resolve()
        self.artifacts_root = (
            Path(artifacts_root).resolve()
            if artifacts_root
            else self.project_root / "jarvis_stage3_artifacts" / "quality_hardening"
        )
        self.reports_dir = ensure_dir(self.artifacts_root / "reports")
        self.logs_dir = ensure_dir(self.artifacts_root / "logs")
        self.runtime_dir = ensure_dir(self.artifacts_root / "runtime")

        self.memory_root = self.project_root / "jarvis_stage3_artifacts" / "memory_learning"
        self.unified_night_root = self.project_root / "jarvis_stage3_artifacts" / "unified_night_bridge"
        self.external_root = self.project_root / "jarvis_stage3_artifacts" / "external_systems_readiness"

    def _write_json(self, path: Path, payload: Any) -> None:
        ensure_dir(path.parent)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _safe_read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _log(self, level: str, message: str) -> None:
        line = f"[{utc_now_iso()}] [{level.upper()}] {message}"
        print(line)
        with (self.logs_dir / "quality_hardening.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------------
    # 1. Memory dedupe
    # ------------------------------------------------------------------
    def dedupe_memory_events(self, dry_run: bool = False) -> Dict[str, Any]:
        events_dir = self.memory_root / "events"
        if not events_dir.exists():
            return {
                "status": "skipped",
                "reason": "memory_events_dir_missing",
                "events_before": 0,
                "duplicates": 0,
                "removed": 0,
            }

        files = sorted(events_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        seen: Dict[str, Path] = {}
        duplicates: List[Path] = []

        for path in files:
            data = self._safe_read_json(path)
            if not data:
                continue

            key_payload = {
                "event_type": data.get("event_type"),
                "source": data.get("source"),
                "source_id": data.get("source_id"),
                "status": data.get("status"),
                "summary": data.get("summary"),
            }
            key = stable_hash(key_payload)

            if key in seen:
                duplicates.append(path)
            else:
                seen[key] = path

        removed = 0
        archive_dir = ensure_dir(self.memory_root / "events_deduped_archive")

        if not dry_run:
            for dup in duplicates:
                try:
                    target = archive_dir / dup.name
                    if target.exists():
                        target = archive_dir / f"{dup.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    shutil.move(str(dup), str(target))
                    removed += 1
                except Exception as exc:
                    self._log("warn", f"failed_to_archive_duplicate path={dup} error={exc}")

        result = {
            "status": "ok",
            "dry_run": dry_run,
            "events_before": len(files),
            "unique_events": len(seen),
            "duplicates": len(duplicates),
            "removed": removed,
            "archive_dir": str(archive_dir),
        }
        self._write_json(self.runtime_dir / "memory_dedupe_result.json", result)
        return result

    # ------------------------------------------------------------------
    # 2. Telegram readiness
    # ------------------------------------------------------------------
    def telegram_readiness(self) -> Dict[str, Any]:
        env_file = read_env_file(self.project_root)

        token = os.environ.get("TELEGRAM_BOT_TOKEN") or env_file.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID") or env_file.get("TELEGRAM_ALLOWED_CHAT_ID")

        token_present = bool(token)
        chat_id_present = bool(chat_id)

        missing = []
        if not token_present:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat_id_present:
            missing.append("TELEGRAM_ALLOWED_CHAT_ID")

        status = "ready" if not missing else "not_ready"

        result = {
            "status": status,
            "token_present": token_present,
            "allowed_chat_id_present": chat_id_present,
            "missing": missing,
            "chat_id_preview": str(chat_id)[0:4] + "***" if chat_id else None,
            "next_best_action": (
                "Telegram bridge is ready for controlled status messages."
                if not missing
                else "Set missing env vars in .env or current shell: " + ", ".join(missing)
            ),
            "safe_actions_when_ready": [
                "send_session_summary",
                "send_operator_alert",
                "send_night_mode_completion_report",
            ],
        }
        self._write_json(self.runtime_dir / "telegram_readiness.json", result)
        return result

    # ------------------------------------------------------------------
    # 3. Richer session summaries
    # ------------------------------------------------------------------
    def summarize_unified_night_sessions(self, limit: int = 10) -> Dict[str, Any]:
        sessions_dir = self.unified_night_root / "sessions"
        if not sessions_dir.exists():
            return {
                "status": "skipped",
                "reason": "sessions_dir_missing",
                "sessions": [],
            }

        files = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        summaries: List[Dict[str, Any]] = []

        for file in files:
            data = self._safe_read_json(file)
            if not data:
                continue

            iterations = data.get("iterations", []) or []
            touched_loop_ids = [
                it.get("loop_run_id")
                for it in iterations
                if isinstance(it, dict) and it.get("loop_run_id")
            ]

            blocked_reasons = []
            next_best_actions = []

            for it in iterations:
                if not isinstance(it, dict):
                    continue
                blocked_reasons.extend(it.get("blocked_reasons", []) or [])
                nba = it.get("next_best_action")
                if nba:
                    next_best_actions.append(nba)

            summaries.append(
                {
                    "session_id": data.get("session_id"),
                    "status": data.get("status"),
                    "mode": data.get("mode"),
                    "completed_count": data.get("completed_count"),
                    "degraded_count": data.get("degraded_count"),
                    "failed_count": data.get("failed_count"),
                    "apply_count": data.get("apply_count"),
                    "iterations_count": len(iterations),
                    "loop_run_ids": touched_loop_ids,
                    "blocked_reasons": sorted(set(blocked_reasons)),
                    "next_best_actions": list(dict.fromkeys(next_best_actions))[:5],
                    "operator_summary": data.get("operator_summary"),
                    "created_at": data.get("created_at"),
                    "finished_at": data.get("finished_at"),
                    "source_path": str(file),
                }
            )

        aggregate = {
            "status": "ok",
            "sessions_count": len(summaries),
            "completed_sessions": sum(1 for s in summaries if s.get("status") == "completed"),
            "degraded_sessions": sum(1 for s in summaries if s.get("status") == "degraded"),
            "failed_sessions": sum(1 for s in summaries if s.get("status") == "failed"),
            "total_applies": sum(int(s.get("apply_count") or 0) for s in summaries),
            "sessions": summaries,
            "created_at": utc_now_iso(),
        }
        self._write_json(self.runtime_dir / "unified_night_session_summary.json", aggregate)
        return aggregate

    # ------------------------------------------------------------------
    # 4. Retention cleanup
    # ------------------------------------------------------------------
    def cleanup_artifacts(self, keep_latest_per_dir: int = 40, dry_run: bool = True) -> Dict[str, Any]:
        roots = [
            self.project_root / "jarvis_stage3_artifacts" / "advanced_mutation_lane" / "decisions",
            self.project_root / "jarvis_stage3_artifacts" / "advanced_mutation_lane" / "blast_radius",
            self.project_root / "jarvis_stage3_artifacts" / "memory_learning" / "snapshots",
            self.project_root / "jarvis_stage3_artifacts" / "explainability_operator_control" / "explanations",
            self.project_root / "jarvis_stage3_artifacts" / "explainability_operator_control" / "snapshots",
            self.project_root / "jarvis_stage3_artifacts" / "unified_autonomous_loop" / "runs",
            self.project_root / "jarvis_stage3_artifacts" / "unified_night_bridge" / "sessions",
        ]

        cleanup_root = ensure_dir(self.artifacts_root / "retention_archive")
        details: List[Dict[str, Any]] = []
        total_candidates = 0
        total_moved = 0

        for root in roots:
            if not root.exists():
                continue

            files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            old = files[keep_latest_per_dir:]
            total_candidates += len(old)

            moved = 0
            if not dry_run:
                archive_dir = ensure_dir(cleanup_root / root.name)
                for path in old:
                    try:
                        target = archive_dir / path.name
                        if target.exists():
                            target = archive_dir / f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        shutil.move(str(path), str(target))
                        moved += 1
                    except Exception as exc:
                        self._log("warn", f"failed_to_archive_old_artifact path={path} error={exc}")

            total_moved += moved
            details.append(
                {
                    "dir": str(root),
                    "files_count": len(files),
                    "keep_latest": keep_latest_per_dir,
                    "cleanup_candidates": len(old),
                    "moved": moved,
                }
            )

        result = {
            "status": "ok",
            "dry_run": dry_run,
            "total_cleanup_candidates": total_candidates,
            "total_moved": total_moved,
            "details": details,
            "archive_root": str(cleanup_root),
            "created_at": utc_now_iso(),
        }
        self._write_json(self.runtime_dir / "retention_cleanup_result.json", result)
        return result

    # ------------------------------------------------------------------
    # 5. Unified hardening report
    # ------------------------------------------------------------------
    def run_quality_hardening(self, dry_run_cleanup: bool = True) -> QualityHardeningReport:
        memory = self.dedupe_memory_events(dry_run=False)
        telegram = self.telegram_readiness()
        sessions = self.summarize_unified_night_sessions(limit=10)
        retention = self.cleanup_artifacts(keep_latest_per_dir=40, dry_run=dry_run_cleanup)

        recommendations: List[str] = []

        if telegram.get("status") != "ready":
            recommendations.append(telegram.get("next_best_action", "Fix Telegram readiness."))

        if memory.get("duplicates", 0) > 0:
            recommendations.append("Memory duplicates were archived. Future ingestion should use stable event keys.")

        if retention.get("total_cleanup_candidates", 0) > 0 and dry_run_cleanup:
            recommendations.append("Retention cleanup has candidates. Re-run with dry_run_cleanup=False when ready.")

        if sessions.get("failed_sessions", 0) > 0:
            recommendations.append("Inspect failed unified night sessions before expanding autonomy.")
        elif sessions.get("completed_sessions", 0) > 0:
            recommendations.append("Unified night sessions are stable. Continue expanding controlled mutation capabilities.")

        status = "ok"
        if sessions.get("failed_sessions", 0) > 0:
            status = "degraded"
        if telegram.get("status") != "ready":
            status = "ok_with_readiness_gap"

        report = QualityHardeningReport(
            status=status,
            idempotent_memory=memory,
            telegram_readiness=telegram,
            session_summary=sessions,
            retention=retention,
            recommendations=recommendations,
        )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._write_json(self.reports_dir / f"quality_hardening_{stamp}.json", asdict(report))
        self._write_json(self.runtime_dir / "latest_quality_hardening_report.json", asdict(report))
        return report