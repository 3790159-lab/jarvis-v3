import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import urllib.request
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


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_file(path, default=None):
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": str(e)}


def run_cmd(cmd, cwd, timeout=60):
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


def file_exists(root, args):
    p = safe_path(root, args["path"])
    return {"ok": True, "exists": p.exists(), "is_file": p.is_file(), "is_dir": p.is_dir(), "path": str(p)}


def list_dir(root, args):
    p = safe_path(root, args.get("path", "."))
    limit = int(args.get("limit", 100))
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": "directory missing", "path": str(p)}
    items = []
    for x in sorted(p.iterdir(), key=lambda z: z.name.lower())[:limit]:
        items.append({
            "name": x.name,
            "kind": "dir" if x.is_dir() else "file",
            "size": x.stat().st_size if x.is_file() else None,
        })
    return {"ok": True, "path": str(p), "items": items}


def grep_search(root, args):
    query = args["query"]
    base = safe_path(root, args.get("path", "."))
    exts = args.get("exts", [".py", ".ps1", ".md", ".json", ".txt"])
    limit = int(args.get("limit", 50))
    results = []
    for fp in base.rglob("*"):
        if len(results) >= limit:
            break
        if not fp.is_file() or fp.suffix.lower() not in exts:
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
            if query.lower() in text.lower():
                idx = text.lower().find(query.lower())
                results.append({
                    "path": str(fp.relative_to(Path(root))),
                    "preview": text[max(0, idx-160):idx+300],
                })
        except Exception:
            pass
    return {"ok": True, "query": query, "count": len(results), "results": results}


def json_read(root, args):
    p = safe_path(root, args["path"])
    return {"ok": p.exists(), "path": str(p), "data": read_json_file(p, None)}


def json_write(root, args):
    p = safe_path(root, args["path"])
    data = args.get("data", {})
    write_json(p, data)
    return {"ok": True, "path": str(p)}


def json_patch(root, args):
    p = safe_path(root, args["path"])
    data = read_json_file(p, {})
    if not isinstance(data, dict):
        return {"ok": False, "error": "json root is not object"}
    patch = args.get("patch", {})
    data.update(patch)
    write_json(p, data)
    return {"ok": True, "path": str(p), "patched_keys": list(patch.keys())}


def safe_backup(root, args):
    p = safe_path(root, args["path"])
    if not p.exists():
        return {"ok": False, "error": "source missing", "path": str(p)}
    backup_dir = Path(root) / "jarvis_stage3_artifacts" / "backups_v6_7"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = backup_dir / f"{p.name}.{stamp}.bak"
    out.write_bytes(p.read_bytes())
    return {"ok": True, "source": str(p), "backup": str(out)}


def hash_file(root, args):
    p = safe_path(root, args["path"])
    if not p.exists() or not p.is_file():
        return {"ok": False, "error": "file missing", "path": str(p)}
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"ok": True, "path": str(p), "sha256": h, "size": p.stat().st_size}


def log_tail(root, args):
    p = safe_path(root, args["path"])
    lines = int(args.get("lines", 80))
    if not p.exists():
        return {"ok": False, "error": "log missing", "path": str(p)}
    text = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"ok": True, "path": str(p), "tail": text[-lines:]}


def port_check(root, args):
    host = args.get("host", "127.0.0.1")
    port = int(args["port"])
    try:
        with socket.create_connection((host, port), timeout=2):
            return {"ok": True, "host": host, "port": port, "open": True}
    except Exception as e:
        return {"ok": True, "host": host, "port": port, "open": False, "error": str(e)}


def process_list(root, args):
    return run_cmd(["powershell", "-NoProfile", "-Command", "Get-Process | Select-Object -First 80 Name,Id,CPU,PM | ConvertTo-Json -Depth 3"], cwd=root)


def env_check(root, args):
    names = args.get("names", [])
    result = {}
    for name in names:
        val = os.environ.get(name)
        result[name] = {
            "exists": val is not None and val != "",
            "preview": None if not val else (val[:4] + "***" + val[-4:] if len(val) > 10 else "***")
        }
    return {"ok": True, "env": result}


def http_post_json(root, args):
    url = args["url"]
    body = json.dumps(args.get("body", {})).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=int(args.get("timeout", 10))) as r:
            text = r.read(5000).decode("utf-8", errors="replace")
            return {"ok": 200 <= r.status < 400, "status": r.status, "body_preview": text[:1500]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def git_status(root, args):
    return run_cmd(["git", "status", "--short"], cwd=root)


def sqlite_probe(root, args):
    p = safe_path(root, args["path"])
    if not p.exists():
        return {"ok": False, "error": "sqlite db missing", "path": str(p)}
    try:
        con = sqlite3.connect(str(p))
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [x[0] for x in cur.fetchall()]
        con.close()
        return {"ok": True, "path": str(p), "tables": tables}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "path": str(p)}


TOOLS = {
    "file_exists": file_exists,
    "list_dir": list_dir,
    "grep_search": grep_search,
    "json_read": json_read,
    "json_write": json_write,
    "json_patch": json_patch,
    "safe_backup": safe_backup,
    "hash_file": hash_file,
    "log_tail": log_tail,
    "port_check": port_check,
    "process_list": process_list,
    "env_check": env_check,
    "http_post_json": http_post_json,
    "git_status": git_status,
    "sqlite_probe": sqlite_probe,
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
        ("file_exists", {"path": "app/main.py"}),
        ("list_dir", {"path": "scripts", "limit": 10}),
        ("grep_search", {"path": ".", "query": "FastAPI", "limit": 10}),
        ("json_read", {"path": "state/jarvis_brain/action_queue_v6_4.json"}),
        ("json_write", {"path": "jarvis_stage3_artifacts/tool_registry_v6_7/test_write.json", "data": {"ok": True, "time": now()}}),
        ("json_patch", {"path": "jarvis_stage3_artifacts/tool_registry_v6_7/test_write.json", "patch": {"patched": True}}),
        ("safe_backup", {"path": "jarvis_stage3_artifacts/tool_registry_v6_7/test_write.json"}),
        ("hash_file", {"path": "jarvis_stage3_artifacts/tool_registry_v6_7/test_write.json"}),
        ("port_check", {"port": 8015}),
        ("process_list", {}),
        ("env_check", {"names": ["APP_PORT", "BACKEND_BASE_URL", "LLM_MODE"]}),
        ("git_status", {}),
    ]

    results = []
    for name, args in tests:
        res = invoke(root, name, args)
        results.append({"tool": name, "args": args, "result": res, "ok": bool(res.get("ok"))})

    report = {
        "schema": "jarvis.tool_registry.v6_7",
        "created_at": now(),
        "tools": sorted(TOOLS),
        "smoke": results,
        "passed": len([x for x in results if x["ok"]]),
        "total": len(results),
    }

    write_json(out / "latest_tool_registry_smoke.json", report)

    lines = ["# Jarvis Tool Registry V6.7 Smoke", "", f"Created: `{now()}`", "", f"Passed: `{report['passed']}/{report['total']}`", ""]
    for r in results:
        icon = "✅" if r["ok"] else "❌"
        lines.append(f"- {icon} `{r['tool']}`")
    (out / "latest_tool_registry_smoke.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "tools_count": len(TOOLS),
        "passed": report["passed"],
        "total": report["total"],
        "latest_json": str(out / "latest_tool_registry_smoke.json"),
        "latest_md": str(out / "latest_tool_registry_smoke.md"),
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

    data = json.loads(args.args_json)
    res = invoke(args.project_root, args.tool, data)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())