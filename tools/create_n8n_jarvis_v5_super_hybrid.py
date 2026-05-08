import json
import os
import re
import uuid
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV = ROOT / ".env"

def env(name, default=""):
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else default

N8N_KEY = env("N8N_API_KEY") or env("N8N_CLOUD_API_KEY")
N8N_BASE = (env("N8N_BASE_URL") or env("N8N_CLOUD_URL") or "https://daniliyc.app.n8n.cloud").rstrip("/")
BACKEND = (env("BACKEND_BASE_URL") or "http://127.0.0.1:8015").rstrip("/")
WEBHOOK_PATH = "jarvis-v5-super-hybrid-" + uuid.uuid4().hex[:8]

if not N8N_KEY:
    raise SystemExit("No N8N API key")

def n8n(method, path, body=None):
    data = None
    headers = {
        "X-N8N-API-KEY": N8N_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(N8N_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {raw}") from e

workflow = {
    "name": "Jarvis V5 Super Hybrid Content Factory",
    "nodes": [
        {
            "parameters": {
                "path": WEBHOOK_PATH,
                "httpMethod": "POST",
                "responseMode": "lastNode",
                "options": {}
            },
            "id": "Webhook",
            "name": "V5 Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [0, 0]
        },
        {
            "parameters": {
                "method": "POST",
                "url": BACKEND + "/api/jarvis/v5/content-factory/run",
                "sendBody": True,
                "contentType": "json",
                "jsonBody": "={{ JSON.stringify($json.body || $json) }}",
                "options": {"timeout": 900000}
            },
            "id": "CallJarvis",
            "name": "Call Jarvis V5 Factory",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [320, 0]
        }
    ],
    "connections": {
        "V5 Webhook": {
            "main": [[{"node": "Call Jarvis V5 Factory", "type": "main", "index": 0}]]
        }
    },
    "settings": {"executionOrder": "v1"}
}

created = n8n("POST", "/api/v1/workflows", workflow)
wid = created.get("id")
if not wid:
    print(json.dumps(created, ensure_ascii=False, indent=2))
    raise SystemExit("No workflow id")

try:
    n8n("POST", f"/api/v1/workflows/{wid}/activate")
except Exception as e:
    print("activation_warning:", e)

print("workflow_url:", f"{N8N_BASE}/workflow/{wid}")
print("webhook_url:", f"{N8N_BASE}/webhook/{WEBHOOK_PATH}")