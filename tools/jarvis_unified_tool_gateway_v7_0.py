import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_registry(path):
    p = Path(path)
    if not p.exists():
        return None, f"missing: {p}"
    spec = importlib.util.spec_from_file_location(p.stem, str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "TOOLS") or not hasattr(mod, "invoke"):
        return None, f"invalid registry: {p}"
    return mod, None


def safe_policy(tool_name, args):
    dangerous = {
        "delete_file_safe",
        "move_file",
        "file_replace_text",
        "json_set",
        "json_patch",
        "queue_update_task",
        "queue_mark_review",
        "http_post_json",
        "powershell_script",
        "pytest_run",
        "smoke_all_previous",
    }

    if tool_name in dangerous:
        return {
            "allowed": True,
            "risk": "medium",
            "note": "Allowed through gateway, but evidence is required. Night Mode should use only when task risk is low or operator-approved."
        }

    return {
        "allowed": True,
        "risk": "low",
        "note": "Safe low-risk tool."
    }


def build_catalog(project_root):
    root = Path(project_root)
    registry_paths = {
        "v6_7": root / "tools" / "jarvis_tool_registry_v6_7.py",
        "v6_8": root / "tools" / "jarvis_tool_registry_v6_8.py",
        "v6_9": root / "tools" / "jarvis_tool_registry_v6_9.py",
    }

    catalog = {}
    errors = {}

    for registry_name, path in registry_paths.items():
        mod, err = load_registry(path)
        if err:
            errors[registry_name] = err
            continue

        for tool_name in sorted(mod.TOOLS.keys()):
            if tool_name not in catalog:
                catalog[tool_name] = {
                    "tool": tool_name,
                    "registry": registry_name,
                    "path": str(path),
                    "policy": safe_policy(tool_name, {}),
                }

    return catalog, errors


def invoke_tool(project_root, out_dir, tool_name, args):
    catalog, errors = build_catalog(project_root)

    run_id = "tool_call_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(out_dir) / "calls" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if tool_name not in catalog:
        result = {
            "ok": False,
            "error": "tool_not_found",
            "tool": tool_name,
            "available_count": len(catalog),
            "available_preview": sorted(catalog.keys())[:150],
            "registry_errors": errors,
        }
        write_json(run_dir / "result.json", result)
        return result

    meta = catalog[tool_name]
    policy = safe_policy(tool_name, args)

    if not policy["allowed"]:
        result = {
            "ok": False,
            "error": "blocked_by_policy",
            "tool": tool_name,
            "policy": policy,
        }
        write_json(run_dir / "result.json", result)
        return result

    mod, err = load_registry(meta["path"])
    if err:
        result = {
            "ok": False,
            "error": err,
            "tool": tool_name,
            "registry": meta["registry"],
        }
        write_json(run_dir / "result.json", result)
        return result

    try:
        raw = mod.invoke(project_root, tool_name, args or {})
    except Exception as e:
        raw = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }

    result = {
        "ok": bool(raw.get("ok")),
        "created_at": now(),
        "run_id": run_id,
        "tool": tool_name,
        "registry": meta["registry"],
        "policy": policy,
        "args": args,
        "result": raw,
        "evidence_dir": str(run_dir),
    }

    write_json(run_dir / "result.json", result)
    write_json(Path(out_dir) / "latest_tool_call.json", result)

    journal = Path(out_dir) / "tool_gateway_journal.jsonl"
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": now(),
            "tool": tool_name,
            "registry": meta["registry"],
            "ok": result["ok"],
            "risk": policy["risk"],
            "evidence_dir": str(run_dir),
        }, ensure_ascii=False) + "\n")

    return result


def smoke(project_root, out_dir):
    catalog, errors = build_catalog(project_root)

    tests = [
        ("file_exists", {"path": "app/main.py"}),
        ("list_dir", {"path": "scripts", "limit": 5}),
        ("system_info", {}),
        ("disk_usage", {"path": "."}),
        ("echo", {"message": "gateway-ok"}),
        ("timestamp", {}),
        ("health_matrix", {}),
        ("queue_summary", {}),
        ("capability_manifest", {}),
    ]

    results = []
    for tool, args in tests:
        res = invoke_tool(project_root, out_dir, tool, args)
        results.append({
            "tool": tool,
            "ok": bool(res.get("ok")),
            "registry": res.get("registry"),
            "evidence_dir": res.get("evidence_dir"),
        })

    report = {
        "schema": "jarvis.unified_tool_gateway.v7_0",
        "created_at": now(),
        "catalog_count": len(catalog),
        "registry_errors": errors,
        "smoke_passed": len([x for x in results if x["ok"]]),
        "smoke_total": len(results),
        "results": results,
        "tools": sorted(catalog.keys()),
    }

    out = Path(out_dir)
    write_json(out / "latest_gateway_smoke.json", report)

    lines = [
        "# Jarvis Unified Tool Gateway V7.0 Smoke",
        "",
        f"Created: `{now()}`",
        f"Catalog count: `{len(catalog)}`",
        f"Smoke: `{report['smoke_passed']}/{report['smoke_total']}`",
        "",
        "## Smoke results"
    ]
    for r in results:
        icon = "✅" if r["ok"] else "❌"
        lines.append(f"- {icon} `{r['tool']}` registry=`{r.get('registry')}`")

    lines.append("")
    lines.append("## Registry errors")
    if errors:
        for k, v in errors.items():
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("- none")

    (out / "latest_gateway_smoke.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--tool")
    ap.add_argument("--args-json", default="{}")
    args = ap.parse_args()

    if args.catalog:
        catalog, errors = build_catalog(args.project_root)
        result = {
            "ok": True,
            "catalog_count": len(catalog),
            "registry_errors": errors,
            "tools": sorted(catalog.keys()),
        }
        write_json(Path(args.out_dir) / "latest_gateway_catalog.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.smoke:
        result = smoke(args.project_root, args.out_dir)
        print(json.dumps({
            "ok": True,
            "catalog_count": result["catalog_count"],
            "smoke_passed": result["smoke_passed"],
            "smoke_total": result["smoke_total"],
            "latest_json": str(Path(args.out_dir) / "latest_gateway_smoke.json"),
            "latest_md": str(Path(args.out_dir) / "latest_gateway_smoke.md"),
        }, ensure_ascii=False, indent=2))
        return 0

    tool_args = json.loads(args.args_json)
    result = invoke_tool(args.project_root, args.out_dir, args.tool, tool_args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())