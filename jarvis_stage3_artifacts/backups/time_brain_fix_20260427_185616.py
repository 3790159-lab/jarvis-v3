from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


DEFAULT_TZ = "Europe/Kyiv"

SCHEDULE_PATH = Path("state/jarvis_brain/scheduled_tasks_v1.json")
QUEUE_PATH = Path("state/jarvis_brain/action_queue_v6_4.json")
ARTIFACT_ROOT = Path("jarvis_stage3_artifacts/time_brain")
EVENT_LOG = ARTIFACT_ROOT / "time_events.jsonl"


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local(tz: str = DEFAULT_TZ) -> datetime:
    return now_utc().astimezone(ZoneInfo(tz))


def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse_dt(value: str, tz: str = DEFAULT_TZ) -> datetime:
    """
    Accepts:
    - 2026-04-27T21:30:00+03:00
    - 2026-04-27T21:30:00
    - 2026-04-27 21:30
    """
    value = value.strip().replace(" ", "T")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(UTC)


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def log_event(event: Dict[str, Any]) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    event.setdefault("created_at", iso(now_utc()))
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def get_time_context(tz: str = DEFAULT_TZ) -> Dict[str, Any]:
    local = now_local(tz)
    utc = now_utc()
    return {
        "schema": "jarvis.time_context.v1",
        "timezone": tz,
        "utc": iso(utc),
        "local": iso(local),
        "date": local.date().isoformat(),
        "time": local.time().replace(microsecond=0).isoformat(),
        "weekday": local.strftime("%A"),
        "is_weekend": local.weekday() >= 5,
        "hour": local.hour,
        "day_part": (
            "night" if local.hour < 6 else
            "morning" if local.hour < 12 else
            "afternoon" if local.hour < 18 else
            "evening"
        ),
    }


@dataclass
class ScheduledTask:
    id: str
    title: str
    due_at_utc: str
    timezone: str
    lane: str = "scheduled"
    priority: int = 50
    risk: str = "low"
    status: str = "scheduled"
    details: str = ""
    gateway_plan: List[Dict[str, Any]] | None = None
    recurrence: Optional[Dict[str, Any]] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_schedule() -> Dict[str, Any]:
    data = read_json(SCHEDULE_PATH, None)
    if not isinstance(data, dict):
        data = {
            "schema": "jarvis.scheduled_tasks.v1",
            "created_at": iso(now_utc()),
            "timezone": DEFAULT_TZ,
            "items": [],
        }
    if "items" not in data or not isinstance(data["items"], list):
        data["items"] = []
    return data


def save_schedule(data: Dict[str, Any]) -> None:
    data["updated_at"] = iso(now_utc())
    write_json(SCHEDULE_PATH, data)


def load_queue() -> Dict[str, Any]:
    data = read_json(QUEUE_PATH, None)
    if not isinstance(data, dict):
        data = {
            "schema": "jarvis.action_queue.v6_4",
            "created_at": iso(now_utc()),
            "items": [],
        }
    if "items" not in data or not isinstance(data["items"], list):
        data["items"] = []
    return data


def save_queue(data: Dict[str, Any]) -> None:
    data["updated_at"] = iso(now_utc())
    write_json(QUEUE_PATH, data)


def add_scheduled_task(
    task_id: str,
    title: str,
    due_at: str,
    timezone: str = DEFAULT_TZ,
    lane: str = "scheduled",
    priority: int = 50,
    risk: str = "low",
    details: str = "",
    gateway_plan: Optional[List[Dict[str, Any]]] = None,
    recurrence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    schedule = load_schedule()

    due_utc = parse_dt(due_at, timezone)

    item = ScheduledTask(
        id=task_id,
        title=title,
        due_at_utc=iso(due_utc),
        timezone=timezone,
        lane=lane,
        priority=priority,
        risk=risk,
        status="scheduled",
        details=details,
        gateway_plan=gateway_plan or [
            {
                "tool": "md_report",
                "args": {
                    "path": f"jarvis_stage3_artifacts/time_brain/scheduled/{task_id}.md",
                    "title": title,
                    "body": details or f"Scheduled task executed: {title}",
                },
            }
        ],
        recurrence=recurrence,
        created_at=iso(now_utc()),
        updated_at=iso(now_utc()),
    ).to_dict()

    schedule["items"] = [x for x in schedule["items"] if x.get("id") != task_id]
    schedule["items"].append(item)
    save_schedule(schedule)

    log_event({"event": "scheduled_task_added", "task": item})
    return item


def list_scheduled_tasks(status: Optional[str] = None) -> Dict[str, Any]:
    schedule = load_schedule()
    items = schedule["items"]
    if status:
        items = [x for x in items if x.get("status") == status]
    return {
        "schema": "jarvis.scheduled_tasks.list.v1",
        "time": get_time_context(),
        "count": len(items),
        "items": items,
    }


def find_due_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    schedule = load_schedule()
    now = now_utc()
    due = []

    for item in schedule["items"]:
        if item.get("status") != "scheduled":
            continue
        try:
            due_at = datetime.fromisoformat(item["due_at_utc"])
        except Exception:
            continue
        if due_at <= now:
            due.append(item)

    due.sort(key=lambda x: x.get("priority", 50))
    return due[:limit]


def _next_recurrence_due(item: Dict[str, Any]) -> Optional[str]:
    recurrence = item.get("recurrence")
    if not isinstance(recurrence, dict):
        return None

    freq = recurrence.get("freq")
    interval = int(recurrence.get("interval", 1))

    try:
        current_due = datetime.fromisoformat(item["due_at_utc"])
    except Exception:
        current_due = now_utc()

    if freq == "daily":
        return iso(current_due + timedelta(days=interval))
    if freq == "hourly":
        return iso(current_due + timedelta(hours=interval))
    if freq == "weekly":
        return iso(current_due + timedelta(weeks=interval))

    return None


def dispatch_due_tasks(limit: int = 20) -> Dict[str, Any]:
    schedule = load_schedule()
    queue = load_queue()
    due = find_due_tasks(limit=limit)

    dispatched = []
    queue_ids = {x.get("id") for x in queue["items"] if isinstance(x, dict)}

    for item in due:
        queue_task_id = f"scheduled_{item['id']}"

        if queue_task_id not in queue_ids:
            queue["items"].append({
                "id": queue_task_id,
                "title": item["title"],
                "lane": item.get("lane", "scheduled"),
                "priority": item.get("priority", 50),
                "risk": item.get("risk", "low"),
                "gateway_plan": item.get("gateway_plan") or [],
                "details": item.get("details", ""),
                "status": "pending",
                "created_at": iso(now_utc()),
                "executor": "gateway_plan_v7_2",
                "evidence_required": True,
                "completion_gate": [
                    "evidence_report_exists",
                    "backend_health_checked",
                    "result_artifact_written",
                ],
                "source": "time_brain_v1",
                "scheduled_source_id": item["id"],
                "due_at_utc": item["due_at_utc"],
            })
            dispatched.append(queue_task_id)

        next_due = _next_recurrence_due(item)
        if next_due:
            item["due_at_utc"] = next_due
            item["updated_at"] = iso(now_utc())
            item["last_dispatched_at"] = iso(now_utc())
            item["status"] = "scheduled"
        else:
            item["status"] = "dispatched"
            item["updated_at"] = iso(now_utc())
            item["dispatched_at"] = iso(now_utc())

    save_queue(queue)
    save_schedule(schedule)

    result = {
        "schema": "jarvis.time_brain.dispatch.v1",
        "created_at": iso(now_utc()),
        "due_count": len(due),
        "dispatched_count": len(dispatched),
        "dispatched": dispatched,
        "queue_path": str(QUEUE_PATH),
        "schedule_path": str(SCHEDULE_PATH),
    }

    log_event({"event": "dispatch_due_tasks", "result": result})
    return result