import argparse
import json
import os
import socket
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


DEFAULT_ENDPOINTS = [
    "/",
    "/health",
    "/api/ai/health",
    "/api/autonomy/health",
    "/api/autonomy/mission-bridge/health",
    "/api/provider-routing/health",
]


N8N_ENDPOINTS = [
    "http://127.0.0.1:5678/healthz",
    "http://127.0.0.1:5678/rest/settings",
]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def safe_json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return json.dumps({"error": "json_dump_failed", "repr": repr(obj)}, ensure_ascii=False, indent=2)


def http_get(url, timeout=5):
    started = time.time()
    result = {
        "url": url,
        "ok": False,
        "status_code": None,
        "elapsed_ms": None,
        "content_type": None,
        "body_preview": None,
        "json": None,
        "error": None,
    }
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "JarvisEvidenceProbe/6.3"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(7000)
            result["status_code"] = getattr(resp, "status", None)
            result["content_type"] = resp.headers.get("content-type")
            text = body.decode("utf-8", errors="replace")
            result["body_preview"] = text[:1500]
            if "json" in (result["content_type"] or "").lower():
                try:
                    result["json"] = json.loads(text)
                except Exception as e:
                    result["json_error"] = str(e)
            result["ok"] = 200 <= int(result["status_code"] or 0) < 400
    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["error"] = f"HTTPError: {e}"
        try:
            result["body_preview"] = e.read(1500).decode("utf-8", errors="replace")
        except Exception:
            pass
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result


def port_open(host, port, timeout=2):
    result = {"host": host, "port": port, "open": False, "error": None}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            result["open"] = True
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def inspect_files(project_root):
    root = Path(project_root)
    targets = [
        "app/main.py",
        "app/services",
        "app/routers",
        "scripts",
        "tools",
        "jarvis_stage3_artifacts",
        "artifacts/memory",
        "state",
    ]

    results = []
    for rel in targets:
        p = root / rel
        item = {
            "relative_path": rel,
            "exists": p.exists(),
            "kind": "directory" if p.is_dir() else "file" if p.is_file() else "missing",
            "size_bytes": None,
            "recent_files": [],
        }
        if p.is_file():
            try:
                item["size_bytes"] = p.stat().st_size
            except Exception:
                pass
        if p.is_dir():
            try:
                files = []
                for fp in p.rglob("*"):
                    if fp.is_file():
                        try:
                            st = fp.stat()
                            files.append({
                                "path": str(fp.relative_to(root)),
                                "size_bytes": st.st_size,
                                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                            })
                        except Exception:
                            pass
                files.sort(key=lambda x: x.get("mtime", ""), reverse=True)
                item["recent_files"] = files[:15]
            except Exception as e:
                item["scan_error"] = str(e)
        results.append(item)
    return results


def classify_endpoint_health(endpoint_results):
    ok = [x for x in endpoint_results if x.get("ok")]
    failed = [x for x in endpoint_results if not x.get("ok")]
    return {
        "total": len(endpoint_results),
        "ok": len(ok),
        "failed": len(failed),
        "status": "healthy" if len(failed) == 0 else "partial" if len(ok) > 0 else "down",
    }


def build_truth_guard_verdict(report):
    backend = report.get("backend", {})
    files = report.get("files", [])
    n8n = report.get("n8n", {})

    backend_status = backend.get("summary", {}).get("status")
    required_files_ok = any(x.get("relative_path") == "app/main.py" and x.get("exists") for x in files)
    artifacts_ok = any(x.get("relative_path") == "jarvis_stage3_artifacts" and x.get("exists") for x in files)

    evidence = []
    gaps = []

    if backend_status in ("healthy", "partial"):
        evidence.append("backend_endpoints_responded")
    else:
        gaps.append("backend_endpoints_not_responding")

    if required_files_ok:
        evidence.append("main_app_file_exists")
    else:
        gaps.append("app_main_missing")

    if artifacts_ok:
        evidence.append("artifact_root_exists")
    else:
        gaps.append("artifact_root_missing")

    if n8n.get("port", {}).get("open"):
        evidence.append("n8n_port_open")
    else:
        gaps.append("n8n_port_closed_or_unavailable")

    passed = backend_status in ("healthy", "partial") and required_files_ok and artifacts_ok

    return {
        "verdict": "passed_with_evidence" if passed else "needs_operator_review",
        "passed": passed,
        "evidence": evidence,
        "gaps": gaps,
        "strict_next_action": "continue_autonomous_low_risk_tasks" if passed else "create_fix_task_or_operator_review",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--timeout", type=int, default=5)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = "evidence_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    endpoints = []
    for ep in DEFAULT_ENDPOINTS:
        endpoints.append(http_get(args.base_url.rstrip("/") + ep, timeout=args.timeout))

    n8n_http = []
    for url in N8N_ENDPOINTS:
        n8n_http.append(http_get(url, timeout=3))

    report = {
        "schema": "jarvis.real_action_evidence.v6_3",
        "run_id": run_id,
        "created_at": now_iso(),
        "project_root": args.project_root,
        "base_url": args.base_url,
        "backend": {
            "summary": classify_endpoint_health(endpoints),
            "endpoints": endpoints,
        },
        "n8n": {
            "port": port_open("127.0.0.1", 5678, timeout=2),
            "http_checks": n8n_http,
        },
        "files": inspect_files(args.project_root),
    }

    report["truth_guard"] = build_truth_guard_verdict(report)

    report_path = run_dir / "real_action_evidence_report.json"
    md_path = run_dir / "real_action_evidence_summary.md"
    latest_json = out_dir / "latest_real_action_evidence_report.json"
    latest_md = out_dir / "latest_real_action_evidence_summary.md"

    text = safe_json(report)
    report_path.write_text(text, encoding="utf-8")
    latest_json.write_text(text, encoding="utf-8")

    md = []
    md.append("# Jarvis Real Action Evidence Report")
    md.append("")
    md.append(f"- Run ID: `{run_id}`")
    md.append(f"- Created: `{report['created_at']}`")
    md.append(f"- Backend status: `{report['backend']['summary']['status']}`")
    md.append(f"- Backend OK endpoints: `{report['backend']['summary']['ok']}/{report['backend']['summary']['total']}`")
    md.append(f"- n8n port open: `{report['n8n']['port']['open']}`")
    md.append(f"- Truth Guard verdict: `{report['truth_guard']['verdict']}`")
    md.append(f"- Strict next action: `{report['truth_guard']['strict_next_action']}`")
    md.append("")
    md.append("## Evidence")
    for e in report["truth_guard"]["evidence"]:
        md.append(f"- ✅ {e}")
    md.append("")
    md.append("## Gaps")
    if report["truth_guard"]["gaps"]:
        for g in report["truth_guard"]["gaps"]:
            md.append(f"- ⚠️ {g}")
    else:
        md.append("- No critical gaps detected.")
    md.append("")
    md.append("## Backend endpoint checks")
    for item in endpoints:
        mark = "✅" if item.get("ok") else "❌"
        md.append(f"- {mark} `{item.get('url')}` status=`{item.get('status_code')}` elapsed_ms=`{item.get('elapsed_ms')}`")
    md.append("")
    md.append("## n8n checks")
    md.append(f"- Port 5678 open: `{report['n8n']['port']['open']}`")
    for item in n8n_http:
        mark = "✅" if item.get("ok") else "❌"
        md.append(f"- {mark} `{item.get('url')}` status=`{item.get('status_code')}` error=`{item.get('error')}`")

    md_text = "\n".join(md) + "\n"
    md_path.write_text(md_text, encoding="utf-8")
    latest_md.write_text(md_text, encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "run_id": run_id,
        "report_path": str(report_path),
        "summary_path": str(md_path),
        "latest_report": str(latest_json),
        "latest_summary": str(latest_md),
        "truth_guard": report["truth_guard"],
        "backend_summary": report["backend"]["summary"],
        "n8n_port_open": report["n8n"]["port"]["open"],
    }, ensure_ascii=False, indent=2))

    return 0 if report["truth_guard"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())