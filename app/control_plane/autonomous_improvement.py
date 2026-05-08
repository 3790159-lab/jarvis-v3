from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .execution_observer import ExecutionObserver
from .improvement_governor import ImprovementGovernor
from .improvement_journal import ImprovementJournal
from .improvement_planner import ImprovementPlanner
from .knowledge_compactor import KnowledgeCompactor
from .lifecycle_manager import LifecycleManager
from .n8n_manager import N8NManager
from .self_healing import SelfHealingEngine


class AutonomousImprovementController:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.state_path = self.base_dir / "autonomous_improvement_state.json"
        self.history_path = self.base_dir / "autonomous_improvement_history.jsonl"
        self.observer = ExecutionObserver(self.base_dir)
        self.compactor = KnowledgeCompactor(self.base_dir)
        self.lifecycle = LifecycleManager(self.base_dir)
        self.healing = SelfHealingEngine(self.base_dir)
        self.planner = ImprovementPlanner(self.base_dir)
        self.governor = ImprovementGovernor(self.base_dir)
        self.n8n = N8NManager(self.base_dir)
        self.journal = ImprovementJournal(self.base_dir)
        self._lock = threading.Lock()
        self._thread = None
        self._ensure_state()

    def _ensure_state(self) -> None:
        if not self.state_path.exists():
            self.state_path.write_text(json.dumps({
                "enabled": True,
                "continuous_enabled": True,
                "interval_seconds": 120,
                "event_driven_min_interval_seconds": 120,
                "last_tick_ts": 0.0,
                "last_eventful_tick_ts": 0.0,
                "observed_task_keys": [],
                "last_report": {},
                "stop_requested": False,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_state(self) -> dict:
        self._ensure_state()
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_history(self, row: dict) -> None:
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _iter_snapshot_files(self) -> list[Path]:
        files = []
        for folder in ["active_snapshots", "archive_snapshots"]:
            d = self.base_dir / folder
            if d.exists():
                files.extend(list(d.glob("*.json")))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:120]

    def start_continuous(self, interval_seconds: int | None = None) -> dict:
        state = self.load_state()
        state["enabled"] = True
        state["continuous_enabled"] = True
        state["stop_requested"] = False
        if interval_seconds is not None:
            state["interval_seconds"] = int(max(120, interval_seconds))
        self.save_state(state)
        self.start_background()
        return {
            "enabled": state["enabled"],
            "continuous_enabled": state["continuous_enabled"],
            "interval_seconds": state["interval_seconds"],
        }

    def stop_continuous(self) -> dict:
        state = self.load_state()
        state["continuous_enabled"] = False
        state["stop_requested"] = True
        self.save_state(state)
        return {
            "enabled": state["enabled"],
            "continuous_enabled": state["continuous_enabled"],
            "interval_seconds": state["interval_seconds"],
        }

    def run_safe_tick(self, reason: str = "manual") -> dict:
        with self._lock:
            state = self.load_state()
            if not state.get("enabled", True):
                report = {"status": "disabled", "reason": reason}
                state["last_report"] = report
                self.save_state(state)
                return report

            observed = 0
            normalized = 0
            telemetry_added = 0
            traces_added = 0

            for path in self._iter_snapshot_files():
                row = self.observer.observe_snapshot_file(path, state)
                observed += int(row.get("observed", 0))
                normalized += int(row.get("normalized", 0))
                telemetry_added += int(row.get("telemetry_added", 0))
                traces_added += int(row.get("traces_added", 0))

            compact_report = self.compactor.compact()
            lifecycle_health = self.lifecycle.health()

            cleanup_report = {}
            if int(lifecycle_health.get("active_snapshots", 0)) >= 8:
                cleanup_report = self.lifecycle.cleanup(
                    retain_recent_completed=8,
                    max_journal_lines_per_mission=380,
                    max_exec_log_lines=650,
                )

            healing_report = self.healing.tick(reason=reason)
            telemetry = self.observer.telemetry.summary()
            traces = self.observer.trace.tail(limit=200)
            n8n_health = self.n8n.health()

            mismatch_count = 0
            for item in traces:
                desired = str(item.get("desired_service") or "")
                actual = str(item.get("actual_service") or "")
                if desired and actual and desired != actual:
                    mismatch_count += 1

            planner_report = self.planner.analyze(
                telemetry_summary=telemetry,
                lifecycle_health=lifecycle_health,
                healing_state=healing_report,
                n8n_health=n8n_health,
            )

            governor_report = self.governor.apply_safe_improvements(planner_report)

            eventful = any([
                observed > 0,
                telemetry_added > 0,
                traces_added > 0,
                len((governor_report or {}).get("applied", []) or []) > 0,
                len((governor_report or {}).get("retired", []) or []) > 0,
                mismatch_count > 0,
            ])

            if eventful:
                state["last_eventful_tick_ts"] = time.time()

            report = {
                "status": "ok",
                "reason": reason,
                "observed_tasks": observed,
                "normalized_tasks": normalized,
                "telemetry_added": telemetry_added,
                "traces_added": traces_added,
                "mismatch_count_recent": mismatch_count,
                "compact_report": compact_report,
                "lifecycle_health": lifecycle_health,
                "cleanup_report": cleanup_report,
                "healing_report": healing_report,
                "planner_report": planner_report,
                "governor_report": governor_report,
                "n8n_health": n8n_health,
                "telemetry_summary": telemetry,
                "eventful": eventful,
                "ts": time.time(),
            }

            state["last_tick_ts"] = time.time()
            state["last_report"] = report
            state["observed_task_keys"] = list(state.get("observed_task_keys", []))[-8000:]
            self.save_state(state)
            self._append_history(report)
            self.journal.append(report)
            return report

    def health(self) -> dict:
        state = self.load_state()
        return {
            "enabled": state.get("enabled", True),
            "continuous_enabled": state.get("continuous_enabled", True),
            "interval_seconds": state.get("interval_seconds", 120),
            "event_driven_min_interval_seconds": state.get("event_driven_min_interval_seconds", 120),
            "last_tick_ts": state.get("last_tick_ts", 0.0),
            "last_eventful_tick_ts": state.get("last_eventful_tick_ts", 0.0),
            "last_report": state.get("last_report", {}),
            "history_exists": self.history_path.exists(),
            "journal_exists": (self.base_dir / "improvement_journal.jsonl").exists(),
            "planner_report": self.planner.load_report(),
        }

    def history(self, limit: int = 50) -> list[dict]:
        if not self.history_path.exists():
            return []
        lines = self.history_path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-max(1, limit):]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def start_background(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False

        def loop():
            while True:
                try:
                    state = self.load_state()
                    if state.get("stop_requested", False):
                        break
                    if not state.get("continuous_enabled", True):
                        time.sleep(2)
                        continue

                    interval_seconds = int(state.get("interval_seconds", 120) or 120)
                    self.run_safe_tick(reason="continuous_background")
                    time.sleep(max(120, interval_seconds))
                except Exception:
                    time.sleep(10)

        self._thread = threading.Thread(target=loop, name="jarvis_autonomy_loop_v17_2", daemon=True)
        self._thread.start()
        return True