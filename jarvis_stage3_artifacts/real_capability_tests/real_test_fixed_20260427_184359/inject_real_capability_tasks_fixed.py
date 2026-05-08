from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

queue_path = Path("state/jarvis_brain/action_queue_v6_4.json")
test_dir = Path(r"jarvis_stage3_artifacts\\real_capability_tests\\real_test_fixed_20260427_184359")
test_dir.mkdir(parents=True, exist_ok=True)

now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

def read_json_safe(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

queue = read_json_safe(queue_path, {})

if not isinstance(queue, dict):
    queue = {
        "schema": "jarvis.action_queue.v6_4",
        "created_at": now,
        "items": []
    }

if "items" not in queue or not isinstance(queue["items"], list):
    queue["items"] = []

def make_task(task_id: str, title: str, lane: str, priority: int, risk: str, plan: list, details: str):
    return {
        "id": task_id,
        "title": title,
        "lane": lane,
        "priority": priority,
        "risk": risk,
        "gateway_plan": plan,
        "details": details,
        "status": "pending",
        "created_at": now,
        "executor": "gateway_plan_v7_2",
        "evidence_required": True,
        "completion_gate": [
            "evidence_report_exists",
            "backend_health_checked",
            "result_artifact_written"
        ],
        "source": "real_capability_test_pack_v1_1_fixed"
    }

base = f"real_capability_v1_1_{stamp}"

tasks = [
    make_task(
        f"{base}_full_creator_package",
        "REAL TEST: FULL CREATOR package",
        "full_creator",
        10,
        "low",
        [
            {
                "tool": "md_report",
                "args": {
                    "path": f"jarvis_stage3_artifacts/real_capability_tests/{base}/full_creator_package.md",
                    "title": "FULL CREATOR Real Test Package",
                    "body": "Jarvis created a product-package artifact for Operator Command Center: idea, design, code plan, QA, rollback, and Night Mode improvement loop."
                }
            }
        ],
        "Create an artifact-first FULL CREATOR package."
    ),
    make_task(
        f"{base}_backend_self_healing_report",
        "REAL TEST: Backend self-healing report",
        "backend",
        20,
        "low",
        [
            {
                "tool": "md_report",
                "args": {
                    "path": f"jarvis_stage3_artifacts/real_capability_tests/{base}/backend_self_healing_report.md",
                    "title": "Backend Self-Healing Report",
                    "body": "Backend self-healing report: check /health, root endpoint, Claude ecosystem health, recovery recommendation, safe actions, and no risky auto-apply."
                }
            }
        ],
        "Create backend recovery/safety report."
    ),
    make_task(
        f"{base}_night_mode_safe_package",
        "REAL TEST: Night Mode safe package",
        "night_mode",
        30,
        "low",
        [
            {
                "tool": "md_report",
                "args": {
                    "path": f"jarvis_stage3_artifacts/real_capability_tests/{base}/night_mode_safe_package.md",
                    "title": "Night Mode Safe Package",
                    "body": "Night Mode safe package: package-first policy, allowed auto-apply items, blocked risky items, required checks, diff summary and rollback plan."
                }
            }
        ],
        "Create Night Mode safe improvement package."
    ),
    make_task(
        f"{base}_design_to_code_ui_spec",
        "REAL TEST: Design-to-Code UI spec",
        "claude_design",
        40,
        "low",
        [
            {
                "tool": "md_report",
                "args": {
                    "path": f"jarvis_stage3_artifacts/real_capability_tests/{base}/design_to_code_ui_spec.md",
                    "title": "Design-to-Code UI Spec",
                    "body": "UI spec for Jarvis Mission Control: mission overview, night mode runs, approval queue, artifacts browser, agent health, operator actions, and future Claude Design pipeline."
                }
            }
        ],
        "Create UI/design specification artifact."
    ),
    make_task(
        f"{base}_artifact_index",
        "REAL TEST: Artifact index",
        "artifacts",
        50,
        "low",
        [
            {
                "tool": "latest_artifacts",
                "args": {
                    "path": "jarvis_stage3_artifacts/real_capability_tests",
                    "limit": 50
                }
            },
            {
                "tool": "md_report",
                "args": {
                    "path": f"jarvis_stage3_artifacts/real_capability_tests/{base}/artifact_index.md",
                    "title": "Real Capability Test Artifact Index",
                    "body": "Artifact index generated after real capability test execution."
                }
            }
        ],
        "Create index of real capability test artifacts."
    )
]

existing_ids = {item.get("id") for item in queue["items"] if isinstance(item, dict)}

added = []
for task in tasks:
    if task["id"] not in existing_ids:
        queue["items"].append(task)
        added.append(task)

queue["updated_at"] = now
queue["guarded_by"] = "real_capability_test_pack_v1_1_fixed"

queue_path.parent.mkdir(parents=True, exist_ok=True)
queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")

summary = {
    "schema": "jarvis.real_capability_test_pack.v1_1_fixed",
    "created_at": now,
    "queue_path": str(queue_path),
    "added_count": len(added),
    "total_items": len(queue["items"]),
    "added_tasks": [
        {
            "id": t["id"],
            "title": t["title"],
            "lane": t["lane"],
            "status": t["status"],
            "gateway_plan_steps": len(t["gateway_plan"])
        }
        for t in added
    ]
}

(test_dir / "injection_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

print(json.dumps(summary, ensure_ascii=False, indent=2))