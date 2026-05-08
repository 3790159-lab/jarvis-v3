import os
import re
import json
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV_PATH = PROJECT_ROOT / ".env"

N8N_BASE = "https://daniliyc.app.n8n.cloud"
WEBHOOK_PATH = "jarvis-real-content-factory-" + uuid.uuid4().hex[:8]

def read_env_value(text: str, names):
    for name in names:
        m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""

env_text = ENV_PATH.read_text(encoding="utf-8", errors="replace")

n8n_key = read_env_value(env_text, ["N8N_API_KEY", "N8N_CLOUD_API_KEY"])
openai_key = read_env_value(env_text, ["OPENAI_API_KEY", "LLM_API_KEY"])

if not n8n_key:
    raise SystemExit("ERROR: N8N_API_KEY or N8N_CLOUD_API_KEY not found in .env")
if not openai_key:
    raise SystemExit("ERROR: OPENAI_API_KEY or LLM_API_KEY not found in .env")

def n8n_request(method, path, body=None):
    url = N8N_BASE + path
    data = None
    headers = {
        "X-N8N-API-KEY": n8n_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {raw}") from e

validate_js = r'''
const raw = $json || {};
const input = raw.body || raw;

const text = input.text_prompt || input.text || input.message || "";
const imageUrl = input.image_url || input.photo_url || "";
const style = input.style || "premium, elegant, cinematic, Instagram-safe";
const language = input.language || "Russian";
const characterPolicy = input.character_policy || "fictional adult 18+ only, no real person imitation";

if (!text && !imageUrl) {
  return [{
    json: {
      ok: false,
      error: "Need text_prompt or image_url",
      received: input
    }
  }];
}

return [{
  json: {
    ok: true,
    source_text: text,
    source_image_url: imageUrl,
    style,
    language,
    character_policy: characterPolicy
  }
}];
'''

build_payload_js = r'''
const j = $json;
if (!j.ok) return [{ json: j }];

const brief = j.source_text || j.source_image_url;

const prompt = `
Create a real ready-to-use content package for an Instagram AI model account.

Rules:
- Fictional adult 18+ character only.
- No real-person imitation.
- No minors.
- No explicit sexual content.
- Premium, stylish, Instagram-safe.
- Language: ${j.language}.
- Style: ${j.style}.
- Character policy: ${j.character_policy}.

User brief:
${brief}

Return STRICT JSON only:
{
  "ok": true,
  "pipeline": "content_factory_real_text",
  "brief_summary": "...",
  "assets_count": 12,
  "assets": [
    {"index":1,"kind":"instagram_post","title":"...","content":"...","visual_prompt":"...","status":"generated"},
    {"index":2,"kind":"instagram_story","title":"...","content":"...","visual_prompt":"...","status":"generated"},
    {"index":3,"kind":"reels_script","title":"...","hook":"...","script":"...","visual_prompt":"...","status":"generated"},
    {"index":4,"kind":"caption","content":"...","status":"generated"},
    {"index":5,"kind":"hashtags","items":["..."],"status":"generated"},
    {"index":6,"kind":"bio_variant","content":"...","status":"generated"},
    {"index":7,"kind":"carousel_plan","slides":[{"slide":1,"text":"...","visual_prompt":"..."}],"status":"generated"},
    {"index":8,"kind":"short_video_prompt","content":"...","status":"generated"},
    {"index":9,"kind":"long_video_prompt","content":"...","status":"generated"},
    {"index":10,"kind":"telegram_post","content":"...","status":"generated"},
    {"index":11,"kind":"x_twitter_post","content":"...","status":"generated"},
    {"index":12,"kind":"google_drive_manifest","folders":["..."],"files":["..."],"status":"generated"}
  ],
  "google_drive": {
    "status": "ready_for_upload_node",
    "quality_policy": "upload original files and generated assets without recompression"
  }
}
`;

return [{
  json: {
    ok: true,
    openai_payload: {
      model: "gpt-4.1-mini",
      input: prompt,
      temperature: 0.7
    }
  }
}];
'''

parse_js = r'''
const response = $json;
let text = "";

if (response.output_text) {
  text = response.output_text;
} else if (Array.isArray(response.output)) {
  for (const item of response.output) {
    if (Array.isArray(item.content)) {
      for (const c of item.content) {
        if (c.text) text += c.text;
      }
    }
  }
}

text = (text || "").trim();
text = text.replace(/^```json/i, "").replace(/^```/i, "").replace(/```$/i, "").trim();

try {
  const parsed = JSON.parse(text);
  return [{
    json: {
      ...parsed,
      provider_response_id: response.id || null,
      generated_at: new Date().toISOString()
    }
  }];
} catch (e) {
  return [{
    json: {
      ok: false,
      pipeline: "content_factory_real_text",
      error: "OpenAI response was not valid JSON",
      raw_text: text,
      provider_response_id: response.id || null
    }
  }];
}
'''

workflow = {
    "name": "Jarvis Content Factory REAL v1.5.2",
    "nodes": [
        {
            "parameters": {
                "path": WEBHOOK_PATH,
                "httpMethod": "POST",
                "responseMode": "responseNode",
                "options": {},
            },
            "id": "Webhook",
            "name": "Communicator Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [0, 0],
        },
        {
            "parameters": {"jsCode": validate_js},
            "id": "ValidateAndPlan",
            "name": "Validate Input + Build Content Plan",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [280, 0],
        },
        {
            "parameters": {"jsCode": build_payload_js},
            "id": "BuildOpenAIPayload",
            "name": "Build OpenAI Payload",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [560, 0],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "https://api.openai.com/v1/responses",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "Authorization", "value": "Bearer " + openai_key},
                        {"name": "Content-Type", "value": "application/json"},
                    ]
                },
                "sendBody": True,
                "contentType": "json",
                "jsonBody": "={{ JSON.stringify($json.openai_payload) }}",
                "options": {},
            },
            "id": "OpenAIRealContent",
            "name": "OpenAI Generate Real Content Pack",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [840, 0],
        },
        {
            "parameters": {"jsCode": parse_js},
            "id": "ParseRealContent",
            "name": "Parse Real Content JSON",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1120, 0],
        },
        {
            "parameters": {
                "respondWith": "json",
                "responseBody": "={{$json}}",
                "options": {},
            },
            "id": "FinalResponse",
            "name": "Final Honest Response",
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1,
            "position": [1400, 0],
        },
    ],
    "connections": {
        "Communicator Webhook": {
            "main": [[{"node": "Validate Input + Build Content Plan", "type": "main", "index": 0}]]
        },
        "Validate Input + Build Content Plan": {
            "main": [[{"node": "Build OpenAI Payload", "type": "main", "index": 0}]]
        },
        "Build OpenAI Payload": {
            "main": [[{"node": "OpenAI Generate Real Content Pack", "type": "main", "index": 0}]]
        },
        "OpenAI Generate Real Content Pack": {
            "main": [[{"node": "Parse Real Content JSON", "type": "main", "index": 0}]]
        },
        "Parse Real Content JSON": {
            "main": [[{"node": "Final Honest Response", "type": "main", "index": 0}]]
        },
    },
    "settings": {"executionOrder": "v1"},
}

print("== CREATE NEW CLEAN WORKFLOW ==")
created = n8n_request("POST", "/api/v1/workflows", workflow)
workflow_id = created.get("id")
if not workflow_id:
    print(json.dumps(created, ensure_ascii=False, indent=2))
    raise SystemExit("ERROR: workflow_id not returned")

workflow_url = f"{N8N_BASE}/workflow/{workflow_id}"
webhook_url = f"{N8N_BASE}/webhook/{WEBHOOK_PATH}"

print("workflow_id:", workflow_id)
print("workflow_url:", workflow_url)
print("webhook_url:", webhook_url)

print("== ACTIVATE ==")
try:
    activated = n8n_request("POST", f"/api/v1/workflows/{workflow_id}/activate")
    print("active:", activated.get("active"))
except Exception as e:
    print("activation warning:", e)

time.sleep(2)

print("== TEST WEBHOOK ==")
payload = {
    "text_prompt": "Create Instagram content pack for a fictional adult 18+ AI model account: luxury lifestyle, travel, premium aesthetic, modern tone",
    "style": "premium, elegant, cinematic, Instagram-safe",
    "language": "Russian",
    "character_policy": "fictional adult 18+ only, no real person imitation",
}

req = urllib.request.Request(
    webhook_url,
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    method="POST",
    headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"},
)

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        print(raw)
except urllib.error.HTTPError as e:
    raw = e.read().decode("utf-8", errors="replace")
    print("WEBHOOK HTTP ERROR:", e.code)
    print(raw)
    raise