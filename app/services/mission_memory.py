import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, List


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATE_DIR = os.path.join(BASE_DIR, "state")
MEMORY_FILE = os.path.join(STATE_DIR, "mission_memory.json")

_lock = threading.Lock()


def _safe_read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _safe_write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="memory_", suffix=".tmp", dir=os.path.dirname(path))
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _load() -> Dict[str, Any]:
    data = _safe_read_json(MEMORY_FILE, {"missions": {}})
    if not isinstance(data, dict):
        return {"missions": {}}
    missions = data.get("missions", {})
    if not isinstance(missions, dict):
        missions = {}
    return {"missions": missions}


def _save(data: Dict[str, Any]) -> None:
    _safe_write_json(MEMORY_FILE, data)


def _ensure_bucket(data: Dict[str, Any], mission_id: str) -> Dict[str, Any]:
    missions = data.setdefault("missions", {})
    bucket = missions.setdefault(mission_id, {
        "events": [],
        "summaries": [],
        "meta": {
            "created_at": int(time.time()),
            "updated_at": int(time.time()),
            "compressions": 0,
        }
    })
    bucket.setdefault("events", [])
    bucket.setdefault("summaries", [])
    bucket.setdefault("meta", {})
    bucket["meta"].setdefault("created_at", int(time.time()))
    bucket["meta"]["updated_at"] = int(time.time())
    bucket["meta"].setdefault("compressions", 0)
    return bucket


def append_memory(mission_id: str, kind: str, content: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        data = _load()
        bucket = _ensure_bucket(data, mission_id)
        bucket["events"].append({
            "ts": int(time.time()),
            "kind": kind,
            "content": content,
        })
        bucket["meta"]["updated_at"] = int(time.time())
        _save(data)
        return bucket


def get_memory(mission_id: str) -> Dict[str, Any]:
    with _lock:
        data = _load()
        missions = data.get("missions", {})
        return missions.get(mission_id, {
            "events": [],
            "summaries": [],
            "meta": {
                "created_at": 0,
                "updated_at": 0,
                "compressions": 0,
            }
        })


def append_summary(mission_id: str, summary_text: str, source_event_count: int) -> Dict[str, Any]:
    with _lock:
        data = _load()
        bucket = _ensure_bucket(data, mission_id)
        bucket["summaries"].append({
            "ts": int(time.time()),
            "summary": str(summary_text or "").strip(),
            "source_event_count": int(source_event_count),
        })
        bucket["meta"]["updated_at"] = int(time.time())
        _save(data)
        return bucket


def _render_event_line(event: Dict[str, Any]) -> str:
    kind = str(event.get("kind", "event"))
    content = event.get("content", {})
    if isinstance(content, dict):
        pieces = []
        for k, v in list(content.items())[:5]:
            pieces.append(f"{k}={v}")
        body = ", ".join(pieces)
    else:
        body = str(content)
    return f"{kind}: {body}"


def compress_memory(mission_id: str, keep_recent_events: int = 20, batch_size: int = 25) -> Dict[str, Any]:
    with _lock:
        data = _load()
        bucket = _ensure_bucket(data, mission_id)
        events: List[Dict[str, Any]] = list(bucket.get("events", []))

        if len(events) <= keep_recent_events:
            return {
                "compressed": False,
                "reason": "not_enough_events",
                "events_total": len(events),
                "summaries_total": len(bucket.get("summaries", [])),
            }

        compressible_count = len(events) - keep_recent_events
        if compressible_count < batch_size:
            return {
                "compressed": False,
                "reason": "batch_threshold_not_reached",
                "events_total": len(events),
                "summaries_total": len(bucket.get("summaries", [])),
            }

        selected = events[:compressible_count]
        selected = selected[:batch_size]

        lines = [_render_event_line(e) for e in selected]
        summary_text = " | ".join(lines)
        if len(summary_text) > 4000:
            summary_text = summary_text[:4000] + " ...[truncated]"

        bucket["summaries"].append({
            "ts": int(time.time()),
            "summary": summary_text,
            "source_event_count": len(selected),
        })
        bucket["events"] = events[len(selected):]
        bucket["meta"]["updated_at"] = int(time.time())
        bucket["meta"]["compressions"] = int(bucket["meta"].get("compressions", 0)) + 1

        _save(data)

        return {
            "compressed": True,
            "compressed_events": len(selected),
            "remaining_events": len(bucket["events"]),
            "summaries_total": len(bucket["summaries"]),
        }


def build_resume_context(mission_id: str, max_summaries: int = 5, max_events: int = 10) -> Dict[str, Any]:
    bucket = get_memory(mission_id)
    summaries = bucket.get("summaries", [])[-max_summaries:]
    events = bucket.get("events", [])[-max_events:]

    summary_lines = [str(s.get("summary", "")) for s in summaries]
    event_lines = [_render_event_line(e) for e in events]

    text_parts: List[str] = []
    if summary_lines:
        text_parts.append("Past compressed context:")
        text_parts.extend(f"- {line}" for line in summary_lines if line)
    if event_lines:
        text_parts.append("Recent events:")
        text_parts.extend(f"- {line}" for line in event_lines if line)

    resume_text = "\n".join(text_parts).strip()
    return {
        "mission_id": mission_id,
        "resume_text": resume_text,
        "recent_events_count": len(events),
        "summary_blocks_count": len(summaries),
        "meta": bucket.get("meta", {}),
    }


def memory_stats() -> Dict[str, Any]:
    with _lock:
        data = _load()
        missions = data.get("missions", {})
        total_events = 0
        total_summaries = 0
        total_compressions = 0

        for bucket in missions.values():
            if not isinstance(bucket, dict):
                continue
            total_events += len(bucket.get("events", []))
            total_summaries += len(bucket.get("summaries", []))
            total_compressions += int(bucket.get("meta", {}).get("compressions", 0))

        return {
            "missions_with_memory": len(missions),
            "memory_events_total": total_events,
            "summary_blocks_total": total_summaries,
            "compressions_total": total_compressions,
            "memory_file": MEMORY_FILE,
        }