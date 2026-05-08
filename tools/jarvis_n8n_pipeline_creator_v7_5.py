import argparse
import json
from datetime import datetime
from pathlib import Path


def now():
    return datetime.now().isoformat(timespec="seconds")


def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def workflow_template(name, webhook_path, description):
    return {
        "name": name,
        "nodes": [
            {
                "parameters": {
                    "path": webhook_path,
                    "httpMethod": "POST",
                    "responseMode": "responseNode"
                },
                "id": "jarvis_webhook",
                "name": "Jarvis Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [260, 300]
            },
            {
                "parameters": {
                    "jsCode": (
                        "const body = $json.body || $json;\\n"
                        "return [{ json: {\\n"
                        "  ok: true,\\n"
                        "  source: 'jarvis',\\n"
                        "  received_at: new Date().toISOString(),\\n"
                        "  description: '" + description.replace("'", "\\'") + "',\\n"
                        "  payload: body\\n"
                        "} }];"
                    )
                },
                "id": "jarvis_normalize",
                "name": "Normalize Payload",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [520, 300]
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ $json }}"
                },
                "id": "jarvis_response",
                "name": "Respond",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1,
                "position": [780, 300]
            }
        ],
        "connections": {
            "Jarvis Webhook": {
                "main": [[{"node": "Normalize Payload", "type": "main", "index": 0}]]
            },
            "Normalize Payload": {
                "main": [[{"node": "Respond", "type": "main", "index": 0}]]
            }
        },
        "settings": {},
        "staticData": None,
        "pinData": {},
        "versionId": None,
        "triggerCount": 0,
        "tags": []
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out = Path(args.out_dir)
    workflows_dir = out / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)

    specs = [
        ("Jarvis Inbox", "jarvis/inbox", "Receives general Jarvis tasks"),
        ("Jarvis Improvements Log", "jarvis/improvements-log", "Receives improvement logs from Jarvis"),
        ("Jarvis Evidence Intake", "jarvis/evidence-intake", "Receives evidence reports from Jarvis"),
        ("Jarvis Pipeline Request", "jarvis/pipeline-request", "Receives pipeline creation requests")
    ]

    created = []
    for name, path, desc in specs:
        wf = workflow_template(name, path, desc)
        file_path = workflows_dir / (name.lower().replace(" ", "_") + ".json")
        write_json(file_path, wf)
        created.append({
            "name": name,
            "webhook_path": path,
            "file": str(file_path)
        })

    manifest = {
        "schema": "jarvis.n8n_pipeline_creator.v7_5",
        "created_at": now(),
        "created_count": len(created),
        "workflows": created,
        "import_note": "Import these JSON files manually into n8n UI, or wire API import later when n8n API key is configured.",
    }

    write_json(out / "latest_n8n_pipeline_manifest.json", manifest)

    md = [
        "# Jarvis n8n Pipeline Creator V7.5",
        "",
        f"Created: `{manifest['created_at']}`",
        f"Workflows: `{len(created)}`",
        "",
        "## Created workflows"
    ]

    for item in created:
        md.append(f"- `{item['name']}` → webhook `{item['webhook_path']}`")
        md.append(f"  - file: `{item['file']}`")

    (out / "latest_n8n_pipeline_manifest.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())