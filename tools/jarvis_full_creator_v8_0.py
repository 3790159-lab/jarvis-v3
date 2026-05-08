import argparse
import json
import re
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def slugify(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9а-яіїєґё]+", "-", text, flags=re.I)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60] or "jarvis-product"


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def default_idea():
    return "AI Operator Dashboard for small teams: a local web panel that monitors backend health, queues, artifacts, n8n pipelines and auto-improvement tasks."


def build_spec(idea):
    return {
        "schema": "jarvis.full_creator.product_spec.v8_0",
        "created_at": now(),
        "idea": idea,
        "product_name": "Jarvis Operator Dashboard",
        "target_user": "Solo founder or small technical team building autonomous AI workflows.",
        "core_value": "One local dashboard to see what Jarvis is doing, what broke, what was improved, and what should happen next.",
        "mvp_features": [
            "Health overview",
            "Action queue view",
            "Night Mode run history",
            "Tool gateway status",
            "n8n workflow artifact list",
            "Evidence and Truth Guard panel",
            "Generated product backlog"
        ],
        "non_goals": [
            "No external deployment by default",
            "No secret exposure",
            "No destructive actions without operator review"
        ],
        "success_metrics": [
            "Backend status visible in under 5 seconds",
            "Latest Night Mode run visible",
            "Queue status visible",
            "Artifacts indexed",
            "Operator can decide next action quickly"
        ],
        "risk_policy": {
            "auto_allowed": ["read files", "generate reports", "create artifacts", "safe backups", "health probes"],
            "operator_review": ["delete", "restart", "external API write", "credential changes", "large refactors"]
        }
    }


def build_demo_html(spec):
    features = "\n".join([f"<li>{x}</li>" for x in spec["mvp_features"]])
    metrics = "\n".join([f"<li>{x}</li>" for x in spec["success_metrics"]])

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{spec['product_name']}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      background: #f6f7fb;
      color: #172033;
    }}
    header {{
      padding: 32px;
      background: #111827;
      color: white;
    }}
    main {{
      padding: 24px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 18px;
    }}
    .card {{
      background: white;
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 8px 24px rgba(0,0,0,.08);
    }}
    .badge {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: #e8f5ee;
      color: #137333;
      font-weight: 700;
      font-size: 12px;
    }}
    h1 {{ margin: 0 0 10px; }}
    h2 {{ margin-top: 0; }}
    li {{ margin-bottom: 8px; }}
  </style>
</head>
<body>
  <header>
    <div class="badge">Jarvis FULL CREATOR V8.0</div>
    <h1>{spec['product_name']}</h1>
    <p>{spec['core_value']}</p>
  </header>
  <main>
    <section class="card">
      <h2>Idea</h2>
      <p>{spec['idea']}</p>
    </section>
    <section class="card">
      <h2>MVP Features</h2>
      <ul>{features}</ul>
    </section>
    <section class="card">
      <h2>Success Metrics</h2>
      <ul>{metrics}</ul>
    </section>
    <section class="card">
      <h2>Operator Policy</h2>
      <p>Safe actions are automated. Risky actions require review.</p>
    </section>
  </main>
</body>
</html>
"""


def build_roadmap(spec):
    return f"""# {spec['product_name']} — Roadmap

Created: `{now()}`

## Phase 1 — MVP
- Local HTML dashboard
- Backend health widget
- Queue snapshot
- Latest artifacts index
- Night Mode status

## Phase 2 — Real backend integration
- FastAPI route for dashboard data
- JSON API for queue/evidence/artifacts
- Read-only UI first

## Phase 3 — Operator actions
- Run evidence check
- Run gateway smoke
- Generate auto tasks
- Start safe night cycle

## Phase 4 — FULL CREATOR expansion
- Product idea generator
- Design generator
- Code generator
- Launch package generator
- Night Mode improvement loop

## Guardrails
- No destructive action without explicit approval
- No secrets printed
- All actions require evidence artifacts
"""


def build_launch_plan(spec):
    return f"""# Launch Plan — {spec['product_name']}

## Positioning
A local operator cockpit for autonomous Jarvis workflows.

## First users
- You
- Technical founders
- Automation builders
- n8n users
- AI agent experimenters

## MVP launch checklist
- [ ] Generate dashboard artifact
- [ ] Connect backend health endpoint
- [ ] Connect queue status
- [ ] Connect artifact index
- [ ] Add Night Mode summary
- [ ] Add n8n workflow artifact viewer

## Distribution
- Demo video
- GitHub README
- Telegram screenshots
- n8n workflow examples
"""


def build_backlog():
    return {
        "schema": "jarvis.full_creator.backlog.v8_0",
        "created_at": now(),
        "items": [
            {"id": "fc_dash_backend_api", "title": "Create backend API for dashboard data", "priority": 10, "risk": "low"},
            {"id": "fc_dash_html_v1", "title": "Create dashboard HTML v1", "priority": 20, "risk": "low"},
            {"id": "fc_artifact_index_api", "title": "Expose artifact index read-only endpoint", "priority": 30, "risk": "low"},
            {"id": "fc_n8n_import_helper", "title": "Create n8n import helper docs", "priority": 40, "risk": "low"},
            {"id": "fc_operator_actions", "title": "Add safe operator action buttons", "priority": 50, "risk": "medium"},
            {"id": "fc_night_improvement_loop", "title": "Use Night Mode to improve the product", "priority": 60, "risk": "medium"}
        ]
    }


def add_queue_tasks(project_root, product_dir, backlog):
    qpath = Path(project_root) / "state" / "jarvis_brain" / "action_queue_v6_4.json"
    try:
        queue = json.loads(qpath.read_text(encoding="utf-8-sig"))
    except Exception:
        queue = {"schema": "jarvis.action_queue.v6_4", "items": []}

    existing = {x.get("id") for x in queue.get("items", [])}
    added = []

    for item in backlog["items"]:
        tid = "full_creator_" + item["id"]
        if tid in existing:
            continue
        task = {
            "id": tid,
            "title": item["title"],
            "details": "Generated by Jarvis FULL CREATOR V8.0.",
            "priority": item["priority"],
            "risk": item["risk"],
            "lane": "full_creator",
            "status": "pending" if item["risk"] == "low" else "blocked",
            "created_at": now(),
            "executor": "gateway_plan_v7_2",
            "evidence_required": True,
            "gateway_plan": [
                {
                    "tool": "md_report",
                    "args": {
                        "path": str(Path(product_dir).relative_to(project_root) / f"{tid}.md").replace("\\", "/"),
                        "title": item["title"],
                        "body": "FULL CREATOR generated implementation note. Operator can expand this into code task."
                    }
                },
                {
                    "tool": "latest_artifacts",
                    "args": {
                        "path": "jarvis_stage3_artifacts",
                        "limit": 30
                    }
                }
            ]
        }
        queue.setdefault("items", []).append(task)
        added.append(tid)

    queue["updated_at"] = now()
    write_json(qpath, queue)
    return added, len(queue.get("items", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--idea", default="")
    ap.add_argument("--add-queue", action="store_true")
    args = ap.parse_args()

    project_root = Path(args.project_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    idea = args.idea.strip() or default_idea()
    spec = build_spec(idea)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = slugify(spec["product_name"])
    product_dir = out_dir / "products" / f"{slug}_{stamp}"
    product_dir.mkdir(parents=True, exist_ok=True)

    backlog = build_backlog()

    write_json(product_dir / "product_spec.json", spec)
    write_text(product_dir / "idea.md", f"# Product Idea\n\n{idea}\n")
    write_text(product_dir / "roadmap.md", build_roadmap(spec))
    write_text(product_dir / "launch_plan.md", build_launch_plan(spec))
    write_json(product_dir / "improvement_backlog.json", backlog)
    write_text(product_dir / "demo_app.html", build_demo_html(spec))

    memory_dir = project_root / "artifacts" / "memory" / "summaries"
    memory_dir.mkdir(parents=True, exist_ok=True)
    memory_path = memory_dir / f"full_creator_v8_0_{stamp}.md"
    write_text(memory_path, f"# FULL CREATOR V8.0 Product Snapshot\n\nProduct: {spec['product_name']}\n\nIdea: {idea}\n\nArtifacts: `{product_dir}`\n")

    added = []
    queue_size = None
    if args.add_queue:
        added, queue_size = add_queue_tasks(project_root, product_dir, backlog)

    manifest = {
        "schema": "jarvis.full_creator.v8_0",
        "created_at": now(),
        "product_dir": str(product_dir),
        "product_name": spec["product_name"],
        "idea": idea,
        "artifacts": {
            "spec": str(product_dir / "product_spec.json"),
            "idea": str(product_dir / "idea.md"),
            "roadmap": str(product_dir / "roadmap.md"),
            "launch_plan": str(product_dir / "launch_plan.md"),
            "backlog": str(product_dir / "improvement_backlog.json"),
            "demo": str(product_dir / "demo_app.html"),
            "memory": str(memory_path)
        },
        "queue_added": added,
        "queue_size": queue_size
    }

    write_json(out_dir / "latest_full_creator_manifest.json", manifest)

    md = [
        "# Jarvis FULL CREATOR V8.0",
        "",
        f"Created: `{manifest['created_at']}`",
        f"Product: `{spec['product_name']}`",
        f"Product dir: `{product_dir}`",
        "",
        "## Artifacts"
    ]
    for k, v in manifest["artifacts"].items():
        md.append(f"- `{k}`: `{v}`")
    md.append("")
    md.append("## Queue")
    md.append(f"- Added: `{len(added)}`")
    md.append(f"- Queue size: `{queue_size}`")

    write_text(out_dir / "latest_full_creator_manifest.md", "\n".join(md) + "\n")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())