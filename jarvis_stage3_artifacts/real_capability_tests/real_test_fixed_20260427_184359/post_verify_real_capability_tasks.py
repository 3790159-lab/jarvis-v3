import json
from pathlib import Path

q = json.loads(Path("state/jarvis_brain/action_queue_v6_4.json").read_text(encoding="utf-8"))
items = q.get("items", [])

recent = [
    x for x in items
    if isinstance(x, dict)
    and str(x.get("id", "")).startswith("real_capability_v1_1_")
]

print(json.dumps({
    "recent_real_capability_tasks": len(recent),
    "statuses": [
        {
            "id": x.get("id"),
            "status": x.get("status"),
            "history_count": len(x.get("execution_history", [])),
            "updated_at": x.get("updated_at")
        }
        for x in recent
    ]
}, ensure_ascii=False, indent=2))