from typing import Any, Dict

from app.services.mission_memory import build_resume_context, compress_memory, get_memory


def ensure_context_compressed(
    mission_id: str,
    keep_recent_events: int = 20,
    batch_size: int = 25
) -> Dict[str, Any]:
    return compress_memory(
        mission_id=mission_id,
        keep_recent_events=keep_recent_events,
        batch_size=batch_size,
    )


def get_resume_bundle(mission_id: str) -> Dict[str, Any]:
    memory = get_memory(mission_id)
    resume = build_resume_context(mission_id)
    return {
        "mission_id": mission_id,
        "memory_meta": memory.get("meta", {}),
        "resume_context": resume,
    }