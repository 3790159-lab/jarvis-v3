"""Night Workflow Engine — Block H5.1.

Orchestrates nightly tasks across 5 phases:
  winddown (22-23), deep_work (23-02), self_improve (02-04),
  morning_prep (04-06), wakeup (06-08).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent
_STATE_DIR = _ROOT / "state" / "night_workflows"
_STATE_DIR.mkdir(parents=True, exist_ok=True)


PHASES = {
    "winddown":     {"start": 22, "end": 23, "label": "Wind-down"},
    "deep_work":    {"start": 23, "end": 2,  "label": "Deep Work"},
    "self_improve": {"start": 2,  "end": 4,  "label": "Self-Improvement"},
    "morning_prep": {"start": 4,  "end": 6,  "label": "Morning Prep"},
    "wakeup":       {"start": 6,  "end": 8,  "label": "Wake-up"},
}


def get_current_phase(hour: Optional[int] = None) -> Optional[str]:
    """Return the name of the current night phase, or None if not in any phase."""
    if hour is None:
        hour = datetime.now().hour
    for name, cfg in PHASES.items():
        start = cfg["start"]
        end = cfg["end"]
        if start < end:
            if start <= hour < end:
                return name
        else:
            # Wraps midnight: e.g. 23–02
            if hour >= start or hour < end:
                return name
    return None


def get_phase_schedule(phase_name: str) -> Optional[Dict[str, int]]:
    """Return start/end hours for a named phase."""
    return PHASES.get(phase_name)


def _log_phase_run(phase_name: str, status: str, detail: str = "") -> None:
    log_path = _STATE_DIR / "phase_log.jsonl"
    entry = {
        "ts": datetime.now().isoformat(),
        "phase": phase_name,
        "status": status,
        "detail": detail[:300],
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _save_phase_state(phase_name: str, data: Dict[str, Any]) -> None:
    path = _STATE_DIR / f"{phase_name}_last.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_phase_state(phase_name: str) -> Dict[str, Any]:
    path = _STATE_DIR / f"{phase_name}_last.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


class NightWorkflow:
    """Orchestrates all nightly automation tasks."""

    def __init__(self, send_fn: Optional[Callable[[str], None]] = None) -> None:
        self._send = send_fn or (lambda msg: logger.info("[Night] %s", msg))

    def notify(self, msg: str) -> None:
        try:
            self._send(msg)
        except Exception:
            logger.info("[Night notify] %s", msg)

    # ── Phase: winddown (22-23) ───────────────────────────────────────────────

    async def run_phase_winddown(self) -> Dict[str, Any]:
        """22-23: Generate recap, cleanup temp files, backup state."""
        results: Dict[str, Any] = {}
        _log_phase_run("winddown", "started")
        try:
            recap = self.generate_daily_recap()
            results["recap"] = recap
        except Exception as exc:
            results["recap_error"] = str(exc)
            logger.warning("[winddown] recap error: %s", exc)

        try:
            cleaned = self.cleanup_temp_files()
            results["cleaned_files"] = cleaned
        except Exception as exc:
            results["cleanup_error"] = str(exc)

        try:
            backed_up = self.backup_state()
            results["backed_up"] = backed_up
        except Exception as exc:
            results["backup_error"] = str(exc)

        _save_phase_state("winddown", results)
        _log_phase_run("winddown", "completed", str(results))
        return results

    # ── Phase: deep_work (23-02) ──────────────────────────────────────────────

    async def run_phase_deep_work(self) -> Dict[str, Any]:
        """23-02: Content generation and trend analysis."""
        results: Dict[str, Any] = {}
        _log_phase_run("deep_work", "started")
        try:
            posts = self.generate_tomorrow_content()
            results["scheduled_posts"] = len(posts)
        except Exception as exc:
            results["content_error"] = str(exc)
            logger.warning("[deep_work] content error: %s", exc)

        try:
            trends = self.analyze_industry_trends()
            results["trends_found"] = len(trends.get("food_trends", []))
        except Exception as exc:
            results["trends_error"] = str(exc)

        _save_phase_state("deep_work", results)
        _log_phase_run("deep_work", "completed")
        return results

    # ── Phase: self_improve (02-04) ───────────────────────────────────────────

    async def run_phase_self_improve(self) -> Dict[str, Any]:
        """02-04: Analyze errors and optimize prompts."""
        results: Dict[str, Any] = {}
        _log_phase_run("self_improve", "started")
        try:
            errors_analyzed = self.analyze_daily_errors()
            results["errors_analyzed"] = errors_analyzed
        except Exception as exc:
            results["analyze_error"] = str(exc)
            logger.warning("[self_improve] error: %s", exc)

        try:
            optimized = self.optimize_prompts()
            results["prompts_optimized"] = optimized
        except Exception as exc:
            results["optimize_error"] = str(exc)

        _save_phase_state("self_improve", results)
        _log_phase_run("self_improve", "completed")
        return results

    # ── Phase: morning_prep (04-06) ───────────────────────────────────────────

    async def run_phase_morning_prep(self) -> Dict[str, Any]:
        """04-06: Fetch data and generate morning brief."""
        results: Dict[str, Any] = {}
        _log_phase_run("morning_prep", "started")
        try:
            data = self.fetch_morning_data()
            results["data_fetched"] = bool(data)
        except Exception as exc:
            results["fetch_error"] = str(exc)

        try:
            brief = self.generate_morning_brief()
            results["brief_generated"] = bool(brief)
            results["brief_length"] = len(brief) if brief else 0
        except Exception as exc:
            results["brief_error"] = str(exc)

        _save_phase_state("morning_prep", results)
        _log_phase_run("morning_prep", "completed")
        return results

    # ── Phase: wakeup (06-08) ─────────────────────────────────────────────────

    async def run_phase_wakeup(self) -> Dict[str, Any]:
        """06-08: Send morning brief and notify user."""
        results: Dict[str, Any] = {}
        _log_phase_run("wakeup", "started")
        try:
            sent = self.send_morning_brief()
            results["brief_sent"] = sent
        except Exception as exc:
            results["send_error"] = str(exc)

        try:
            self.notify_user()
            results["notified"] = True
        except Exception as exc:
            results["notify_error"] = str(exc)

        _save_phase_state("wakeup", results)
        _log_phase_run("wakeup", "completed")
        return results

    # ── Task implementations ──────────────────────────────────────────────────

    def generate_daily_recap(self) -> Dict[str, Any]:
        """Generate daily recap from decisions and feedback."""
        try:
            import sys
            root = str(_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from app.services.daily_recap import generate_daily_recap as _recap
            return _recap()
        except Exception as exc:
            return {"error": str(exc), "date": date.today().isoformat()}

    def cleanup_temp_files(self) -> int:
        """Delete temp files older than 24h. Returns count deleted."""
        import time
        temp_dir = _ROOT / "state" / "incoming_files"
        deleted = 0
        if temp_dir.exists():
            cutoff = time.time() - 86400
            for f in temp_dir.iterdir():
                try:
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        f.unlink()
                        deleted += 1
                except Exception:
                    pass
        return deleted

    def backup_state(self) -> bool:
        """Copy key state files to state/backups/<date>/."""
        backup_dir = _ROOT / "state" / "backups" / date.today().isoformat()
        backup_dir.mkdir(parents=True, exist_ok=True)
        key_files = [
            _ROOT / "state" / "decisions.jsonl",
            _ROOT / "state" / "loras" / "index.json",
            _ROOT / "state" / "scheduled_tasks.json",
        ]
        backed = 0
        for src in key_files:
            if src.exists():
                try:
                    import shutil
                    shutil.copy2(str(src), str(backup_dir / src.name))
                    backed += 1
                except Exception:
                    pass
        return backed > 0

    def generate_tomorrow_content(self) -> List[Dict[str, Any]]:
        """Generate scheduled content posts for tomorrow."""
        try:
            import sys
            root = str(_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from app.services.auto_content import generate_tomorrow_content as _gtc
            return _gtc()
        except Exception as exc:
            logger.warning("[night] tomorrow content error: %s", exc)
            return []

    def analyze_industry_trends(self) -> Dict[str, Any]:
        """Fetch and analyze industry trends."""
        try:
            import sys
            root = str(_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from app.services.trend_analyzer import analyze_industry_trends as _ait
            return _ait()
        except Exception as exc:
            return {"food_trends": [], "error": str(exc)}

    def analyze_daily_errors(self) -> int:
        """Count and analyze errors from today."""
        try:
            from app.services.error_reporter import get_recent_errors
            errors = get_recent_errors(50)
            return len(errors)
        except Exception:
            return 0

    def optimize_prompts(self) -> int:
        """Run self-improvement loop. Returns number of prompts improved."""
        try:
            import sys
            root = str(_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from app.services.self_improvement import SelfImprovementLoop
            loop = SelfImprovementLoop()
            feedback = loop.collect_feedback(days=1)
            patterns = loop.analyze_negatives(feedback)
            improved = 0
            for p in patterns:
                try:
                    new_prompt = loop.optimize_prompt(
                        p["intent"], "", p.get("analysis", "")
                    )
                    ab = loop.ab_test("", new_prompt, [])
                    loop.apply_if_better(p["intent"], new_prompt, ab)
                    improved += 1
                except Exception:
                    pass
            return improved
        except Exception as exc:
            logger.warning("[optimize_prompts] error: %s", exc)
            return 0

    def fetch_morning_data(self) -> Dict[str, Any]:
        """Fetch data needed for morning brief."""
        data: Dict[str, Any] = {}
        # Load last deep_work trends
        dw_state = _load_phase_state("deep_work")
        data["trends_ready"] = dw_state.get("trends_found", 0) > 0
        data["posts_ready"] = dw_state.get("scheduled_posts", 0) > 0
        return data

    def generate_morning_brief(self) -> str:
        """Generate morning brief text."""
        try:
            import sys
            root = str(_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from app.services.daily_recap import format_recap_for_telegram
            recap = _load_phase_state("winddown").get("recap", {})
            if recap:
                brief = format_recap_for_telegram(recap)
            else:
                brief = "☀️ Доброе утро! Ночной анализ завершён.\n\nДетали: /night_report"
            brief_path = _STATE_DIR / "morning_brief.txt"
            brief_path.write_text(brief, encoding="utf-8")
            return brief
        except Exception as exc:
            fallback = f"☀️ Доброе утро! (ошибка брифа: {exc})"
            (_STATE_DIR / "morning_brief.txt").write_text(fallback, encoding="utf-8")
            return fallback

    def send_morning_brief(self) -> bool:
        """Send morning brief via Telegram notification."""
        brief_path = _STATE_DIR / "morning_brief.txt"
        if brief_path.exists():
            brief = brief_path.read_text(encoding="utf-8")
        else:
            brief = "☀️ Доброе утро! Ночной цикл завершён."
        self.notify(brief)
        return True

    def notify_user(self) -> None:
        """Send final wake-up notification."""
        self.notify(
            "🌅 Ночной цикл завершён.\n\n"
            "• /night_report — детальный отчёт\n"
            "• /improve stats — статистика\n"
            "• /schedule list — запланированный контент"
        )

    # ── Scheduler integration ─────────────────────────────────────────────────

    def schedule_all_phases(self, scheduler: Any = None) -> List[str]:
        """Register all night phases with the scheduler. Returns task IDs."""
        if scheduler is None:
            try:
                import sys
                root = str(_ROOT)
                if root not in sys.path:
                    sys.path.insert(0, root)
                from app.services.scheduler import JarvisScheduler, _load_tasks
                scheduler = JarvisScheduler()
                # Load existing tasks to avoid overwriting reminders etc.
                with scheduler._lock:
                    scheduler._tasks = _load_tasks()
            except Exception as exc:
                logger.warning("[schedule_all_phases] scheduler unavailable: %s", exc)
                return []

        # Phase I.1: Remove existing night_* tasks to prevent duplicates on restart
        with scheduler._lock:
            scheduler._tasks = [
                t for t in scheduler._tasks
                if not str(t.get("action", "")).startswith("night_")
            ]

        task_ids: List[str] = []
        phase_actions = {
            "winddown":     "0 22 * * *",
            "deep_work":    "0 23 * * *",
            "self_improve": "0 2 * * *",
            "morning_prep": "0 4 * * *",
            "wakeup":       "0 6 * * *",
        }
        for phase_name, cron in phase_actions.items():
            try:
                task_id = scheduler.add_task(
                    action=f"night_{phase_name}",
                    params={"phase": phase_name},
                    cron=cron,
                    chat_id=None,
                )
                task_ids.append(task_id)
            except Exception as exc:
                logger.warning("[schedule] %s failed: %s", phase_name, exc)

        return task_ids

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Return current autonomy status."""
        current_phase = get_current_phase()
        statuses = {}
        for name in PHASES:
            state = _load_phase_state(name)
            statuses[name] = {
                "last_run": state.get("ts") or state.get("started_at"),
                "active": name == current_phase,
            }
        return {
            "current_phase": current_phase,
            "phases": statuses,
        }

    def get_phase_log(self, n: int = 20) -> List[Dict[str, Any]]:
        """Return last N phase log entries."""
        log_path = _STATE_DIR / "phase_log.jsonl"
        if not log_path.exists():
            return []
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
            return [json.loads(l) for l in lines[-n:] if l.strip()]
        except Exception:
            return []
