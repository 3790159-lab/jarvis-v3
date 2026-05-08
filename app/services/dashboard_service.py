from pathlib import Path
from typing import Any, Dict, List

from app.services.active_run_store import active_run_stats, list_active_runs
from app.services.llm_router import get_stats as get_llm_stats, ollama_health
from app.services.mission_memory import memory_stats
from app.services.mission_store import get_mission, list_missions, mission_stats
from app.services.policy import get_policy
from app.services.task_queue import list_queue, mission_queue_items, queue_stats
from app.services.tool_registry import get_tool_registry
from app.services.worker_loop import get_worker_state
from app.services.worker_registry import list_workers, registry_stats


BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "artifacts" / "output"
LOGS_DIR = BASE_DIR / "artifacts" / "logs"


def _safe_list_files(folder: Path, limit: int = 50) -> List[Dict[str, Any]]:
    folder.mkdir(parents=True, exist_ok=True)
    items: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            stat = path.stat()
            items.append({
                "name": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "modified_at": int(stat.st_mtime),
            })
        except Exception:
            continue
    return items


def dashboard_snapshot(limit_missions: int = 20, limit_queue: int = 50) -> Dict[str, Any]:
    return {
        "policy": get_policy(),
        "ollama": ollama_health(),
        "llm_stats": get_llm_stats(),
        "mission_stats": mission_stats(),
        "queue_stats": queue_stats(),
        "memory_stats": memory_stats(),
        "worker_state": get_worker_state(),
        "worker_registry": list_workers(),
        "worker_registry_stats": registry_stats(),
        "active_run_stats": active_run_stats(),
        "active_runs": list_active_runs(),
        "missions": list_missions(limit=limit_missions),
        "queue_items": list_queue(limit=limit_queue),
        "tools": get_tool_registry(),
        "output_files": _safe_list_files(OUTPUT_DIR, limit=50),
        "log_files": _safe_list_files(LOGS_DIR, limit=50),
    }


def mission_detail_snapshot(mission_id: str) -> Dict[str, Any]:
    return {
        "mission": get_mission(mission_id),
        "queue_items": mission_queue_items(mission_id),
    }