import argparse
import json
import socket
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def run_cmd(cmd, cwd, timeout=120):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout, shell=False)
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout[-8000:],
            "stderr": p.stderr[-8000:],
            "cmd": cmd,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "cmd": cmd}


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def http_get(url, timeout=5):
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(3000).decode("utf-8", errors="replace")
            return {
                "ok": 200 <= r.status < 400,
                "status": r.status,
                "elapsed_ms": int((time.time() - started) * 1000),
                "body_preview": body[:1000],
            }
    except Exception as e:
        return {
            "ok": False,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": f"{type(e).__name__}: {e}",
        }


def port_open(host, port):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except Exception:
        return False


def probe_backend(base_url):
    endpoints = ["/", "/health", "/api/ai/health", "/api/autonomy/health", "/api/provider-routing/health"]
    results = []
    for ep in endpoints:
        results.append({"endpoint": ep, **http_get(base_url.rstrip("/") + ep)})
    ok_count = len([x for x in results if x.get("ok")])
    return {
        "ok_count": ok_count,
        "total": len(results),
        "status": "healthy" if ok_count == len(results) else "partial" if ok_count > 0 else "down",
        "results": results,
    }


def compile_scan(project_root):
    root = Path(project_root)
    files = list((root / "tools").glob("*.py")) + list((root / "app").rglob("*.py"))
    results = []
    for f in files[:300]:
        r = run_cmd(["python", "-m", "py_compile", str(f)], cwd=root, timeout=60)
        results.append({"file": str(f.relative_to(root)), "ok": r.get("ok"), "stderr": r.get("stderr", "")[-1200:]})
    return {
        "ok": all(x["ok"] for x in results),
        "checked": len(results),
        "failed": [x for x in results if not x["ok"]],
    }


def inspect_port(project_root, port):
    return run_cmd([
        "powershell", "-NoProfile", "-Command",
        f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,State,OwningProcess | ConvertTo-Json -Depth 3"
    ], cwd=project_root, timeout=30)


def restart_backend(project_root):
    root = Path(project_root)
    candidates = [
        root / "scripts" / "jarvis_backend_restart_hardened.ps1",
        root / "scripts" / "restart_backend.ps1",
        root / "scripts" / "jarvis_restart_backend.ps1",
    ]
    for script in candidates:
        if script.exists():
            return run_cmd(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], cwd=root, timeout=180)
    return {"ok": False, "error": "no restart script found", "candidates": [str(x) for x in candidates]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:8015")
    ap.add_argument("--auto-restart", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    run_id = "backend_heal_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "schema": "jarvis.backend_self_healing.v8_2",
        "run_id": run_id,
        "created_at": now(),
        "base_url": args.base_url,
        "actions": [],
    }

    report["port_8015_open_before"] = port_open("127.0.0.1", 8015)
    report["port_inspect_before"] = inspect_port(root, 8015)
    report["compile_scan"] = compile_scan(root)
    report["backend_before"] = probe_backend(args.base_url)

    should_restart = report["backend_before"]["status"] == "down"
    should_review = report["backend_before"]["status"] == "partial"

    if should_restart and args.auto_restart:
        restart = restart_backend(root)
        report["actions"].append({"action": "restart_backend", "result": restart})
        time.sleep(5)
        report["backend_after_restart"] = probe_backend(args.base_url)
    elif should_restart:
        report["actions"].append({"action": "restart_recommended_but_not_auto", "reason": "backend_down"})
    elif should_review:
        report["actions"].append({"action": "diagnostic_only", "reason": "backend_partial_not_down"})

    report["port_8015_open_after"] = port_open("127.0.0.1", 8015)
    report["backend_final"] = probe_backend(args.base_url)

    report["verdict"] = (
        "healthy" if report["backend_final"]["status"] == "healthy" and report["compile_scan"]["ok"]
        else "partial_review" if report["backend_final"]["status"] == "partial"
        else "needs_operator"
    )

    write_json(run_dir / "backend_self_healing_report.json", report)
    write_json(out / "latest_backend_self_healing_report.json", report)

    md = [
        "# Jarvis Backend Self-Healing V8.2",
        "",
        f"Run ID: `{run_id}`",
        f"Created: `{report['created_at']}`",
        f"Backend before: `{report['backend_before']['status']}`",
        f"Backend final: `{report['backend_final']['status']}`",
        f"Compile scan OK: `{report['compile_scan']['ok']}`",
        f"Compile checked: `{report['compile_scan']['checked']}`",
        f"Verdict: `{report['verdict']}`",
        "",
        "## Actions"
    ]

    if report["actions"]:
        for a in report["actions"]:
            md.append(f"- `{a.get('action')}` — {a.get('reason', '')}")
    else:
        md.append("- No repair action needed.")

    (out / "latest_backend_self_healing_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "backend_before": report["backend_before"]["status"],
        "backend_final": report["backend_final"]["status"],
        "compile_ok": report["compile_scan"]["ok"],
        "compile_failed_count": len(report["compile_scan"]["failed"]),
        "actions": report["actions"],
        "verdict": report["verdict"],
        "latest_report": str(out / "latest_backend_self_healing_report.json"),
        "latest_summary": str(out / "latest_backend_self_healing_report.md"),
    }, ensure_ascii=False, indent=2))

    return 0 if report["verdict"] in ("healthy", "partial_review") else 2


if __name__ == "__main__":
    raise SystemExit(main())