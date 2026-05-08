import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def read_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def load_latest_evidence(project_root):
    p = Path(project_root) / "jarvis_stage3_artifacts" / "real_action_evidence" / "latest_real_action_evidence_report.json"
    if not p.exists():
        return {
            "available": False,
            "path": str(p),
            "truth_guard": {
                "verdict": "missing_evidence",
                "passed": False,
                "gaps": ["latest_real_action_evidence_report_missing"],
                "evidence": [],
                "strict_next_action": "run_real_action_evidence",
            },
        }
    data = read_json(p, {})
    data["available"] = True
    data["path"] = str(p)
    return data


def default_strategy():
    return {
        "schema": "jarvis.long_term_strategy.v6_4",
        "created_at": now(),
        "updated_at": now(),
        "mission": "Build Jarvis into a reliable autonomous supervisor that can plan, execute, verify, learn, and safely improve itself.",
        "principles": [
            "Never treat a task as completed without evidence.",
            "Prefer low-risk additive improvements.",
            "Create operator review tasks for risky or unclear changes.",
            "Keep backend healthy before expanding features.",
            "Use evidence reports, smoke tests, and artifacts as source of truth.",
            "Improve tool execution before improving abstract planning.",
            "Preserve secrets and avoid exposing credentials."
        ],
        "strategic_blocks": [
            {
                "id": "block_1_control_continuity",
                "priority": 1,
                "status": "active",
                "goal": "Reliable mission continuity, queues, priorities, safe recovery, and evidence-based completion."
            },
            {
                "id": "block_2_tools_execution_power",
                "priority": 2,
                "status": "active",
                "goal": "Real tools, file actions, API actions, n8n actions, Google Workspace actions, and structured tool chains."
            },
            {
                "id": "block_3_long_autonomy",
                "priority": 3,
                "status": "planned",
                "goal": "Long-running autonomous loops with memory, scheduling, retries, and operator-safe escalation."
            },
            {
                "id": "block_4_self_improvement",
                "priority": 4,
                "status": "guarded",
                "goal": "Self-improvement only through evidence gates, code gates, rollback plans, and smoke tests."
            }
        ],
        "current_focus": [
            "evidence-aware task execution",
            "dependency-aware queue",
            "safe autonomous next actions",
            "n8n pipeline readiness",
            "tool-chain integration"
        ]
    }


def normalize_queue(queue):
    items = queue.get("items", [])
    seen = set()
    clean = []
    for item in items:
        tid = item.get("id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        item.setdefault("status", "pending")
        item.setdefault("priority", 50)
        item.setdefault("depends_on", [])
        item.setdefault("risk", "low")
        item.setdefault("created_at", now())
        clean.append(item)
    clean.sort(key=lambda x: (x.get("status") != "pending", x.get("priority", 50), x.get("created_at", "")))
    queue["items"] = clean
    queue["updated_at"] = now()
    return queue


def make_task(task_id, title, details, priority=50, risk="low", depends_on=None, lane="system"):
    return {
        "id": task_id,
        "title": title,
        "details": details,
        "priority": priority,
        "risk": risk,
        "lane": lane,
        "depends_on": depends_on or [],
        "status": "pending",
        "created_at": now(),
        "evidence_required": True,
        "completion_gate": [
            "evidence_report_exists",
            "backend_health_checked",
            "result_artifact_written"
        ]
    }


def queue_has(queue, task_id):
    return any(x.get("id") == task_id for x in queue.get("items", []))


def plan_from_evidence(project_root, strategy, evidence, queue):
    tg = evidence.get("truth_guard", {})
    gaps = tg.get("gaps", []) or []
    backend_status = evidence.get("backend", {}).get("summary", {}).get("status")
    n8n_open = evidence.get("n8n", {}).get("port", {}).get("open")

    added = []

    def add(task):
        if not queue_has(queue, task["id"]):
            queue.setdefault("items", []).append(task)
            added.append(task["id"])

    if not evidence.get("available"):
        add(make_task(
            "run_real_action_evidence",
            "Run real action evidence collector",
            "Generate latest real action evidence report before allowing autonomous completion claims.",
            priority=1,
            risk="low",
            lane="evidence"
        ))

    if backend_status not in ("healthy", "partial"):
        add(make_task(
            "repair_backend_health",
            "Repair backend health before autonomous expansion",
            "Backend endpoints are not healthy enough. Run restart diagnostics and collect fresh evidence.",
            priority=2,
            risk="medium",
            lane="backend"
        ))

    if "n8n_port_closed_or_unavailable" in gaps or n8n_open is False:
        add(make_task(
            "inspect_n8n_readiness",
            "Inspect n8n readiness",
            "Check whether n8n is intentionally offline, misconfigured, or needs startup. Do not expose secrets.",
            priority=8,
            risk="low",
            lane="n8n"
        ))

    add(make_task(
        "integrate_truth_guard_bridge_into_night_mode",
        "Integrate Truth Guard evidence bridge into night mode",
        "Night mode should call jarvis_truth_guard_evidence_bridge_v6_3.ps1 before marking tasks completed.",
        priority=5,
        risk="low",
        lane="night_mode"
    ))

    add(make_task(
        "create_tool_chain_readiness_probe",
        "Create tool-chain readiness probe",
        "Inspect available tool-chain endpoints/scripts and produce a readiness report for real action execution.",
        priority=12,
        risk="low",
        lane="tools"
    ))

    add(make_task(
        "create_strategy_memory_snapshot",
        "Create strategy memory snapshot",
        "Persist current strategy, active focus, queue state, and evidence verdict for future mission resume.",
        priority=15,
        risk="low",
        lane="memory"
    ))

    return added


def choose_next_tasks(queue, limit=5):
    completed = {x.get("id") for x in queue.get("items", []) if x.get("status") == "completed"}
    ready = []
    blocked = []

    for item in queue.get("items", []):
        if item.get("status") != "pending":
            continue
        deps = item.get("depends_on", [])
        missing = [d for d in deps if d not in completed]
        if missing:
            copy = dict(item)
            copy["missing_dependencies"] = missing
            blocked.append(copy)
        else:
            ready.append(item)

    ready.sort(key=lambda x: (x.get("priority", 50), x.get("created_at", "")))
    return ready[:limit], blocked


def write_markdown_report(path, strategy, evidence, queue, added, ready, blocked):
    tg = evidence.get("truth_guard", {})
    lines = []
    lines.append("# Jarvis Strategic Brain V6.4 Report")
    lines.append("")
    lines.append(f"- Created: `{now()}`")
    lines.append(f"- Mission: {strategy.get('mission')}")
    lines.append(f"- Truth Guard verdict: `{tg.get('verdict')}`")
    lines.append(f"- Strict next action: `{tg.get('strict_next_action')}`")
    lines.append(f"- Queue size: `{len(queue.get('items', []))}`")
    lines.append(f"- New tasks added: `{len(added)}`")
    lines.append("")
    lines.append("## Current focus")
    for f in strategy.get("current_focus", []):
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## New tasks")
    if added:
        for tid in added:
            lines.append(f"- ✅ `{tid}`")
    else:
        lines.append("- No new tasks added.")
    lines.append("")
    lines.append("## Ready next actions")
    if ready:
        for t in ready:
            lines.append(f"- `{t.get('id')}` — {t.get('title')} / priority `{t.get('priority')}` / risk `{t.get('risk')}`")
    else:
        lines.append("- No ready pending tasks.")
    lines.append("")
    lines.append("## Blocked tasks")
    if blocked:
        for t in blocked:
            lines.append(f"- `{t.get('id')}` missing deps: `{t.get('missing_dependencies')}`")
    else:
        lines.append("- No blocked tasks.")
    lines.append("")
    lines.append("## Evidence gaps")
    gaps = tg.get("gaps", []) or []
    if gaps:
        for g in gaps:
            lines.append(f"- ⚠️ {g}")
    else:
        lines.append("- No critical evidence gaps.")
    write_text(path, "\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    out_dir = Path(args.out_dir)
    state_dir = Path(args.state_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    strategy_path = state_dir / "long_term_strategy_v6_4.json"
    queue_path = state_dir / "action_queue_v6_4.json"
    snapshot_path = state_dir / "latest_brain_snapshot_v6_4.json"

    strategy = read_json(strategy_path, None)
    if not strategy:
        strategy = default_strategy()
    strategy["updated_at"] = now()

    queue = read_json(queue_path, {"schema": "jarvis.action_queue.v6_4", "created_at": now(), "items": []})
    evidence = load_latest_evidence(project_root)

    added = plan_from_evidence(project_root, strategy, evidence, queue)
    queue = normalize_queue(queue)
    ready, blocked = choose_next_tasks(queue)

    run_id = "brain_v6_4_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "schema": "jarvis.strategic_brain_snapshot.v6_4",
        "run_id": run_id,
        "created_at": now(),
        "strategy": strategy,
        "evidence_summary": {
            "available": evidence.get("available"),
            "path": evidence.get("path"),
            "truth_guard": evidence.get("truth_guard"),
            "backend_summary": evidence.get("backend", {}).get("summary"),
            "n8n_port_open": evidence.get("n8n", {}).get("port", {}).get("open"),
        },
        "queue": queue,
        "added_task_ids": added,
        "ready_next_tasks": ready,
        "blocked_tasks": blocked,
    }

    write_json(strategy_path, strategy)
    write_json(queue_path, queue)
    write_json(snapshot_path, snapshot)

    run_json = run_dir / "brain_report.json"
    run_md = run_dir / "brain_report.md"
    latest_md = out_dir / "latest_strategic_brain_report.md"
    latest_json = out_dir / "latest_strategic_brain_report.json"

    write_json(run_json, snapshot)
    write_json(latest_json, snapshot)
    write_markdown_report(run_md, strategy, evidence, queue, added, ready, blocked)
    write_markdown_report(latest_md, strategy, evidence, queue, added, ready, blocked)

    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "truth_guard_verdict": evidence.get("truth_guard", {}).get("verdict"),
        "queue_size": len(queue.get("items", [])),
        "added_task_ids": added,
        "ready_next_tasks": [x.get("id") for x in ready],
        "blocked_tasks": [x.get("id") for x in blocked],
        "strategy_path": str(strategy_path),
        "queue_path": str(queue_path),
        "latest_report": str(latest_json),
        "latest_summary": str(latest_md),
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())