from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any


def _now_ts() -> float:
    return time.time()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        broken_path = path.with_suffix(path.suffix + ".broken")
        broken_path.write_text(raw, encoding="utf-8")
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_tasks_payload(data: Any) -> tuple[dict[str, dict[str, Any]], str, dict[str, Any] | None]:
    if isinstance(data, list):
        tasks_map = {}
        for item in data:
            if isinstance(item, dict) and item.get("task_id"):
                tasks_map[str(item["task_id"])] = dict(item)
        return tasks_map, "list", None

    if isinstance(data, dict) and "tasks" in data:
        container = dict(data)
        tasks_raw = container.get("tasks", [])
        if isinstance(tasks_raw, list):
            tasks_map = {}
            for item in tasks_raw:
                if isinstance(item, dict) and item.get("task_id"):
                    tasks_map[str(item["task_id"])] = dict(item)
            return tasks_map, "container_list", container
        if isinstance(tasks_raw, dict):
            tasks_map = {}
            for task_id, item in tasks_raw.items():
                if isinstance(item, dict):
                    fixed = dict(item)
                    fixed.setdefault("task_id", str(task_id))
                    tasks_map[str(fixed["task_id"])] = fixed
            return tasks_map, "container_map", container

    if isinstance(data, dict):
        maybe_map = {}
        good = False
        for task_id, item in data.items():
            if isinstance(item, dict) and (item.get("task_id") or str(task_id).startswith("task_")):
                fixed = dict(item)
                fixed.setdefault("task_id", str(task_id))
                maybe_map[str(fixed["task_id"])] = fixed
                good = True
        if good:
            return maybe_map, "map", None

    return {}, "list", None


def _serialize_tasks(tasks_map: dict[str, dict[str, Any]], fmt: str, container: dict[str, Any] | None) -> Any:
    ordered_items = list(tasks_map.values())

    if fmt == "list":
        return ordered_items
    if fmt == "container_list":
        result = dict(container or {})
        result["tasks"] = ordered_items
        return result
    if fmt == "container_map":
        result = dict(container or {})
        result["tasks"] = {task["task_id"]: task for task in ordered_items}
        return result
    if fmt == "map":
        return {task["task_id"]: task for task in ordered_items}
    return ordered_items


def _normalize_queue_from_file(tasks_doc: Any, tasks_map: dict[str, dict[str, Any]]) -> tuple[list[str], bool]:
    if isinstance(tasks_doc, dict) and "queue" in tasks_doc and isinstance(tasks_doc["queue"], list):
        queue_ids = [str(x) for x in tasks_doc["queue"] if isinstance(x, (str, int))]
        return queue_ids, True

    derived = []
    for task_id, task in tasks_map.items():
        status = str(task.get("status") or "").lower()
        if status in {"queued", "retrying", "running"}:
            derived.append(task_id)
    return derived, False


def _read_agents(agent_file: Path) -> set[str]:
    data = _read_json(agent_file, {})
    names: set[str] = set()

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("enabled", True):
                name = item.get("name") or item.get("agent_name") or item.get("agent_id")
                if name:
                    names.add(str(name))
    elif isinstance(data, dict):
        if "agents" in data and isinstance(data["agents"], list):
            for item in data["agents"]:
                if isinstance(item, dict) and item.get("enabled", True):
                    name = item.get("name") or item.get("agent_name") or item.get("agent_id")
                    if name:
                        names.add(str(name))
    return names


def _repair_missions(mission_file: Path) -> dict[str, Any]:
    missions_doc = _read_json(mission_file, {})
    changed = 0

    if isinstance(missions_doc, dict) and "missions" in missions_doc and isinstance(missions_doc["missions"], list):
        for item in missions_doc["missions"]:
            if isinstance(item, dict) and str(item.get("status", "")).lower() == "running":
                item["status"] = "created"
                item["updated_at"] = _now_ts()
                changed += 1
        _write_json(mission_file, missions_doc)
        return {"missions_changed": changed, "format": "container_list"}

    if isinstance(missions_doc, list):
        for item in missions_doc:
            if isinstance(item, dict) and str(item.get("status", "")).lower() == "running":
                item["status"] = "created"
                item["updated_at"] = _now_ts()
                changed += 1
        _write_json(mission_file, missions_doc)
        return {"missions_changed": changed, "format": "list"}

    if isinstance(missions_doc, dict):
        for _, item in missions_doc.items():
            if isinstance(item, dict) and str(item.get("status", "")).lower() == "running":
                item["status"] = "created"
                item["updated_at"] = _now_ts()
                changed += 1
        _write_json(mission_file, missions_doc)
        return {"missions_changed": changed, "format": "map"}

    _write_json(mission_file, {"missions": []})
    return {"missions_changed": 0, "format": "initialized"}


def repair_runtime_state(state_dir: str | Path) -> dict[str, Any]:
    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)

    tasks_file = state_path / "tasks.json"
    missions_file = state_path / "missions.json"
    agents_file = state_path / "agents.json"
    report_file = state_path / "repair_report.json"

    if not tasks_file.exists():
        _write_json(tasks_file, [])
    if not missions_file.exists():
        _write_json(missions_file, {"missions": []})
    if not agents_file.exists():
        _write_json(agents_file, {"agents": []})

    tasks_doc = _read_json(tasks_file, [])
    tasks_map, fmt, container = _normalize_tasks_payload(tasks_doc)
    queue_ids, queue_was_present = _normalize_queue_from_file(tasks_doc, tasks_map)
    enabled_agents = _read_agents(agents_file)

    removed_orphans = []
    deduped_queue = []
    seen = set()

    for task_id in queue_ids:
        if task_id not in tasks_map:
            removed_orphans.append(task_id)
            continue
        if task_id in seen:
            continue
        seen.add(task_id)
        deduped_queue.append(task_id)

    reset_running = []
    reset_retrying = []
    failed_missing_agent = []

    for task_id, task in tasks_map.items():
        status = str(task.get("status") or "").lower()
        assigned_agent = task.get("assigned_agent")

        if assigned_agent and enabled_agents and str(assigned_agent) not in enabled_agents:
            if status in {"queued", "retrying", "running"}:
                task["status"] = "failed"
                task["error"] = f"Assigned agent is not enabled or missing: {assigned_agent}"
                task["updated_at"] = _now_ts()
                failed_missing_agent.append(task_id)
                continue

        if status == "running":
            task["status"] = "queued"
            task["updated_at"] = _now_ts()
            reset_running.append(task_id)
            if task_id not in deduped_queue:
                deduped_queue.append(task_id)

        if status == "retrying":
            task["status"] = "queued"
            task["updated_at"] = _now_ts()
            reset_retrying.append(task_id)
            if task_id not in deduped_queue:
                deduped_queue.append(task_id)

    repaired_doc = _serialize_tasks(tasks_map, fmt, container)

    if isinstance(repaired_doc, dict) and queue_was_present:
        repaired_doc["queue"] = deduped_queue

    _write_json(tasks_file, repaired_doc)
    mission_info = _repair_missions(missions_file)

    report = {
        "status": "ok",
        "state_dir": str(state_path.resolve()),
        "task_count": len(tasks_map),
        "queue_count_after": len(deduped_queue),
        "removed_orphan_queue_ids": removed_orphans,
        "reset_running_to_queued": reset_running,
        "reset_retrying_to_queued": reset_retrying,
        "failed_missing_agent": failed_missing_agent,
        "enabled_agents": sorted(enabled_agents),
        "queue_was_present": queue_was_present,
        "task_storage_format": fmt,
        "mission_repair": mission_info,
        "repaired_at": _now_ts(),
    }
    _write_json(report_file, report)
    return report


def compute_retry_delay(attempt: int, base_seconds: float = 2.0, max_seconds: float = 60.0) -> float:
    attempt = max(1, int(attempt))
    delay = base_seconds * (2 ** (attempt - 1))
    return min(max_seconds, delay)


if __name__ == "__main__":
    target_state_dir = sys.argv[1] if len(sys.argv) > 1 else "state"
    result = repair_runtime_state(target_state_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
