import argparse
import importlib.util
import json
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


def load_gateway(project_root):
    p = Path(project_root) / "tools" / "jarvis_unified_tool_gateway_v7_0.py"
    spec = importlib.util.spec_from_file_location("gateway", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def classify_task(text):
    t = text.lower()
    scores = {
        "diagnostics": 0,
        "backend": 0,
        "n8n": 0,
        "memory": 0,
        "queue": 0,
        "code_quality": 0,
        "safety": 0,
        "artifacts": 0,
        "recovery": 0,
    }

    keywords = {
        "diagnostics": ["health", "status", "latency", "check", "probe", "diagnostic", "диагност", "провер"],
        "backend": ["backend", "api", "endpoint", "route", "fastapi", "server"],
        "n8n": ["n8n", "workflow", "pipeline", "webhook", "пайплайн", "воркфлоу"],
        "memory": ["memory", "summary", "obsidian", "память", "резюме"],
        "queue": ["queue", "task", "priority", "очеред", "задач"],
        "code_quality": ["code", "syntax", "compile", "imports", "metrics", "код", "ошибк"],
        "safety": ["secret", "token", "risk", "policy", "safe", "секрет", "токен"],
        "artifacts": ["artifact", "report", "log", "journal", "артефакт", "лог"],
        "recovery": ["backup", "restore", "restart", "recovery", "бэкап", "восстанов"],
    }

    for lane, words in keywords.items():
        for w in words:
            if w in t:
                scores[lane] += 1

    best = sorted(scores.items(), key=lambda x: x[1], reverse=True)[0]
    return best[0] if best[1] > 0 else "diagnostics"


def plan_for_lane(lane, title):
    safe_title = title.replace("`", "").replace("\n", " ")[:120]

    plans = {
        "diagnostics": [
            {"tool": "health_matrix", "args": {"base_url": "http://127.0.0.1:8015"}},
            {"tool": "latency_probe", "args": {"url": "http://127.0.0.1:8015/health"}},
            {"tool": "md_report", "args": {
                "path": "jarvis_stage3_artifacts/tool_selection_brain_v7_6/diagnostics_report.md",
                "title": "Tool Selection Diagnostics Report",
                "body": f"Selected diagnostics plan for: {safe_title}"
            }},
        ],
        "backend": [
            {"tool": "route_scan", "args": {"path": "app"}},
            {"tool": "openapi_probe", "args": {"base_url": "http://127.0.0.1:8015"}},
            {"tool": "error_scan", "args": {"path": "jarvis_stage3_artifacts/backend_logs"}},
        ],
        "n8n": [
            {"tool": "n8n_health_matrix", "args": {"base_url": "http://127.0.0.1:5678"}},
            {"tool": "n8n_workflow_stub", "args": {
                "name": "Jarvis Selected Workflow Stub",
                "path": "jarvis_stage3_artifacts/n8n_workflows_v7_6/selected_workflow_stub.json"
            }},
            {"tool": "md_report", "args": {
                "path": "jarvis_stage3_artifacts/tool_selection_brain_v7_6/n8n_selected_plan.md",
                "title": "n8n Selected Plan",
                "body": f"Selected n8n plan for: {safe_title}"
            }},
        ],
        "memory": [
            {"tool": "memory_index", "args": {}},
            {"tool": "memory_search", "args": {"query": "strategy"}},
            {"tool": "memory_write_summary", "args": {
                "title": "Tool Selection Memory Summary",
                "body": f"Selected memory plan for: {safe_title}"
            }},
        ],
        "queue": [
            {"tool": "queue_summary", "args": {}},
            {"tool": "queue_ready", "args": {"limit": 20}},
            {"tool": "md_report", "args": {
                "path": "jarvis_stage3_artifacts/tool_selection_brain_v7_6/queue_report.md",
                "title": "Queue Selected Plan",
                "body": f"Selected queue plan for: {safe_title}"
            }},
        ],
        "code_quality": [
            {"tool": "code_metrics", "args": {"path": "app"}},
            {"tool": "python_syntax_scan", "args": {"path": "app", "limit": 200}},
            {"tool": "todo_scan", "args": {"path": "app"}},
            {"tool": "md_report", "args": {
                "path": "jarvis_stage3_artifacts/tool_selection_brain_v7_6/code_quality_report.md",
                "title": "Code Quality Selected Plan",
                "body": f"Selected code quality plan for: {safe_title}"
            }},
        ],
        "safety": [
            {"tool": "secret_leak_scan", "args": {"path": "app"}},
            {"tool": "config_inventory", "args": {}},
            {"tool": "policy_gate", "args": {"risk": "low"}},
        ],
        "artifacts": [
            {"tool": "latest_artifacts", "args": {"path": "jarvis_stage3_artifacts", "limit": 50}},
            {"tool": "artifact_index", "args": {"path": "jarvis_stage3_artifacts", "limit": 200}},
            {"tool": "report_bundle", "args": {
                "src": "jarvis_stage3_artifacts",
                "dst": "jarvis_stage3_artifacts/tool_selection_brain_v7_6/artifact_bundle.zip"
            }},
        ],
        "recovery": [
            {"tool": "backup_state", "args": {}},
            {"tool": "backup_scripts", "args": {}},
            {"tool": "restart_backend_plan", "args": {}},
        ],
    }

    return plans.get(lane, plans["diagnostics"])


def add_queue_task(project_root, task):
    qpath = Path(project_root) / "state" / "jarvis_brain" / "action_queue_v6_4.json"
    queue = read_json(qpath, {"schema": "jarvis.action_queue.v6_4", "items": []})

    if any(x.get("id") == task["id"] for x in queue.get("items", [])):
        return False, len(queue.get("items", []))

    queue.setdefault("items", []).append(task)
    queue["updated_at"] = now()
    write_json(qpath, queue)
    return True, len(queue.get("items", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    lane = classify_task(args.task)
    plan = plan_for_lane(lane, args.task)

    task_id = "selected_" + lane + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")

    task = {
        "id": task_id,
        "title": args.task[:140],
        "details": "Generated by V7.6 Tool Selection Brain.",
        "priority": 22,
        "risk": "low",
        "lane": lane,
        "status": "pending",
        "created_at": now(),
        "executor": "gateway_plan_v7_2",
        "evidence_required": True,
        "gateway_plan": plan,
        "selection_reason": {
            "classified_lane": lane,
            "task_text": args.task,
            "tools_selected": [x["tool"] for x in plan]
        }
    }

    dry_results = []
    if args.dry_run:
        gateway = load_gateway(project_root)
        for step in plan:
            tool = step["tool"]
            dry_results.append({
                "tool": tool,
                "known": tool in gateway.build_catalog(str(project_root))[0]
            })

    added, queue_size = add_queue_task(project_root, task)

    report = {
        "schema": "jarvis.tool_selection_brain.v7_6",
        "created_at": now(),
        "task_id": task_id,
        "task_text": args.task,
        "classified_lane": lane,
        "tools_selected": [x["tool"] for x in plan],
        "added_to_queue": added,
        "queue_size": queue_size,
        "dry_run": dry_results,
        "task": task,
    }

    write_json(out_dir / "latest_tool_selection_report.json", report)

    md = [
        "# Jarvis Tool Selection Brain V7.6",
        "",
        f"Created: `{report['created_at']}`",
        f"Task: `{args.task}`",
        f"Lane: `{lane}`",
        f"Added to queue: `{added}`",
        "",
        "## Selected tools"
    ]
    for t in report["tools_selected"]:
        md.append(f"- `{t}`")

    (out_dir / "latest_tool_selection_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())