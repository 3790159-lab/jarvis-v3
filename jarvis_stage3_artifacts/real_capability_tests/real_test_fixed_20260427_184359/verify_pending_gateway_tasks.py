import json
from pathlib import Path

q = json.loads(Path("state/jarvis_brain/action_queue_v6_4.json").read_text(encoding="utf-8"))
items = q.get("items", [])
pending = [
    x for x in items
    if isinstance(x, dict)
    and x.get("status") == "pending"
    and isinstance(x.get("gateway_plan"), list)
    and x.get("gateway_plan")
]

print(json.dumps({
    "total_items": len(items),
    "pending_gateway_tasks": len(pending),
    "pending_ids": [x.get("id") for x in pending[-10:]]
}, ensure_ascii=False, indent=2))