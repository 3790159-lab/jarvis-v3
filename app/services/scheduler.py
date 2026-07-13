"""Phase 28: Jarvis Scheduler — persistent scheduled tasks with APScheduler.

Supports:
  - remind: send a reminder text at a given datetime
  - research: run research and DM result
  - n8n_run: trigger n8n workflow
  - morning_brief: daily morning summary

State persisted in state/scheduled_tasks.json (survives restart).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_PATH = Path("state") / "scheduled_tasks.json"
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_tasks() -> List[Dict[str, Any]]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_tasks(tasks: List[Dict[str, Any]]) -> None:
    STATE_PATH.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# JarvisScheduler
# ---------------------------------------------------------------------------

class JarvisScheduler:
    """Manages scheduled tasks. Persists to state/scheduled_tasks.json."""

    def __init__(self, send_fn: Optional[Callable[[str, str], None]] = None) -> None:
        self._lock = threading.Lock()
        self._tasks: List[Dict[str, Any]] = []
        self._scheduler = None
        self._send_fn = send_fn  # callback(chat_id, text)
        self._started = False

    # ── public API ──────────────────────────────────────────────────────────

    def add_task(
        self,
        action: str,
        params: Dict[str, Any],
        run_at: Optional[datetime] = None,
        cron: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        chat_id: str = "",
    ) -> str:
        """Schedule a task. Returns task_id.

        action values:
          remind      — send params["text"] to chat_id at run_at
          research    — run research(params["query"]) and send result
          n8n_run     — trigger n8n workflow params["workflow_id"]
          morning_brief — send daily summary

        Scheduling modes (mutually exclusive):
          run_at            — one-shot at specific datetime
          cron              — APScheduler cron string "H M dom mon dow"
          interval_seconds  — repeat every N seconds
        """
        task_id = uuid.uuid4().hex[:12]
        task: Dict[str, Any] = {
            "task_id": task_id,
            "action": action,
            "params": params,
            "chat_id": chat_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        if run_at:
            task["run_at"] = run_at.isoformat()
            task["schedule_type"] = "once"
        elif cron:
            task["cron"] = cron
            task["schedule_type"] = "cron"
        elif interval_seconds:
            task["interval_seconds"] = interval_seconds
            task["schedule_type"] = "interval"
        else:
            raise ValueError("Must provide run_at, cron, or interval_seconds")

        with self._lock:
            self._tasks.append(task)
            _save_tasks(self._tasks)

        if self._started and self._scheduler:
            self._register_job(task)

        logger.info("Scheduled task %s action=%s", task_id, action)
        return task_id

    def remove_task(self, task_id: str) -> bool:
        """Cancel a scheduled task. Returns True if found and removed."""
        with self._lock:
            before = len(self._tasks)
            self._tasks = [t for t in self._tasks if t["task_id"] != task_id]
            removed = len(self._tasks) < before
            if removed:
                _save_tasks(self._tasks)

        if removed and self._started and self._scheduler:
            try:
                self._scheduler.remove_job(task_id)
            except Exception:
                pass

        return removed

    def list_tasks(self) -> List[Dict[str, Any]]:
        """Return a copy of all active scheduled tasks."""
        with self._lock:
            return list(self._tasks)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return next((t for t in self._tasks if t["task_id"] == task_id), None)

    def ensure_garbage_cleanup_job(self, chat_id: str, cron: str = "0 4 * * 1") -> Optional[str]:
        """Idempotently register the weekly garbage-cleanup cron job (Mon 04:00 UTC).

        Called on every scheduler start so a fresh state file always gets the
        job, but a persisted one is never duplicated. Returns the new task_id,
        or None if an active ``garbage_cleanup`` job already exists.
        """
        with self._lock:
            for t in self._tasks:
                if t.get("action") == "garbage_cleanup" and t.get("active"):
                    return None
        return self.add_task(action="garbage_cleanup", params={}, cron=cron, chat_id=chat_id)

    def run_garbage_cleanup(self, chat_id: str) -> None:
        """Run the weekly disk-garbage cleanup and send the report to ``chat_id``.

        DRY-RUN by default (``GARBAGE_CLEANUP_DRY_RUN`` env, default "true") per
        spec: real deletion is an explicit operator opt-in after the first
        week's reports are reviewed. Used by both the ``garbage_cleanup``
        scheduled action and the manual ``/garbage_cleanup`` admin command.
        """
        from app.services import garbage_cleanup as _gc
        from app.services.devtask import git_ops as _git_ops
        from app.services.devtask.queue import DevTaskQueue as _DevTaskQueue

        dry_run = os.getenv("GARBAGE_CLEANUP_DRY_RUN", "true").strip().lower() not in (
            "0", "false", "no",
        )
        queue = _DevTaskQueue()

        def _get_status(task_id: str) -> Optional[str]:
            item = queue.get(task_id)
            return item.get("status") if item else None

        try:
            report = _gc.run_cleanup(
                incoming_dir=Path("state") / "incoming_files",
                artifacts_dir=Path("artifacts"),
                logs_dir=Path("logs"),
                wt_root=Path(_git_ops.WT_ROOT),
                get_status=_get_status,
                now=datetime.now(timezone.utc),
                dry_run=dry_run,
                remove_worktree_fn=_git_ops.remove_worktree,
            )
            self._send(chat_id, _gc.format_report_message(report, dry_run))
        except Exception as exc:
            logger.error("garbage_cleanup failed: %s", exc)
            self._send(chat_id, f"❌ Ошибка уборки мусора: {exc}")

    def start(self) -> None:
        """Start the APScheduler background thread and restore persisted tasks."""
        if self._started:
            return
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            self._scheduler = BackgroundScheduler(timezone="UTC")
            self._scheduler.start()
            self._started = True
        except Exception as exc:
            logger.error("Failed to start APScheduler: %s", exc)
            return

        with self._lock:
            self._tasks = _load_tasks()

        for task in self._tasks:
            if task.get("active"):
                self._register_job(task)

        logger.info("JarvisScheduler started with %d tasks", len(self._tasks))

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self._scheduler and self._started:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
        self._started = False
        logger.info("JarvisScheduler stopped")

    # ── internals ───────────────────────────────────────────────────────────

    def _register_job(self, task: Dict[str, Any]) -> None:
        """Register an APScheduler job for the given task dict."""
        if not self._scheduler:
            return

        task_id = task["task_id"]
        schedule_type = task.get("schedule_type", "once")

        def job_fn():
            self._execute_task(task)

        try:
            if schedule_type == "once":
                run_at = datetime.fromisoformat(task["run_at"])
                self._scheduler.add_job(
                    job_fn,
                    trigger="date",
                    run_date=run_at,
                    id=task_id,
                    replace_existing=True,
                )
            elif schedule_type == "cron":
                parts = task["cron"].split()
                if len(parts) == 5:
                    minute, hour, dom, month, dow = parts
                else:
                    minute, hour = parts[0], parts[1]
                    dom, month, dow = "*", "*", "*"
                self._scheduler.add_job(
                    job_fn,
                    trigger="cron",
                    minute=minute,
                    hour=hour,
                    day=dom,
                    month=month,
                    day_of_week=dow,
                    id=task_id,
                    replace_existing=True,
                )
            elif schedule_type == "interval":
                self._scheduler.add_job(
                    job_fn,
                    trigger="interval",
                    seconds=task["interval_seconds"],
                    id=task_id,
                    replace_existing=True,
                )
        except Exception as exc:
            logger.warning("Failed to register job %s: %s", task_id, exc)

    def _execute_task(self, task: Dict[str, Any]) -> None:
        """Execute a scheduled task action."""
        action = task.get("action", "remind")
        params = task.get("params", {})
        chat_id = task.get("chat_id", "")

        logger.info("Executing scheduled task %s action=%s", task["task_id"], action)

        try:
            if action == "remind":
                text = params.get("text", "⏰ Напоминание!")
                self._send(chat_id, f"⏰ Напоминание: {text}")

            elif action == "research":
                query = params.get("query", "")
                self._send(chat_id, f"🔍 Запускаю исследование: {query}")
                try:
                    import requests
                    resp = requests.post(
                        "http://127.0.0.1:8010/api/jarvis/tools/internet/research",
                        json={"query": query},
                        timeout=120,
                    )
                    result = resp.json().get("answer", "Ответ не найден.")
                    self._send(chat_id, f"📊 Результат исследования:\n{result}")
                except Exception as exc:
                    self._send(chat_id, f"❌ Ошибка исследования: {exc}")

            elif action == "n8n_run":
                workflow_id = params.get("workflow_id", "")
                self._send(chat_id, f"🤖 Запускаю n8n workflow: {workflow_id}")
                try:
                    from app.services.n8n_integration import trigger_workflow
                    result = trigger_workflow(workflow_id, params.get("payload", {}))
                    self._send(chat_id, f"✅ Workflow запущен. Execution: {result.get('execution_id', '?')}")
                except Exception as exc:
                    self._send(chat_id, f"❌ Ошибка n8n: {exc}")

            elif action == "morning_brief":
                self._send(chat_id, self._build_morning_brief(params))

            elif action == "garbage_cleanup":
                self.run_garbage_cleanup(chat_id)

            else:
                logger.warning("Unknown action: %s", action)

        except Exception as exc:
            logger.error("Task execution failed %s: %s", task["task_id"], exc)

        # Remove one-shot tasks after execution
        if task.get("schedule_type") == "once":
            self.remove_task(task["task_id"])

    def _send(self, chat_id: str, text: str) -> None:
        if self._send_fn and chat_id:
            try:
                self._send_fn(chat_id, text)
            except Exception as exc:
                logger.warning("send_fn failed: %s", exc)
        else:
            logger.info("[NO_SEND] chat=%s text=%s", chat_id, text[:80])

    def _build_morning_brief(self, params: Dict[str, Any]) -> str:
        tasks = self.list_tasks()
        active_count = sum(1 for t in tasks if t.get("active"))
        remind_tasks = [t for t in tasks if t.get("action") == "remind" and t.get("active")]

        lines = [
            "🌅 Доброе утро, Daniil!",
            "",
            f"📋 Активных задач по расписанию: {active_count}",
        ]
        if remind_tasks:
            lines.append("\n⏰ Ближайшие напоминания:")
            for t in remind_tasks[:3]:
                run_at = t.get("run_at", "")
                text = t.get("params", {}).get("text", "")
                lines.append(f"  • {run_at[:16]} — {text}")

        lines.append("\nЧем могу помочь сегодня? 🚀")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Natural-language reminder parser
# ---------------------------------------------------------------------------

import re as _re


def _clean_text(text: str) -> str:
    """Remove surrounding quotes and trim."""
    return text.strip().strip('"').strip("'").strip()


def parse_remind_text(query: str) -> Optional[Dict[str, Any]]:
    """Parse natural language reminder. Russian-first, English fallback.

    Examples:
      "через 10 минут позвонить маме"
      "через 1 час встреча"
      "завтра в 9:00 проверить почту"
      "каждый день 7:00 утренний бриф"
      "in 10 minutes test"
      "tomorrow at 14:30 meeting"
    """
    if not query or not query.strip():
        return None

    q = query.strip()
    now = datetime.now(timezone.utc)

    # ── Russian patterns ──────────────────────────────────────────────────────

    # "через X минут <text>"
    m = _re.match(r'^через\s+(\d+)\s+минут\w*\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "once",
            "datetime": now + timedelta(minutes=int(m.group(1))),
            "cron": None,
            "text": _clean_text(m.group(2)),
        }

    # "через X (часов|часа|час|ч) <text>"
    m = _re.match(r'^через\s+(\d+)\s+(?:час\w*|ч)\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "once",
            "datetime": now + timedelta(hours=int(m.group(1))),
            "cron": None,
            "text": _clean_text(m.group(2)),
        }

    # "через X (дней|дня|день) <text>"
    m = _re.match(r'^через\s+(\d+)\s+(?:дн[ея]|день|дней)\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "once",
            "datetime": now + timedelta(days=int(m.group(1))),
            "cron": None,
            "text": _clean_text(m.group(2)),
        }

    # "завтра (в) HH:MM <text>"
    m = _re.match(r'^завтра\s+(?:в\s+)?(\d{1,2}):(\d{2})\s+(.+)$', q, _re.IGNORECASE)
    if m:
        tomorrow = now + timedelta(days=1)
        run_at = tomorrow.replace(
            hour=int(m.group(1)), minute=int(m.group(2)),
            second=0, microsecond=0,
        )
        return {
            "schedule_type": "once",
            "datetime": run_at,
            "cron": None,
            "text": _clean_text(m.group(3)),
        }

    # "сегодня (в) HH:MM <text>"
    m = _re.match(r'^сегодня\s+(?:в\s+)?(\d{1,2}):(\d{2})\s+(.+)$', q, _re.IGNORECASE)
    if m:
        run_at = now.replace(
            hour=int(m.group(1)), minute=int(m.group(2)),
            second=0, microsecond=0,
        )
        if run_at < now:
            run_at += timedelta(days=1)
        return {
            "schedule_type": "once",
            "datetime": run_at,
            "cron": None,
            "text": _clean_text(m.group(3)),
        }

    # "каждый день (в) HH:MM <text>"
    m = _re.match(r'^каждый\s+день\s+(?:в\s+)?(\d{1,2}):(\d{2})\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "daily",
            "datetime": None,
            "cron": f"{int(m.group(2))} {int(m.group(1))} * * *",
            "text": _clean_text(m.group(3)),
        }

    # "ежедневно (в) HH:MM <text>"
    m = _re.match(r'^ежедневно\s+(?:в\s+)?(\d{1,2}):(\d{2})\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "daily",
            "datetime": None,
            "cron": f"{int(m.group(2))} {int(m.group(1))} * * *",
            "text": _clean_text(m.group(3)),
        }

    # ── English patterns ──────────────────────────────────────────────────────

    # "in X minutes <text>"
    m = _re.match(r'^in\s+(\d+)\s+minutes?\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "once",
            "datetime": now + timedelta(minutes=int(m.group(1))),
            "cron": None,
            "text": _clean_text(m.group(2)),
        }

    # "in X hours <text>"
    m = _re.match(r'^in\s+(\d+)\s+hours?\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "once",
            "datetime": now + timedelta(hours=int(m.group(1))),
            "cron": None,
            "text": _clean_text(m.group(2)),
        }

    # "in X days <text>"
    m = _re.match(r'^in\s+(\d+)\s+days?\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "once",
            "datetime": now + timedelta(days=int(m.group(1))),
            "cron": None,
            "text": _clean_text(m.group(2)),
        }

    # "tomorrow (at) HH:MM <text>"
    m = _re.match(r'^tomorrow\s+(?:at\s+)?(\d{1,2}):(\d{2})\s+(.+)$', q, _re.IGNORECASE)
    if m:
        tomorrow = now + timedelta(days=1)
        run_at = tomorrow.replace(
            hour=int(m.group(1)), minute=int(m.group(2)),
            second=0, microsecond=0,
        )
        return {
            "schedule_type": "once",
            "datetime": run_at,
            "cron": None,
            "text": _clean_text(m.group(3)),
        }

    # "every day (at) HH:MM <text>"
    m = _re.match(r'^every\s+day\s+(?:at\s+)?(\d{1,2}):(\d{2})\s+(.+)$', q, _re.IGNORECASE)
    if m:
        return {
            "schedule_type": "daily",
            "datetime": None,
            "cron": f"{int(m.group(2))} {int(m.group(1))} * * *",
            "text": _clean_text(m.group(3)),
        }

    # ── Last resort: dateparser fallback ─────────────────────────────────────
    try:
        import dateparser
        words = q.split()
        for split_idx in range(len(words) - 1, 0, -1):
            time_part = ' '.join(words[:split_idx])
            text_part = ' '.join(words[split_idx:])
            dt = dateparser.parse(time_part, languages=['ru', 'en'], settings={
                'PREFER_DATES_FROM': 'future',
                'TIMEZONE': 'UTC',
                'RETURN_AS_TIMEZONE_AWARE': True,
            })
            if dt and text_part.strip():
                return {
                    "schedule_type": "once",
                    "datetime": dt,
                    "cron": None,
                    "text": _clean_text(text_part),
                }
    except Exception:
        pass

    return None


def format_task_list(tasks: List[Dict[str, Any]]) -> str:
    """Format task list for Telegram display."""
    if not tasks:
        return "📭 Нет активных задач по расписанию.\n/remind — создать напоминание"

    lines = [f"📋 Задачи по расписанию ({len(tasks)}):"]
    for t in tasks:
        action = t.get("action", "?")
        task_id = t["task_id"][:8]
        schedule_type = t.get("schedule_type", "?")
        icon = {"remind": "⏰", "research": "🔍", "n8n_run": "🤖", "morning_brief": "🌅"}.get(action, "📌")

        if schedule_type == "once":
            when = t.get("run_at", "?")[:16]
        elif schedule_type == "cron":
            when = f"cron: {t.get('cron', '?')}"
        else:
            when = f"каждые {t.get('interval_seconds', '?')}с"

        text_preview = t.get("params", {}).get("text", t.get("params", {}).get("query", ""))[:40]
        lines.append(f"{icon} [{task_id}] {action} | {when} | {text_preview}")

    lines.append("\n/schedule remove <id> — отменить")
    return "\n".join(lines)
