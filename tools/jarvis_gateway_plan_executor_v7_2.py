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
    path = Path(project_root) / "tools" / "jarvis_unified_tool_gateway_v7_0.py"
    spec = importlib.util.spec_from_file_location("jarvis_unified_tool_gateway_v7_0", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mark(queue, task_id, status, note, evidence):
    for item in queue.get("items", []):
        if item.get("id") == task_id:
            item["status"] = status
            item["updated_at"] = now()
            item.setdefault("execution_history", []).append({
                "time": now(),
                "status": status,
                "note": note,
                "evidence_path": evidence,
                "executor": "gateway_plan_executor_v7_2"
            })
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=8)
    args = ap.parse_args()

    root = Path(args.project_root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    queue_path = root / "state" / "jarvis_brain" / "action_queue_v6_4.json"
    queue = read_json(queue_path, {"items": []})
    gateway = load_gateway(root)

    run_id = "gateway_plan_exec_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        x for x in queue.get("items", [])
        if x.get("status") == "pending"
        and x.get("executor") == "gateway_plan_v7_2"
        and x.get("risk") in ("low", "safe")
    ]
    tasks = sorted(tasks, key=lambda x: (x.get("priority", 50), x.get("created_at", "")))[:args.limit]

    executed = []

    for task in tasks:
        tid = task.get("id")
        task_dir = run_dir / tid
        task_dir.mkdir(parents=True, exist_ok=True)

        mark(queue, tid, "running", "Gateway plan execution started.", str(task_dir))

        steps = []
        ok_all = True

        for i, step in enumerate(task.get("gateway_plan", []), start=1):
            tool = step.get("tool")
            tool_args = step.get("args", {})
            result = gateway.invoke_tool(str(root), str(root / "jarvis_stage3_artifacts" / "unified_tool_gateway_v7_0"), tool, tool_args)
            step_report = {
                "index": i,
                "tool": tool,
                "args": tool_args,
                "ok": bool(result.get("ok")),
                "result": result,
                "time": now()
            }
            write_json(task_dir / f"step_{i:02d}_{tool}.json", step_report)
            steps.append(step_report)
            if not result.get("ok"):
                ok_all = False
                break

        result = {
            "task_id": tid,
            "ok": ok_all,
            "steps_total": len(task.get("gateway_plan", [])),
            "steps_done": len(steps),
            "evidence_dir": str(task_dir)
        }

        write_json(task_dir / "gateway_plan_result.json", result)

        if ok_all:
            mark(queue, tid, "completed", "Gateway plan completed with evidence.", str(task_dir / "gateway_plan_result.json"))
        else:
            mark(queue, tid, "failed", "Gateway plan failed; inspect step evidence.", str(task_dir / "gateway_plan_result.json"))

        executed.append(result)

    queue["updated_at"] = now()
    write_json(queue_path, queue)

    report = {
        "schema": "jarvis.gateway_plan_executor.v7_2",
        "created_at": now(),
        "run_id": run_id,
        "executed_count": len(executed),
        "completed": [x["task_id"] for x in executed if x["ok"]],
        "failed": [x["task_id"] for x in executed if not x["ok"]],
        "run_dir": str(run_dir),
        "queue_path": str(queue_path)
    }

    write_json(out / "latest_gateway_plan_executor_report.json", report)

    md = [
        "# Jarvis Gateway Plan Executor V7.2",
        "",
        f"Run ID: `{run_id}`",
        f"Executed: `{len(executed)}`",
        "",
        "## Completed"
    ]
    for x in report["completed"]:
        md.append(f"- ✅ `{x}`")
    if not report["completed"]:
        md.append("- none")

    md.append("")
    md.append("## Failed")
    for x in report["failed"]:
        md.append(f"- ❌ `{x}`")
    if not report["failed"]:
        md.append("- none")

    (out / "latest_gateway_plan_executor_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())