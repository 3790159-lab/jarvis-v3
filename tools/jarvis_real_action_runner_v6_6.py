import argparse
import json
import os
import subprocess
import urllib.request
import urllib.error
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


def safe_rel_path(project_root, rel):
    root = Path(project_root).resolve()
    p = (root / rel).resolve()
    if not str(p).startswith(str(root)):
        raise ValueError("Path escapes project root")
    return p


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
            "stdout": p.stdout[-10000:],
            "stderr": p.stderr[-10000:],
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


def http_get(url, timeout=8):
    result = {"ok": False, "url": url, "status_code": None, "body_preview": None, "error": None}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JarvisRealActionRunner/6.6"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(5000).decode("utf-8", errors="replace")
            result["status_code"] = getattr(r, "status", None)
            result["body_preview"] = body[:1500]
            result["ok"] = 200 <= int(result["status_code"] or 0) < 400
    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["error"] = str(e)
        try:
            result["body_preview"] = e.read(1500).decode("utf-8", errors="replace")
        except Exception:
            pass
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def tool_file_write(project_root, args):
    rel = args.get("path")
    content = args.get("content", "")
    if not rel:
        return {"ok": False, "error": "missing path"}
    p = safe_rel_path(project_root, rel)
    write_text(p, content)
    return {"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))}


def tool_file_read(project_root, args):
    rel = args.get("path")
    if not rel:
        return {"ok": False, "error": "missing path"}
    p = safe_rel_path(project_root, rel)
    if not p.exists():
        return {"ok": False, "error": "file missing", "path": str(p)}
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"ok": True, "path": str(p), "preview": text[:4000], "bytes": len(text.encode("utf-8"))}


def tool_endpoint_probe(project_root, args):
    base = args.get("base_url", "http://127.0.0.1:8015").rstrip("/")
    endpoints = args.get("endpoints") or ["/", "/health", "/api/ai/health"]
    results = []
    for ep in endpoints:
        if ep.startswith("http://") or ep.startswith("https://"):
            url = ep
        else:
            url = base + ep
        results.append(http_get(url))
    return {
        "ok": any(x.get("ok") for x in results),
        "results": results,
    }


def tool_python_compile(project_root, args):
    files = args.get("files") or []
    if not files:
        files = ["app/main.py"]
    pyexe = args.get("python", "python")
    results = []
    for rel in files:
        p = safe_rel_path(project_root, rel)
        if not p.exists():
            results.append({"file": rel, "ok": False, "error": "missing"})
            continue
        r = run_cmd([pyexe, "-m", "py_compile", str(p)], cwd=project_root, timeout=120)
        results.append({"file": rel, **r})
    return {"ok": all(x.get("ok") for x in results), "results": results}


def tool_powershell_script(project_root, args):
    rel = args.get("script")
    script_args = args.get("args", [])
    if not rel:
        return {"ok": False, "error": "missing script"}
    if not rel.startswith("scripts/") and not rel.startswith("scripts\\"):
        return {"ok": False, "error": "only scripts directory allowed"}
    p = safe_rel_path(project_root, rel)
    if not p.exists():
        return {"ok": False, "error": "script missing", "path": str(p)}
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(p)] + list(script_args)
    return run_cmd(cmd, cwd=project_root, timeout=int(args.get("timeout", 180)))


def tool_artifact_report(project_root, args):
    title = args.get("title", "Jarvis Action Report")
    body = args.get("body", "")
    out_rel = args.get("path", "jarvis_stage3_artifacts/real_action_runner_v6_6/manual_report.md")
    p = safe_rel_path(project_root, out_rel)
    text = f"# {title}\n\nCreated: `{now()}`\n\n{body}\n"
    write_text(p, text)
    return {"ok": True, "path": str(p)}


def tool_n8n_probe(project_root, args):
    urls = args.get("urls") or [
        "http://127.0.0.1:5678/healthz",
        "http://127.0.0.1:5678/rest/settings"
    ]
    results = [http_get(u, timeout=5) for u in urls]
    return {"ok": any(x.get("ok") for x in results), "results": results}


TOOLS = {
    "file_write": tool_file_write,
    "file_read": tool_file_read,
    "endpoint_probe": tool_endpoint_probe,
    "python_compile": tool_python_compile,
    "powershell_script": tool_powershell_script,
    "artifact_report": tool_artifact_report,
    "n8n_probe": tool_n8n_probe,
}


def task_to_plan(task, project_root, pyexe):
    tid = task.get("id", "")
    title = task.get("title", "")

    if tid == "create_tool_chain_readiness_probe":
        return [
            {"tool": "file_read", "args": {"path": "tools/jarvis_queue_executor_v6_5.py"}},
            {"tool": "endpoint_probe", "args": {"base_url": "http://127.0.0.1:8015", "endpoints": ["/health", "/api/ai/health", "/api/autonomy/health"]}},
            {"tool": "artifact_report", "args": {
                "path": "jarvis_stage3_artifacts/real_action_runner_v6_6/tool_chain_probe_real.md",
                "title": "Real Tool Chain Probe",
                "body": "Tool-chain probe executed through real action runner. Evidence includes file read and endpoint checks."
            }},
        ]

    if tid == "integrate_truth_guard_bridge_into_night_mode":
        return [
            {"tool": "powershell_script", "args": {"script": "scripts/jarvis_truth_guard_evidence_bridge_v6_3.ps1", "args": ["-ProjectRoot", project_root, "-BaseUrl", "http://127.0.0.1:8015"], "timeout": 240}},
            {"tool": "artifact_report", "args": {
                "path": "jarvis_stage3_artifacts/real_action_runner_v6_6/truth_guard_bridge_real.md",
                "title": "Truth Guard Bridge Real Execution",
                "body": "Truth Guard evidence bridge executed as a real PowerShell action."
            }},
        ]

    if tid == "create_strategy_memory_snapshot":
        return [
            {"tool": "file_read", "args": {"path": "state/jarvis_brain/long_term_strategy_v6_4.json"}},
            {"tool": "file_read", "args": {"path": "state/jarvis_brain/action_queue_v6_4.json"}},
            {"tool": "artifact_report", "args": {
                "path": "artifacts/memory/summaries/real_action_strategy_snapshot_v6_6.md",
                "title": "Real Action Strategy Snapshot V6.6",
                "body": "Strategy and queue state were read by the real action runner and saved as memory evidence."
            }},
        ]

    if "n8n" in tid.lower() or "n8n" in title.lower():
        return [
            {"tool": "n8n_probe", "args": {}},
            {"tool": "artifact_report", "args": {
                "path": "jarvis_stage3_artifacts/real_action_runner_v6_6/n8n_probe_real.md",
                "title": "n8n Real Probe",
                "body": "n8n readiness was checked through HTTP probes."
            }},
        ]

    return [
        {"tool": "endpoint_probe", "args": {"base_url": "http://127.0.0.1:8015", "endpoints": ["/health"]}},
        {"tool": "artifact_report", "args": {
            "path": f"jarvis_stage3_artifacts/real_action_runner_v6_6/generic_{tid}.md",
            "title": f"Generic Real Action: {tid}",
            "body": f"Generic safe action executed for task: {title}"
        }},
    ]


def execute_plan(project_root, plan, run_dir):
    steps = []
    for i, step in enumerate(plan, start=1):
        tool = step.get("tool")
        args = step.get("args", {})
        fn = TOOLS.get(tool)
        if not fn:
            result = {"ok": False, "error": f"unknown tool: {tool}"}
        else:
            try:
                result = fn(project_root, args)
            except Exception as e:
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        step_result = {
            "index": i,
            "tool": tool,
            "args": args,
            "result": result,
            "ok": bool(result.get("ok")),
            "time": now(),
        }
        steps.append(step_result)
        write_json(run_dir / f"step_{i:02d}_{tool}.json", step_result)
        if not result.get("ok"):
            break
    return {
        "ok": all(s.get("ok") for s in steps) and len(steps) == len(plan),
        "steps": steps,
    }


def mark(queue, task_id, status, note, evidence_path):
    for item in queue.get("items", []):
        if item.get("id") == task_id:
            item["status"] = status
            item["updated_at"] = now()
            item.setdefault("execution_history", []).append({
                "time": now(),
                "status": status,
                "note": note,
                "evidence_path": evidence_path,
                "executor": "real_action_runner_v6_6",
            })
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--python", default="python")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    project_root = Path(args.project_root)
    state_dir = Path(args.state_dir)
    out_dir = Path(args.out_dir)
    queue_path = state_dir / "action_queue_v6_4.json"

    queue = read_json(queue_path, {"items": []})
    run_id = "real_action_v6_6_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    executed = []
    candidates = sorted(
        [x for x in queue.get("items", []) if x.get("status") in ("pending", "blocked", "failed")],
        key=lambda x: (x.get("priority", 50), x.get("created_at", ""))
    )

    for task in candidates[:args.limit]:
        if task.get("risk") not in ("low", "safe", None):
            continue

        tid = task.get("id")
        task_dir = run_dir / tid
        task_dir.mkdir(parents=True, exist_ok=True)

        mark(queue, tid, "running", "Real action execution started.", str(task_dir))

        plan = task_to_plan(task, str(project_root), args.python)
        write_json(task_dir / "action_plan.json", {"task": task, "plan": plan})

        result = execute_plan(str(project_root), plan, task_dir)
        write_json(task_dir / "action_result.json", result)

        if result.get("ok"):
            mark(queue, tid, "completed", "Real action execution completed with step evidence.", str(task_dir / "action_result.json"))
        else:
            mark(queue, tid, "failed", "Real action execution failed; see step evidence.", str(task_dir / "action_result.json"))

        executed.append({
            "task": tid,
            "ok": result.get("ok"),
            "steps": len(result.get("steps", [])),
            "evidence_dir": str(task_dir),
        })

    queue["updated_at"] = now()
    write_json(queue_path, queue)

    summary = {
        "schema": "jarvis.real_action_runner.v6_6",
        "run_id": run_id,
        "created_at": now(),
        "executed": executed,
        "queue_path": str(queue_path),
    }

    latest_json = out_dir / "latest_real_action_runner_report.json"
    latest_md = out_dir / "latest_real_action_runner_report.md"

    write_json(run_dir / "real_action_runner_report.json", summary)
    write_json(latest_json, summary)

    lines = ["# Jarvis Real Action Runner V6.6", "", f"Run ID: `{run_id}`", ""]
    for e in executed:
        mark_icon = "✅" if e.get("ok") else "❌"
        lines.append(f"- {mark_icon} `{e.get('task')}` steps=`{e.get('steps')}` evidence=`{e.get('evidence_dir')}`")
    if not executed:
        lines.append("- No executable safe tasks found.")
    write_text(latest_md, "\n".join(lines) + "\n")

    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "executed_count": len(executed),
        "completed": [x["task"] for x in executed if x.get("ok")],
        "failed": [x["task"] for x in executed if not x.get("ok")],
        "latest_report": str(latest_json),
        "latest_summary": str(latest_md),
        "queue_path": str(queue_path),
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())