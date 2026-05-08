import argparse
import json
import os
import subprocess
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


def run_cmd(cmd, cwd, timeout=120):
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False
        )
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout[-8000:],
            "stderr": p.stderr[-8000:],
            "cmd": cmd,
        }
    except Exception as e:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
            "cmd": cmd,
        }


def latest_evidence(project_root):
    p = Path(project_root) / "jarvis_stage3_artifacts" / "real_action_evidence" / "latest_real_action_evidence_report.json"
    return read_json(p, {"truth_guard": {"verdict": "missing"}})


def mark_task(queue, task_id, status, evidence_path=None, note=None):
    for item in queue.get("items", []):
        if item.get("id") == task_id:
            item["status"] = status
            item["updated_at"] = now()
            item.setdefault("execution_history", []).append({
                "time": now(),
                "status": status,
                "evidence_path": evidence_path,
                "note": note
            })
            return item
    return None


def task_ready(item, queue):
    completed = {x.get("id") for x in queue.get("items", []) if x.get("status") == "completed"}
    missing = [d for d in item.get("depends_on", []) if d not in completed]
    return len(missing) == 0, missing


def execute_integrate_truth_guard(project_root, run_dir):
    scripts = Path(project_root) / "scripts"
    night_candidates = list(scripts.glob("jarvis_night_mode_v6_2*.ps1"))
    bridge = scripts / "jarvis_truth_guard_evidence_bridge_v6_3.ps1"

    report = {
        "task": "integrate_truth_guard_bridge_into_night_mode",
        "created_at": now(),
        "bridge_exists": bridge.exists(),
        "night_mode_candidates": [str(x) for x in night_candidates],
        "actions": [],
        "ok": False,
    }

    if not bridge.exists():
        report["actions"].append("Bridge script missing; cannot integrate.")
        return report

    helper = scripts / "jarvis_run_night_mode_with_evidence_v6_5.ps1"
    content = f'''param(
    [int]$DurationHours = 6,
    [int]$SleepSeconds = 90,
    [int]$MaxIterations = 20,
    [string]$ProjectRoot = "{project_root}",
    [string]$BaseUrl = "http://127.0.0.1:8015"
)

$ErrorActionPreference = "Continue"
Set-Location $ProjectRoot

Write-Host "=== PRE-NIGHT EVIDENCE GATE ===" -ForegroundColor Cyan
& ".\\scripts\\jarvis_truth_guard_evidence_bridge_v6_3.ps1" -ProjectRoot $ProjectRoot -BaseUrl $BaseUrl
$EvidenceExit = $LASTEXITCODE

if ($EvidenceExit -ne 0) {{
    Write-Host "Evidence gate did not fully pass. Night mode can continue only in safe review mode." -ForegroundColor Yellow
}}

$Night = ".\\scripts\\jarvis_night_mode_v6_2_brain_loop_safe.ps1"
if (-not (Test-Path $Night)) {{
    throw "Night mode script not found: $Night"
}}

Write-Host "=== START NIGHT MODE WITH EVIDENCE AWARENESS ===" -ForegroundColor Green
& $Night -DurationHours $DurationHours -SleepSeconds $SleepSeconds -MaxIterations $MaxIterations
'''
    write_text(helper, content)

    report["actions"].append(f"Created evidence-aware night runner: {helper}")
    report["helper_script"] = str(helper)
    report["ok"] = True
    return report


def execute_tool_chain_probe(project_root, run_dir):
    root = Path(project_root)
    candidates = [
        "app/services/tool_router.py",
        "app/services/tool_chain_planner.py",
        "app/services/tool_chain_executor.py",
        "app/routers/tool_chains.py",
        "app/routers",
        "tools",
        "scripts",
    ]

    results = []
    for rel in candidates:
        p = root / rel
        results.append({
            "path": rel,
            "exists": p.exists(),
            "kind": "directory" if p.is_dir() else "file" if p.is_file() else "missing",
            "size": p.stat().st_size if p.exists() and p.is_file() else None,
        })

    probe_path = run_dir / "tool_chain_readiness_report.md"
    lines = ["# Tool Chain Readiness Probe", "", f"Created: `{now()}`", ""]
    ok_count = 0
    for r in results:
        mark = "✅" if r["exists"] else "❌"
        if r["exists"]:
            ok_count += 1
        lines.append(f"- {mark} `{r['path']}` kind=`{r['kind']}`")

    verdict = "ready_or_partial" if ok_count >= 3 else "not_ready"
    lines.append("")
    lines.append(f"Verdict: `{verdict}`")
    write_text(probe_path, "\n".join(lines) + "\n")

    return {
        "task": "create_tool_chain_readiness_probe",
        "created_at": now(),
        "ok": ok_count >= 3,
        "verdict": verdict,
        "results": results,
        "report": str(probe_path),
    }


def execute_strategy_memory_snapshot(project_root, run_dir):
    root = Path(project_root)
    state = root / "state" / "jarvis_brain"
    strategy = read_json(state / "long_term_strategy_v6_4.json", {})
    queue = read_json(state / "action_queue_v6_4.json", {})
    evidence = latest_evidence(project_root)

    snapshot = {
        "schema": "jarvis.strategy_memory_snapshot.v6_5",
        "created_at": now(),
        "strategy": strategy,
        "queue_summary": {
            "total": len(queue.get("items", [])),
            "pending": len([x for x in queue.get("items", []) if x.get("status") == "pending"]),
            "completed": len([x for x in queue.get("items", []) if x.get("status") == "completed"]),
        },
        "evidence_truth_guard": evidence.get("truth_guard", {}),
    }

    out_json = run_dir / "strategy_memory_snapshot.json"
    out_md = run_dir / "strategy_memory_snapshot.md"
    write_json(out_json, snapshot)

    lines = [
        "# Strategy Memory Snapshot",
        "",
        f"Created: `{snapshot['created_at']}`",
        f"Mission: {strategy.get('mission')}",
        "",
        f"Queue total: `{snapshot['queue_summary']['total']}`",
        f"Pending: `{snapshot['queue_summary']['pending']}`",
        f"Completed: `{snapshot['queue_summary']['completed']}`",
        "",
        f"Truth Guard: `{snapshot['evidence_truth_guard'].get('verdict')}`",
    ]
    write_text(out_md, "\n".join(lines) + "\n")

    memory_dir = root / "artifacts" / "memory" / "summaries"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_copy = memory_dir / ("strategy_snapshot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".md")
    write_text(memory_copy, "\n".join(lines) + "\n")

    return {
        "task": "create_strategy_memory_snapshot",
        "ok": True,
        "snapshot_json": str(out_json),
        "snapshot_md": str(out_md),
        "memory_copy": str(memory_copy),
    }


def execute_task(project_root, task, run_dir):
    tid = task.get("id")

    if task.get("risk") not in ("low", "safe"):
        return {
            "ok": False,
            "blocked": True,
            "reason": "Only low-risk tasks are executed automatically.",
            "task": tid,
        }

    if tid == "integrate_truth_guard_bridge_into_night_mode":
        return execute_integrate_truth_guard(project_root, run_dir)

    if tid == "create_tool_chain_readiness_probe":
        return execute_tool_chain_probe(project_root, run_dir)

    if tid == "create_strategy_memory_snapshot":
        return execute_strategy_memory_snapshot(project_root, run_dir)

    return {
        "ok": False,
        "blocked": True,
        "reason": "No safe executor registered for this task.",
        "task": tid,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    project_root = Path(args.project_root)
    state_dir = Path(args.state_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    queue_path = state_dir / "action_queue_v6_4.json"
    queue = read_json(queue_path, {"items": []})

    run_id = "queue_exec_v6_5_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    results = []
    executed = 0

    items = sorted(queue.get("items", []), key=lambda x: (x.get("priority", 50), x.get("created_at", "")))

    for item in items:
        if executed >= args.limit:
            break

        if item.get("status") != "pending":
            continue

        ready, missing = task_ready(item, queue)
        if not ready:
            results.append({
                "task": item.get("id"),
                "ok": False,
                "blocked": True,
                "reason": "missing_dependencies",
                "missing_dependencies": missing,
            })
            continue

        task_dir = run_dir / item.get("id", "unknown")
        task_dir.mkdir(parents=True, exist_ok=True)

        mark_task(queue, item.get("id"), "running", str(task_dir), "Execution started.")
        result = execute_task(project_root, item, task_dir)
        result_path = task_dir / "execution_result.json"
        write_json(result_path, result)

        if result.get("ok"):
            mark_task(queue, item.get("id"), "completed", str(result_path), "Execution completed with evidence.")
        elif result.get("blocked"):
            mark_task(queue, item.get("id"), "blocked", str(result_path), result.get("reason"))
        else:
            mark_task(queue, item.get("id"), "failed", str(result_path), "Execution failed.")

        results.append(result)
        executed += 1

    queue["updated_at"] = now()
    write_json(queue_path, queue)

    summary = {
        "schema": "jarvis.queue_executor.v6_5",
        "run_id": run_id,
        "created_at": now(),
        "executed": executed,
        "results": results,
        "queue_path": str(queue_path),
    }

    latest_json = out_dir / "latest_queue_executor_report.json"
    latest_md = out_dir / "latest_queue_executor_report.md"
    write_json(run_dir / "queue_executor_report.json", summary)
    write_json(latest_json, summary)

    lines = ["# Jarvis Queue Executor V6.5 Report", "", f"Run ID: `{run_id}`", f"Executed: `{executed}`", ""]
    for r in results:
        status = "✅" if r.get("ok") else "⚠️" if r.get("blocked") else "❌"
        lines.append(f"- {status} `{r.get('task')}` ok=`{r.get('ok')}` blocked=`{r.get('blocked', False)}`")
        if r.get("reason"):
            lines.append(f"  - Reason: `{r.get('reason')}`")
    write_text(latest_md, "\n".join(lines) + "\n")

    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "executed": executed,
        "completed": [r.get("task") for r in results if r.get("ok")],
        "blocked": [r.get("task") for r in results if r.get("blocked")],
        "latest_report": str(latest_json),
        "latest_summary": str(latest_md),
        "queue_path": str(queue_path),
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())