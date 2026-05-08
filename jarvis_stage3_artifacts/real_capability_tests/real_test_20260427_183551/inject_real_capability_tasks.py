from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

queue_path = Path("state/jarvis_brain/action_queue_v6_4.json")
test_dir = Path(r"jarvis_stage3_artifacts\\real_capability_tests\\real_test_20260427_183551")
test_dir.mkdir(parents=True, exist_ok=True)

now = datetime.utcnow().isoformat()

def load_queue():
    if not queue_path.exists():
        return []
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("items", "queue", "tasks", "actions"):
            if isinstance(data.get(key), list):
                return data[key]

    return []

def task(kind, title, goal, payload):
    return {
        "id": f"real_test_{kind}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}",
        "kind": kind,
        "title": title,
        "goal": goal,
        "status": "pending",
        "priority": 80,
        "created_at": now,
        "source": "jarvis_real_capability_test_pack_v1",
        "executor": "claude_ecosystem_v1_3",
        "payload": payload,
        "safety": {
            "mode": "safe_package_first",
            "allow_direct_code_apply": False,
            "require_artifact": True,
            "require_qa": True,
            "require_rollback_plan": True
        }
    }

new_tasks = [
    task(
        "full_creator_v1_real_test",
        "FULL CREATOR: mini SaaS concept package",
        "Create a complete artifact-first mini-SaaS package idea for a Jarvis Operator Command Center.",
        {
            "product_name": "Jarvis Operator Command Center",
            "target_user": "Jarvis operator",
            "expected_artifacts": [
                "product brief",
                "design spec",
                "implementation plan",
                "QA checklist",
                "rollback plan"
            ]
        }
    ),
    task(
        "backend_self_healing_v1_real_test",
        "Backend Self-Healing: health and recovery report",
        "Inspect backend health and generate a safe recovery report with no risky auto-apply.",
        {
            "base_url": "http://127.0.0.1:8015",
            "checks": [
                "/health",
                "/",
                "/api/claude-ecosystem/health"
            ],
            "expected_artifacts": [
                "health snapshot",
                "recovery recommendation",
                "safe actions"
            ]
        }
    ),
    task(
        "night_mode_safe_package_v1_real_test",
        "Night Mode: safe improvement package",
        "Prepare a safe Night Mode improvement package using package-first policy.",
        {
            "policy": "safe_package_first",
            "allowed_auto_apply": [
                "diagnostic scripts",
                "artifact index",
                "logs",
                "non-invasive tests"
            ],
            "blocked_without_package": [
                "core router rewrite",
                "delete files",
                "credentials",
                "database migration"
            ]
        }
    ),
    task(
        "design_to_code_v1_real_test",
        "Design-to-Code: operator dashboard UI spec",
        "Generate a UI/design spec for Jarvis dashboard: missions, approvals, artifacts, night mode, agents.",
        {
            "product_name": "Jarvis Mission Control UI",
            "screens": [
                "Mission Overview",
                "Night Mode Runs",
                "Approval Queue",
                "Artifacts Browser",
                "Agent Health"
            ],
            "future_tool": "Claude Design"
        }
    ),
    task(
        "artifact_index_v1_real_test",
        "Artifact Index: collect created test artifacts",
        "Create an index of generated real capability test artifacts.",
        "payload": {
            "artifact_roots": [
                "jarvis_stage3_artifacts/claude_ecosystem",
                "jarvis_stage3_artifacts/full_creator",
                "jarvis_stage3_artifacts/ultra_upgrade",
                "jarvis_stage3_artifacts/real_capability_tests"
            ]
        }
    )
]

existing = load_queue()

existing_ids = {x.get("id") for x in existing if isinstance(x, dict)}
for t in new_tasks:
    if t["id"] not in existing_ids:
        existing.append(t)

queue_path.parent.mkdir(parents=True, exist_ok=True)
queue_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

summary = {
    "schema": "jarvis.real_capability_test_pack.v1",
    "created_at": now,
    "queue_path": str(queue_path),
    "added_count": len(new_tasks),
    "total_queue_count": len(existing),
    "tasks": [
        {
            "id": t["id"],
            "kind": t["kind"],
            "title": t["title"],
            "status": t["status"]
        }
        for t in new_tasks
    ]
}

(test_dir / "real_capability_test_tasks.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(json.dumps(summary, ensure_ascii=False, indent=2))