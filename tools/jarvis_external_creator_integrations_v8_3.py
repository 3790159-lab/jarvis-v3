import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def run_cmd(cmd, cwd, timeout=60):
    try:
        p = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, timeout=timeout, shell=False)
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout[-5000:],
            "stderr": p.stderr[-5000:],
            "cmd": cmd,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "cmd": cmd}


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def env_present(name):
    val = os.environ.get(name)
    return bool(val and val.strip())


def probe_github(project_root):
    root = Path(project_root)
    git_exists = shutil.which("git") is not None
    gh_exists = shutil.which("gh") is not None

    result = {
        "git_cli_available": git_exists,
        "gh_cli_available": gh_exists,
        "chatgpt_github_connector": {
            "available": True,
            "login": "3790159-lab",
            "known_repositories": [
                "3790159-lab/WEB.INDUSTRIES",
                "3790159-lab/ai-supervisor-backend"
            ],
            "note": "Detected through ChatGPT GitHub connector in this conversation."
        },
        "local_git": {},
    }

    if git_exists:
        result["local_git"]["status"] = run_cmd(["git", "status", "--short"], root)
        result["local_git"]["remote"] = run_cmd(["git", "remote", "-v"], root)
        result["local_git"]["branch"] = run_cmd(["git", "branch", "--show-current"], root)

    if gh_exists:
        result["gh_auth_status"] = run_cmd(["gh", "auth", "status"], root)

    return result


def probe_claude_code(project_root):
    root = Path(project_root)
    claude_exists = shutil.which("claude") is not None
    node_exists = shutil.which("node") is not None
    npm_exists = shutil.which("npm") is not None

    return {
        "claude_cli_available": claude_exists,
        "node_available": node_exists,
        "npm_available": npm_exists,
        "anthropic_api_key_present": env_present("ANTHROPIC_API_KEY"),
        "claude_code_mode": "cli_ready" if claude_exists else "handoff_package_only",
        "claude_version": run_cmd(["claude", "--version"], root) if claude_exists else None,
        "policy": {
            "auto_execute_code_changes": False,
            "safe_mode": True,
            "approval_required_for": [
                "external code execution",
                "repo push",
                "dependency install",
                "backend restart",
                "credential changes"
            ]
        }
    }


def probe_claude_design(project_root):
    return {
        "claude_design_mode": "design_brief_and_html_package",
        "figma_token_present": env_present("FIGMA_TOKEN"),
        "design_exports_supported": [
            "design brief",
            "UI component spec",
            "HTML prototype",
            "CSS tokens",
            "implementation checklist"
        ],
        "policy": {
            "no_external_design_write_without_approval": True,
            "no_secret_export": True
        }
    }


def create_handoff_package(project_root, out_dir):
    root = Path(project_root)
    out = Path(out_dir)
    package_dir = out / "handoff_packages" / ("creator_handoff_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    package_dir.mkdir(parents=True, exist_ok=True)

    claude_code_md = """# Claude Code Handoff — Jarvis

## Goal
Improve Jarvis Operator Dashboard and backend autonomy safely.

## Current architecture
- FastAPI backend
- Jarvis tool gateway
- Night Mode
- Evidence / Truth Guard
- Auto Task Generator
- n8n workflow artifact creator
- FULL CREATOR product package

## Safe tasks for Claude Code
1. Add read-only dashboard API endpoints.
2. Add artifact index endpoint.
3. Add queue status endpoint.
4. Add latest evidence endpoint.
5. Add tests for those endpoints.
6. Avoid destructive actions.

## Rules
- Do not print secrets.
- Do not overwrite env files.
- Do not push to GitHub automatically.
- Create patches and reports first.
"""

    claude_design_md = """# Claude Design Handoff — Jarvis Operator Dashboard

## Product
Jarvis Operator Dashboard

## Design goal
Modern, local, operator-focused dashboard for autonomous AI workflows.

## Screens
1. Health overview
2. Queue status
3. Night Mode runs
4. Evidence reports
5. n8n workflows
6. FULL CREATOR products

## Style
- Modern dark/light neutral interface
- Large status cards
- Clear risk labels
- Operator-first layout
- No clutter

## Deliverables
- UI structure
- Component list
- Color tokens
- HTML prototype
- Implementation notes
"""

    github_plan_md = """# GitHub Integration Plan — Jarvis

## Confirmed ChatGPT GitHub connector
- Login: 3790159-lab
- Repositories seen:
  - 3790159-lab/WEB.INDUSTRIES
  - 3790159-lab/ai-supervisor-backend

## Local goals
1. Detect git remote.
2. Prepare safe commit package.
3. Create PR plan, not direct push.
4. Keep secrets out of repo.
5. Use operator review before write actions.

## Recommended target repository
3790159-lab/ai-supervisor-backend

## First PR idea
Add Jarvis Operator Dashboard read-only endpoints and artifact viewer.
"""

    write_text(package_dir / "claude_code_handoff.md", claude_code_md)
    write_text(package_dir / "claude_design_handoff.md", claude_design_md)
    write_text(package_dir / "github_integration_plan.md", github_plan_md)

    demo_html = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Jarvis Operator Dashboard — Design Draft</title>
<style>
body{font-family:Arial,sans-serif;background:#0f172a;color:#e5e7eb;margin:0}
header{padding:28px;background:#111827}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px;padding:20px}
.card{background:#1f2937;border-radius:18px;padding:18px;box-shadow:0 8px 24px rgba(0,0,0,.25)}
.ok{color:#22c55e}.warn{color:#f59e0b}.bad{color:#ef4444}
</style>
</head>
<body>
<header><h1>Jarvis Operator Dashboard</h1><p>Autonomy, evidence, tools, n8n, FULL CREATOR.</p></header>
<main>
<section class="card"><h2>Backend</h2><p class="warn">Partial / Review</p></section>
<section class="card"><h2>Evidence</h2><p class="ok">Passed with evidence</p></section>
<section class="card"><h2>Tool Gateway</h2><p class="ok">98 tools</p></section>
<section class="card"><h2>Night Mode</h2><p class="ok">Active</p></section>
<section class="card"><h2>n8n</h2><p class="ok">Pipeline artifacts ready</p></section>
<section class="card"><h2>FULL CREATOR</h2><p class="ok">Product packages generated</p></section>
</main>
</body>
</html>
"""
    write_text(package_dir / "operator_dashboard_design_draft.html", demo_html)

    return package_dir


def add_queue_tasks(project_root, package_dir):
    qpath = Path(project_root) / "state" / "jarvis_brain" / "action_queue_v6_4.json"
    try:
        queue = json.loads(qpath.read_text(encoding="utf-8-sig"))
    except Exception:
        queue = {"schema": "jarvis.action_queue.v6_4", "items": []}

    existing = {x.get("id") for x in queue.get("items", [])}
    tasks = [
        {
            "id": "external_github_connection_audit_v8_3",
            "title": "Audit GitHub local and ChatGPT connector readiness",
            "lane": "github",
            "priority": 12,
            "gateway_plan": [
                {"tool": "config_inventory", "args": {}},
                {"tool": "git_status", "args": {}},
                {"tool": "md_report", "args": {
                    "path": "jarvis_stage3_artifacts/external_creator_integrations_v8_3/github_audit_report.md",
                    "title": "GitHub Connection Audit",
                    "body": "Audit local git/gh readiness and known ChatGPT GitHub connector repositories."
                }}
            ]
        },
        {
            "id": "external_claude_code_handoff_v8_3",
            "title": "Prepare Claude Code implementation handoff",
            "lane": "claude_code",
            "priority": 18,
            "gateway_plan": [
                {"tool": "latest_artifacts", "args": {"path": "jarvis_stage3_artifacts/full_creator_v8_0", "limit": 30}},
                {"tool": "md_report", "args": {
                    "path": "jarvis_stage3_artifacts/external_creator_integrations_v8_3/claude_code_queue_note.md",
                    "title": "Claude Code Handoff Ready",
                    "body": "Claude Code handoff package was prepared by V8.3."
                }}
            ]
        },
        {
            "id": "external_claude_design_handoff_v8_3",
            "title": "Prepare Claude Design dashboard handoff",
            "lane": "claude_design",
            "priority": 20,
            "gateway_plan": [
                {"tool": "md_index", "args": {"path": "jarvis_stage3_artifacts/full_creator_v8_0", "limit": 50}},
                {"tool": "md_report", "args": {
                    "path": "jarvis_stage3_artifacts/external_creator_integrations_v8_3/claude_design_queue_note.md",
                    "title": "Claude Design Handoff Ready",
                    "body": "Claude Design brief and dashboard draft were prepared by V8.3."
                }}
            ]
        }
    ]

    added = []
    for t in tasks:
        if t["id"] in existing:
            continue
        t.update({
            "details": "Generated by External Creator Integrations V8.3.",
            "risk": "low",
            "status": "pending",
            "created_at": now(),
            "executor": "gateway_plan_v7_2",
            "evidence_required": True,
        })
        queue.setdefault("items", []).append(t)
        added.append(t["id"])

    queue["updated_at"] = now()
    write_json(qpath, queue)
    return added, len(queue.get("items", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--add-queue", action="store_true")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    github = probe_github(args.project_root)
    claude_code = probe_claude_code(args.project_root)
    claude_design = probe_claude_design(args.project_root)
    package_dir = create_handoff_package(args.project_root, out)

    added = []
    queue_size = None
    if args.add_queue:
        added, queue_size = add_queue_tasks(args.project_root, package_dir)

    report = {
        "schema": "jarvis.external_creator_integrations.v8_3",
        "created_at": now(),
        "github": github,
        "claude_code": claude_code,
        "claude_design": claude_design,
        "handoff_package": str(package_dir),
        "queue_added": added,
        "queue_size": queue_size,
        "status": {
            "github_chatgpt_connector_ready": True,
            "github_local_git_ready": github.get("git_cli_available"),
            "claude_code_ready": claude_code.get("claude_cli_available") or claude_code.get("anthropic_api_key_present"),
            "claude_design_package_ready": True
        }
    }

    write_json(out / "latest_external_creator_integrations_report.json", report)

    md = [
        "# Jarvis External Creator Integrations V8.3",
        "",
        f"Created: `{report['created_at']}`",
        "",
        "## GitHub",
        f"- ChatGPT connector: `{report['status']['github_chatgpt_connector_ready']}`",
        f"- Login: `3790159-lab`",
        f"- Local git CLI: `{github.get('git_cli_available')}`",
        f"- Local gh CLI: `{github.get('gh_cli_available')}`",
        "",
        "## Claude Code",
        f"- Claude CLI: `{claude_code.get('claude_cli_available')}`",
        f"- ANTHROPIC_API_KEY present: `{claude_code.get('anthropic_api_key_present')}`",
        f"- Mode: `{claude_code.get('claude_code_mode')}`",
        "",
        "## Claude Design",
        f"- Package mode: `{claude_design.get('claude_design_mode')}`",
        f"- Figma token present: `{claude_design.get('figma_token_present')}`",
        "",
        "## Handoff package",
        f"`{package_dir}`",
        "",
        "## Queue",
        f"- Added: `{len(added)}`",
        f"- Queue size: `{queue_size}`"
    ]

    write_text(out / "latest_external_creator_integrations_report.md", "\n".join(md) + "\n")

    print(json.dumps({
        "ok": True,
        "github_chatgpt_connector_ready": True,
        "github_login": "3790159-lab",
        "known_repositories": ["3790159-lab/WEB.INDUSTRIES", "3790159-lab/ai-supervisor-backend"],
        "github_local_git_ready": github.get("git_cli_available"),
        "github_local_gh_ready": github.get("gh_cli_available"),
        "claude_cli_available": claude_code.get("claude_cli_available"),
        "anthropic_api_key_present": claude_code.get("anthropic_api_key_present"),
        "claude_code_mode": claude_code.get("claude_code_mode"),
        "claude_design_package_ready": True,
        "handoff_package": str(package_dir),
        "queue_added": added,
        "queue_size": queue_size,
        "latest_report": str(out / "latest_external_creator_integrations_report.json"),
        "latest_summary": str(out / "latest_external_creator_integrations_report.md"),
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())