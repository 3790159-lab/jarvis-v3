import json
import os
import uuid
from datetime import datetime
from pathlib import Path


ROOT = Path.cwd()
QUEUE = ROOT / "state" / "jarvis_brain" / "action_queue_v6_4.json"
OUT = ROOT / "jarvis_stage3_artifacts" / "full_creator_v8_8_1"


def now():
    return datetime.now().isoformat(timespec="seconds")


def read_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as exc:
        return {
            **default,
            "_read_error": str(exc),
        }


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def slug(text):
    return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")[:80]


def make_product():
    return {
        "name": "Jarvis Mini SaaS Builder",
        "description": "A local product generator that creates idea, design brief, backend plan, n8n workflow plan, GitHub PR plan and improvement backlog.",
        "target_user": "Founder, automation builder, AI operator",
        "value": "Turns a product idea into a ready implementation package with safe execution gates.",
    }


def create_product_artifacts(product):
    run_id = "creator_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:6]
    product_dir = OUT / "products" / run_id
    product_dir.mkdir(parents=True, exist_ok=True)

    write_json(product_dir / "product_spec.json", {
        "schema": "jarvis.full_creator.product.v8_8_1",
        "created_at": now(),
        **product,
    })

    write_text(product_dir / "design_brief.md", "\n".join([
        "# Design Brief",
        "",
        f"Product: {product['name']}",
        "",
        "Screens:",
        "- Landing page",
        "- Operator dashboard",
        "- Product generator panel",
        "- Execution evidence panel",
        "- GitHub PR status panel",
        "",
        "Style:",
        "- Dark mode",
        "- Large cards",
        "- Clear status indicators",
        "- Founder/operator focused",
    ]))

    write_text(product_dir / "code_plan.md", "\n".join([
        "# Code Plan",
        "",
        "Stack:",
        "- FastAPI backend",
        "- simple HTML/React dashboard",
        "- JSON artifact store",
        "- n8n workflow artifacts",
        "",
        "First implementation:",
        "- Add read-only endpoints",
        "- Generate prototype page",
        "- Add product artifact index",
    ]))

    write_text(product_dir / "github_pr_plan.md", "\n".join([
        "# GitHub PR Plan",
        "",
        "Branch: jarvis/full-creator-product-v8-8-1",
        "PR title: Add FULL CREATOR product package",
        "",
        "Safety:",
        "- no secrets",
        "- no destructive endpoints",
        "- no auto-push without operator approval",
    ]))

    write_text(product_dir / "night_mode_improvement_plan.md", "\n".join([
        "# Night Mode Improvement Plan",
        "",
        "Night Mode should:",
        "- inspect generated product artifacts",
        "- create low-risk improvement tasks",
        "- run evidence checks",
        "- update backlog",
        "- never push without approval",
    ]))

    return product_dir


def add_creator_tasks(queue, product, product_dir):
    existing = {x.get("id") for x in queue.get("items", [])}
    base_id = "full_creator_v8_8_1_" + slug(product["name"])

    tasks = [
        {
            "id": base_id + "_artifact_index",
            "title": "FULL CREATOR: index generated product artifacts",
            "lane": "full_creator",
            "priority": 10,
            "risk": "low",
            "gateway_plan": [
                {"tool": "latest_artifacts", "args": {"path": str(product_dir.relative_to(ROOT)).replace("\\", "/"), "limit": 50}},
                {"tool": "md_report", "args": {
                    "path": "jarvis_stage3_artifacts/full_creator_v8_8_1/reports/product_artifact_index.md",
                    "title": "FULL CREATOR Product Artifact Index",
                    "body": "Generated product artifacts were indexed."
                }}
            ]
        },
        {
            "id": base_id + "_design_review",
            "title": "FULL CREATOR: review design brief",
            "lane": "claude_design",
            "priority": 20,
            "risk": "low",
            "gateway_plan": [
                {"tool": "md_report", "args": {
                    "path": "jarvis_stage3_artifacts/full_creator_v8_8_1/reports/design_review.md",
                    "title": "Design Review",
                    "body": "Review generated design brief and prepare UI implementation checklist."
                }}
            ]
        },
        {
            "id": base_id + "_github_pr_package",
            "title": "FULL CREATOR: prepare GitHub PR package",
            "lane": "github",
            "priority": 30,
            "risk": "low",
            "gateway_plan": [
                {"tool": "md_report", "args": {
                    "path": "jarvis_stage3_artifacts/full_creator_v8_8_1/reports/github_pr_package.md",
                    "title": "GitHub PR Package",
                    "body": "Prepare PR plan and operator-review-only git commands."
                }}
            ]
        }
    ]

    added = []
    for t in tasks:
        if t["id"] in existing:
            continue
        t.update({
            "details": "Generated by FULL CREATOR V8.8.1.",
            "status": "pending",
            "created_at": now(),
            "executor": "gateway_plan_v7_2",
            "evidence_required": True,
        })
        queue.setdefault("items", []).append(t)
        added.append(t["id"])

    return added


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    queue = read_json(QUEUE, {"schema": "jarvis.action_queue.v6_4", "items": []})
    if "items" not in queue or not isinstance(queue["items"], list):
        queue["items"] = []

    product = make_product()
    product_dir = create_product_artifacts(product)
    added = add_creator_tasks(queue, product, product_dir)

    queue["updated_at"] = now()
    queue["guarded_by"] = "full_creator_v8_8_1"
    write_json(QUEUE, queue)

    report = {
        "ok": True,
        "schema": "jarvis.full_creator.v8_8_1",
        "created_at": now(),
        "product": product,
        "product_dir": str(product_dir),
        "queue_added": added,
        "queue_size": len(queue.get("items", [])),
        "queue_path": str(QUEUE),
        "fixed": [
            "utf_8_sig_queue_read",
            "no_bom_queue_write",
            "low_risk_gateway_tasks",
            "operator_review_for_git_push"
        ]
    }

    write_json(OUT / "latest_full_creator_v8_8_1_report.json", report)

    write_text(OUT / "latest_full_creator_v8_8_1_report.md", "\n".join([
        "# FULL CREATOR V8.8.1 Report",
        "",
        f"Created: `{report['created_at']}`",
        f"Product: `{product['name']}`",
        f"Product dir: `{product_dir}`",
        f"Queue added: `{len(added)}`",
        f"Queue size: `{report['queue_size']}`",
        "",
        "## Added tasks",
        *[f"- `{x}`" for x in added],
    ]))

    print(json.dumps({
        "ok": True,
        "product": product["name"],
        "product_dir": str(product_dir),
        "queue_added": added,
        "queue_size": report["queue_size"],
        "latest_report": str(OUT / "latest_full_creator_v8_8_1_report.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()