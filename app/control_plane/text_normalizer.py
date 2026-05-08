from __future__ import annotations

import os
import uuid


NORMALIZED_MARKER = "normalized_v17_1"


def _clean_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_string_list(value) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []

    if not isinstance(value, list):
        s = _clean_str(value)
        return [s] if s else []

    cleaned = []
    for item in value:
        if item is None:
            continue
        cleaned.append(str(item))

    if not cleaned:
        return []

    if all(len(x) <= 1 for x in cleaned if isinstance(x, str)):
        joined = "".join(cleaned).strip()
        return [joined] if joined else []

    out = []
    seen = set()
    for item in cleaned:
        s = item.strip()
        if not s:
            continue
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def _ensure_artifact_metadata_dict(artifact: dict, agent_id: str, task_id: str):
    metadata = dict(artifact.get("metadata") or {})
    name = str(artifact.get("name") or "")
    path = str(artifact.get("path") or "")
    kind = str(artifact.get("kind") or "artifact")
    filename = os.path.basename(path or name or f"{task_id}_{kind}")
    ext = filename.rsplit(".", 1)[1] if "." in filename else ""

    metadata["artifact_id"] = metadata.get("artifact_id") or f"artifact_{uuid.uuid4().hex[:10]}"
    metadata["artifact_type"] = metadata.get("artifact_type") or kind
    metadata["filename"] = metadata.get("filename") or filename
    metadata["extension"] = metadata.get("extension") or ext
    metadata["producer_agent"] = metadata.get("producer_agent") or agent_id
    metadata["source_task_id"] = metadata.get("source_task_id") or task_id
    metadata["validation_state"] = metadata.get("validation_state") or "unknown"

    artifact["metadata"] = metadata
    return artifact


def is_task_entry_normalized(task_entry: dict) -> bool:
    result = dict(task_entry.get("result") or {})
    dynamic_fields = dict(result.get("dynamic_fields") or {})
    return bool(dynamic_fields.get(NORMALIZED_MARKER, False))


def normalize_result_dict(task_entry: dict) -> tuple[dict, bool]:
    result = dict(task_entry.get("result") or {})
    if not result:
        return task_entry, False

    if is_task_entry_normalized(task_entry):
        return task_entry, False

    changed = False

    new_risk_notes = normalize_string_list(result.get("risk_notes"))
    new_validation_hints = normalize_string_list(result.get("validation_hints"))
    new_next_actions = normalize_string_list(result.get("next_actions"))
    new_handoff_notes = normalize_string_list(result.get("handoff_notes"))

    if result.get("risk_notes") != new_risk_notes:
        changed = True
    if result.get("validation_hints") != new_validation_hints:
        changed = True
    if result.get("next_actions") != new_next_actions:
        changed = True
    if result.get("handoff_notes") != new_handoff_notes:
        changed = True

    result["risk_notes"] = new_risk_notes
    result["validation_hints"] = new_validation_hints
    result["next_actions"] = new_next_actions
    result["handoff_notes"] = new_handoff_notes

    agent_id = str(result.get("agent_id") or task_entry.get("assigned_agent_id") or "unknown_agent")
    task_id = str(((task_entry.get("task") or {}).get("task_id")) or "unknown_task")

    artifacts = []
    for item in list(result.get("artifacts") or []):
        if isinstance(item, dict):
            artifacts.append(_ensure_artifact_metadata_dict(item, agent_id=agent_id, task_id=task_id))
    if result.get("artifacts") != artifacts:
        changed = True
    result["artifacts"] = artifacts

    dynamic_fields = dict(result.get("dynamic_fields") or {})
    dynamic_fields[NORMALIZED_MARKER] = True
    result["dynamic_fields"] = dynamic_fields

    task_entry["result"] = result
    return task_entry, changed


def normalize_snapshot_dict(snapshot: dict) -> tuple[dict, dict]:
    snapshot = dict(snapshot or {})
    fixed_tasks = 0
    fixed_fields = 0
    fixed_artifacts = 0

    tasks = []
    for task_entry in list(snapshot.get("tasks") or []):
        task_entry, changed = normalize_result_dict(task_entry)
        if changed:
            fixed_tasks += 1
            result = task_entry.get("result") or {}
            fixed_fields += len(result.get("risk_notes") or [])
            fixed_fields += len(result.get("validation_hints") or [])
            fixed_artifacts += len(result.get("artifacts") or [])
        tasks.append(task_entry)

    snapshot["tasks"] = tasks
    snapshot["_normalized_snapshot_v17_1"] = True
    return snapshot, {
        "fixed_tasks": fixed_tasks,
        "fixed_fields": fixed_fields,
        "fixed_artifacts": fixed_artifacts,
    }