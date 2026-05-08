import argparse
import csv
import difflib
import json
import os
import platform
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def safe_path(root, rel):
    root = Path(root).resolve()
    p = (root / rel).resolve()
    if not str(p).startswith(str(root)):
        raise ValueError("Path escapes project root")
    return p


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def read_json(path, default=None):
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(read_text(p))
    except Exception as e:
        return {"_error": str(e)}


def run_cmd(cmd, cwd, timeout=90):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout, shell=False)
        return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": p.stdout[-10000:], "stderr": p.stderr[-10000:], "cmd": cmd}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "cmd": cmd}


def copy_file(root, args):
    src = safe_path(root, args["src"])
    dst = safe_path(root, args["dst"])
    if not src.exists() or not src.is_file():
        return {"ok": False, "error": "source missing"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"ok": True, "src": str(src), "dst": str(dst)}


def move_file(root, args):
    src = safe_path(root, args["src"])
    dst = safe_path(root, args["dst"])
    if not src.exists():
        return {"ok": False, "error": "source missing"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"ok": True, "src": str(src), "dst": str(dst)}


def delete_file_safe(root, args):
    p = safe_path(root, args["path"])
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": "file missing"}
    trash = Path(root) / "jarvis_stage3_artifacts" / "safe_trash_v6_8"
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / f"{p.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.deleted"
    shutil.move(str(p), str(dst))
    return {"ok": True, "moved_to": str(dst)}


def make_dir(root, args):
    p = safe_path(root, args["path"])
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(p)}


def file_replace_text(root, args):
    p = safe_path(root, args["path"])
    old = args["old"]
    new = args["new"]
    if not p.exists():
        return {"ok": False, "error": "file missing"}
    text = read_text(p)
    count = text.count(old)
    if count == 0:
        return {"ok": False, "error": "old text not found"}
    backup = Path(root) / "jarvis_stage3_artifacts" / "backups_v6_8" / f"{p.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(text, encoding="utf-8")
    write_text(p, text.replace(old, new, int(args.get("max", count))))
    return {"ok": True, "path": str(p), "replacements": count, "backup": str(backup)}


def file_append(root, args):
    p = safe_path(root, args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(args.get("text", ""))
    return {"ok": True, "path": str(p)}


def diff_files(root, args):
    a = safe_path(root, args["a"])
    b = safe_path(root, args["b"])
    if not a.exists() or not b.exists():
        return {"ok": False, "error": "one of files missing"}
    diff = "\n".join(difflib.unified_diff(read_text(a).splitlines(), read_text(b).splitlines(), fromfile=str(a), tofile=str(b), lineterm=""))
    return {"ok": True, "diff_preview": diff[:12000], "lines": len(diff.splitlines())}


def zip_dir(root, args):
    src = safe_path(root, args["src"])
    dst = safe_path(root, args["dst"])
    if not src.exists() or not src.is_dir():
        return {"ok": False, "error": "src dir missing"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for fp in src.rglob("*"):
            if fp.is_file():
                z.write(fp, fp.relative_to(src))
    return {"ok": True, "src": str(src), "zip": str(dst)}


def unzip_safe(root, args):
    src = safe_path(root, args["src"])
    dst = safe_path(root, args["dst"])
    if not src.exists():
        return {"ok": False, "error": "zip missing"}
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src, "r") as z:
        for member in z.namelist():
            target = (dst / member).resolve()
            if not str(target).startswith(str(dst.resolve())):
                return {"ok": False, "error": "unsafe zip path"}
        z.extractall(dst)
    return {"ok": True, "src": str(src), "dst": str(dst)}


def jsonl_append(root, args):
    p = safe_path(root, args["path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    row = args.get("row", {})
    row.setdefault("time", now())
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True, "path": str(p)}


def jsonl_tail(root, args):
    p = safe_path(root, args["path"])
    if not p.exists():
        return {"ok": False, "error": "jsonl missing"}
    n = int(args.get("lines", 20))
    rows = p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    parsed = []
    for line in rows:
        try:
            parsed.append(json.loads(line))
        except Exception:
            parsed.append({"raw": line})
    return {"ok": True, "rows": parsed}


def csv_read(root, args):
    p = safe_path(root, args["path"])
    if not p.exists():
        return {"ok": False, "error": "csv missing"}
    limit = int(args.get("limit", 50))
    with p.open("r", encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))[:limit]
    return {"ok": True, "rows": rows, "count": len(rows)}


def csv_write(root, args):
    p = safe_path(root, args["path"])
    rows = args.get("rows", [])
    if not rows:
        return {"ok": False, "error": "rows empty"}
    p.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return {"ok": True, "path": str(p), "rows": len(rows)}


def system_info(root, args):
    return {"ok": True, "platform": platform.platform(), "python": platform.python_version(), "cwd": str(root)}


def disk_usage(root, args):
    target = safe_path(root, args.get("path", "."))
    total, used, free = shutil.disk_usage(target)
    return {"ok": True, "path": str(target), "total_gb": round(total/1e9, 2), "used_gb": round(used/1e9, 2), "free_gb": round(free/1e9, 2)}


def python_import_check(root, args):
    modules = args.get("modules", [])
    results = {}
    for m in modules:
        r = run_cmd(["python", "-c", f"import {m}; print('ok')"], cwd=root, timeout=30)
        results[m] = r.get("ok")
    return {"ok": True, "modules": results}


def pip_freeze(root, args):
    return run_cmd(["python", "-m", "pip", "freeze"], cwd=root, timeout=60)


def requirements_check(root, args):
    p = safe_path(root, args.get("path", "requirements.txt"))
    if not p.exists():
        return {"ok": False, "error": "requirements missing"}
    lines = [x.strip() for x in read_text(p).splitlines() if x.strip() and not x.strip().startswith("#")]
    return {"ok": True, "path": str(p), "count": len(lines), "requirements": lines[:200]}


def pytest_run(root, args):
    target = args.get("target", "tests")
    cmd = ["python", "-m", "pytest", target, "-q"]
    return run_cmd(cmd, cwd=root, timeout=int(args.get("timeout", 180)))


def uvicorn_smoke(root, args):
    base = args.get("base_url", "http://127.0.0.1:8015")
    import urllib.request
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/health", timeout=5) as r:
            text = r.read(3000).decode("utf-8", errors="replace")
            return {"ok": 200 <= r.status < 400, "status": r.status, "body_preview": text[:1000]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def endpoint_batch_probe(root, args):
    import urllib.request
    base = args.get("base_url", "http://127.0.0.1:8015").rstrip("/")
    endpoints = args.get("endpoints", ["/health"])
    results = []
    for ep in endpoints:
        url = ep if ep.startswith("http") else base + ep
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                results.append({"url": url, "ok": 200 <= r.status < 400, "status": r.status})
        except Exception as e:
            results.append({"url": url, "ok": False, "error": str(e)})
    return {"ok": any(x["ok"] for x in results), "results": results}


def health_diff(root, args):
    before = safe_path(root, args["before"])
    after = safe_path(root, args["after"])
    b = read_json(before, {})
    a = read_json(after, {})
    return {"ok": True, "before_keys": list(b.keys()) if isinstance(b, dict) else [], "after_keys": list(a.keys()) if isinstance(a, dict) else [], "changed": b != a}


def queue_add_task(root, args):
    qpath = safe_path(root, "state/jarvis_brain/action_queue_v6_4.json")
    q = read_json(qpath, {"items": []})
    task = args.get("task", {})
    if not task.get("id"):
        return {"ok": False, "error": "task.id missing"}
    task.setdefault("status", "pending")
    task.setdefault("priority", 50)
    task.setdefault("risk", "low")
    task.setdefault("created_at", now())
    if any(x.get("id") == task["id"] for x in q.get("items", [])):
        return {"ok": True, "note": "task already exists", "id": task["id"]}
    q.setdefault("items", []).append(task)
    q["updated_at"] = now()
    write_json(qpath, q)
    return {"ok": True, "id": task["id"], "queue": str(qpath)}


def queue_update_task(root, args):
    qpath = safe_path(root, "state/jarvis_brain/action_queue_v6_4.json")
    q = read_json(qpath, {"items": []})
    tid = args["id"]
    patch = args.get("patch", {})
    for item in q.get("items", []):
        if item.get("id") == tid:
            item.update(patch)
            item["updated_at"] = now()
            write_json(qpath, q)
            return {"ok": True, "id": tid, "patched": list(patch.keys())}
    return {"ok": False, "error": "task not found"}


def create_patch_plan(root, args):
    path = safe_path(root, args["path"])
    old = args.get("old", "")
    new = args.get("new", "")
    if not path.exists():
        return {"ok": False, "error": "file missing"}
    text = read_text(path)
    proposed = text.replace(old, new, 1) if old else text + new
    diff = "\n".join(difflib.unified_diff(text.splitlines(), proposed.splitlines(), fromfile="current", tofile="proposed", lineterm=""))
    out = Path(root) / "jarvis_stage3_artifacts" / "patch_plans_v6_8" / f"patch_{path.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.diff"
    write_text(out, diff)
    return {"ok": True, "patch_plan": str(out), "diff_preview": diff[:8000]}


TOOLS = {
    "copy_file": copy_file,
    "move_file": move_file,
    "delete_file_safe": delete_file_safe,
    "make_dir": make_dir,
    "file_replace_text": file_replace_text,
    "file_append": file_append,
    "diff_files": diff_files,
    "zip_dir": zip_dir,
    "unzip_safe": unzip_safe,
    "jsonl_append": jsonl_append,
    "jsonl_tail": jsonl_tail,
    "csv_read": csv_read,
    "csv_write": csv_write,
    "system_info": system_info,
    "disk_usage": disk_usage,
    "python_import_check": python_import_check,
    "pip_freeze": pip_freeze,
    "requirements_check": requirements_check,
    "pytest_run": pytest_run,
    "uvicorn_smoke": uvicorn_smoke,
    "endpoint_batch_probe": endpoint_batch_probe,
    "health_diff": health_diff,
    "queue_add_task": queue_add_task,
    "queue_update_task": queue_update_task,
    "create_patch_plan": create_patch_plan,
}


def invoke(root, tool, args):
    if tool not in TOOLS:
        return {"ok": False, "error": f"unknown tool: {tool}", "available_tools": sorted(TOOLS)}
    try:
        return TOOLS[tool](root, args or {})
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def smoke(root, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    tests = [
        ("system_info", {}),
        ("disk_usage", {"path": "."}),
        ("make_dir", {"path": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke"}),
        ("csv_write", {"path": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test.csv", "rows": [{"a": "1", "b": "2"}]}),
        ("csv_read", {"path": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test.csv"}),
        ("jsonl_append", {"path": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test.jsonl", "row": {"event": "smoke"}}),
        ("jsonl_tail", {"path": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test.jsonl"}),
        ("copy_file", {"src": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test.csv", "dst": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test_copy.csv"}),
        ("diff_files", {"a": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test.csv", "b": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke/test_copy.csv"}),
        ("zip_dir", {"src": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke", "dst": "jarvis_stage3_artifacts/tool_registry_v6_8/smoke.zip"}),
        ("python_import_check", {"modules": ["json", "sqlite3", "pathlib"]}),
        ("uvicorn_smoke", {"base_url": "http://127.0.0.1:8015"}),
        ("endpoint_batch_probe", {"base_url": "http://127.0.0.1:8015", "endpoints": ["/health", "/api/ai/health"]}),
        ("requirements_check", {"path": "requirements.txt"}),
        ("queue_add_task", {"task": {"id": "v6_8_tool_registry_capability_smoke", "title": "V6.8 tool registry capability smoke", "priority": 30, "risk": "low", "lane": "tools"}}),
    ]

    results = []
    for name, args in tests:
        res = invoke(root, name, args)
        results.append({"tool": name, "args": args, "ok": bool(res.get("ok")), "result": res})

    report = {
        "schema": "jarvis.tool_registry.v6_8",
        "created_at": now(),
        "tools_count": len(TOOLS),
        "tools": sorted(TOOLS),
        "passed": len([x for x in results if x["ok"]]),
        "total": len(results),
        "results": results,
    }

    write_json(out / "latest_tool_registry_v6_8_smoke.json", report)

    lines = ["# Jarvis Tool Registry V6.8 Smoke", "", f"Created: `{now()}`", f"Tools count: `{len(TOOLS)}`", f"Passed: `{report['passed']}/{report['total']}`", ""]
    for r in results:
        icon = "✅" if r["ok"] else "❌"
        lines.append(f"- {icon} `{r['tool']}`")
    write_text(out / "latest_tool_registry_v6_8_smoke.md", "\n".join(lines) + "\n")

    print(json.dumps({
        "ok": True,
        "tools_added": len(TOOLS),
        "passed": report["passed"],
        "total": report["total"],
        "latest_json": str(out / "latest_tool_registry_v6_8_smoke.json"),
        "latest_md": str(out / "latest_tool_registry_v6_8_smoke.md"),
    }, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--tool")
    ap.add_argument("--args-json", default="{}")
    args = ap.parse_args()

    if args.smoke:
        smoke(args.project_root, args.out_dir)
        return 0

    res = invoke(args.project_root, args.tool, json.loads(args.args_json))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())