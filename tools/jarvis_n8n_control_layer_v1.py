import os, json, urllib.request, urllib.error, datetime, sys

BASE = os.environ.get("N8N_BASE_URL", "").rstrip("/")
KEY = os.environ.get("N8N_API_KEY", "")
WID = os.environ.get("N8N_WORKFLOW_ID", "")
JARVIS = os.environ.get("JARVIS_PUBLIC_BASE_URL", "").rstrip("/")
ART = os.path.join(os.getcwd(), "jarvis_stage3_artifacts", "n8n_control_layer")
os.makedirs(ART, exist_ok=True)

if not BASE or not KEY or not WID or not JARVIS:
    raise SystemExit("Missing N8N_BASE_URL, N8N_API_KEY, N8N_WORKFLOW_ID, or JARVIS_PUBLIC_BASE_URL")

def api(method, path, body=None):
    url = BASE + path
    data = None
    headers = {
        "X-N8N-API-KEY": KEY,
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {e.code}\n{detail}") from e

def find_node(workflow, name):
    for n in workflow.get("nodes", []):
        if n.get("name") == name:
            return n
    return None

def set_code(workflow, name, code):
    n = find_node(workflow, name)
    if n:
        n.setdefault("parameters", {})["jsCode"] = code
        return True
    return False

def patch_if(workflow, name, left_value, right_value, typ, op):
    n = find_node(workflow, name)
    if not n:
        return False
    params = n.setdefault("parameters", {})
    params["conditions"] = {
        "options": {
            "caseSensitive": True,
            "leftValue": "",
            "typeValidation": "strict"
        },
        "conditions": [{
            "id": name.lower().replace(" ", "_"),
            "leftValue": left_value,
            "rightValue": right_value,
            "operator": {
                "type": typ,
                "operation": op
            }
        }],
        "combinator": "and"
    }
    params.setdefault("options", {})
    return True

def patch_http_run(workflow, name, retry=False):
    n = find_node(workflow, name)
    if not n:
        return False
    p = n.setdefault("parameters", {})
    p["method"] = "POST"
    p["url"] = JARVIS + "/api/jarvis/v5/content-factory/run"
    p["sendBody"] = True
    p["contentType"] = "json"
    p["options"] = {"timeout": 900000}
    if retry:
        p["jsonBody"] = """={{ {
  prompt: $json.prompt + ', ultra detailed, improved composition, premium lighting, sharp focus',
  style_mode: $json.style_mode || 'luxury_safe',
  image_batch: 2,
  video_enabled: false,
  retry_of: $json.run_id
} }}"""
    else:
        p["jsonBody"] = """={{ {
  prompt: $json.prompt,
  style_mode: $json.style_mode,
  image_batch: $json.image_batch,
  video_enabled: $json.video_enabled,
  quality_target: $json.quality_target,
  destination: $json.destination,
  run_meta: $json.run_meta,
  policy: $json.policy
} }}"""
    return True

NORMALIZE = r'''
const input = $json.body || $json || {};

const prompt = String(input.prompt || input.text || input.message || '').trim();
const style_mode = String(input.style_mode || input.mode || 'luxury_safe').trim();
const image_batch = Number(input.image_batch || input.images || 2);
const video_enabled = Boolean(input.video_enabled || input.video || false);
const quality_target = String(input.quality_target || 'high').trim();
const destination = String(input.destination || 'google_drive').trim();

const run_meta = {
  source: 'n8n_super_hybrid_full',
  received_at: new Date().toISOString(),
  requested_by: input.requested_by || 'operator',
  priority: input.priority || 'normal'
};

if (!prompt) {
  return [{ json: { ok: false, stage: 'normalize', error: 'EMPTY_PROMPT', message: 'prompt/text/message is required', input } }];
}

return [{ json: { ok: true, stage: 'normalized', prompt, style_mode, image_batch, video_enabled, quality_target, destination, run_meta } }];
'''.strip()

POLICY = r'''
const data = $json;

const blockedTerms = ['minor', 'underage', 'child', 'teen nude', 'illegal'];
const text = String(data.prompt || '').toLowerCase();
const blocked = blockedTerms.some(t => text.includes(t));

if (blocked) {
  return [{
    json: {
      ok: false,
      stage: 'policy_gate',
      decision: 'blocked',
      reason: 'Prompt matched forbidden/safety blocked term.',
      prompt: data.prompt,
      run_meta: data.run_meta
    }
  }];
}

let risk = 'low';
if (data.video_enabled) risk = 'medium';
if (data.image_batch > 4) risk = 'medium';

return [{
  json: {
    ...data,
    stage: 'policy_gate',
    policy: {
      decision: 'allow',
      risk,
      checked_at: new Date().toISOString()
    }
  }
}];
'''.strip()

POSTPROCESS = r'''
const r = $json;

const imageUrls = r.image_urls || [];
const videoUrls = r.video_urls || [];
const uploaded = r.uploaded || [];

const score = {
  images_count: imageUrls.length,
  videos_count: videoUrls.length,
  uploaded_count: uploaded.length,
  has_drive: Boolean(r.drive_folder_url),
  has_local_dir: Boolean(r.local_run_dir),
  quality_status: imageUrls.length > 0 ? 'passed_basic_check' : 'needs_retry'
};

const should_retry = imageUrls.length === 0;

return [{
  json: {
    ok: Boolean(r.ok),
    stage: 'postprocess',
    pipeline: r.pipeline || 'jarvis_v5_super_hybrid',
    run_id: r.run_id || null,
    prompt: r.prompt || null,
    style_mode: r.style_mode || null,
    image_urls: imageUrls,
    video_urls: videoUrls,
    drive_folder_url: r.drive_folder_url || null,
    uploaded,
    local_run_dir: r.local_run_dir || null,
    credits_before: r.credits_before || null,
    score,
    should_retry,
    raw: r
  }
}];
'''.strip()

RETRY_POST = r'''
const r = $json;

return [{
  json: {
    ok: true,
    stage: 'retry_postprocess',
    retry_ok: Boolean(r.ok),
    retry_run_id: r.run_id || null,
    image_urls: r.image_urls || [],
    video_urls: r.video_urls || [],
    drive_folder_url: r.drive_folder_url || null,
    uploaded: r.uploaded || [],
    local_run_dir: r.local_run_dir || null,
    raw: r
  }
}];
'''.strip()

REPORT = r'''
const r = $json;
const lines = [];

lines.push('✅ Jarvis V5 Super Hybrid Pipeline completed');
lines.push('');
lines.push(`Run ID: ${r.run_id || r.retry_run_id || 'unknown'}`);
lines.push(`Stage: ${r.stage || 'unknown'}`);

if (r.score) {
  lines.push(`Images: ${r.score.images_count}`);
  lines.push(`Videos: ${r.score.videos_count}`);
  lines.push(`Uploaded: ${r.score.uploaded_count}`);
  lines.push(`Quality: ${r.score.quality_status}`);
}

if (r.image_urls && r.image_urls.length) {
  lines.push('');
  lines.push('Image URLs:');
  for (const u of r.image_urls) lines.push(`- ${u}`);
}

if (r.drive_folder_url) {
  lines.push('');
  lines.push(`Drive folder: ${r.drive_folder_url}`);
}

if (r.local_run_dir) {
  lines.push('');
  lines.push(`Local dir: ${r.local_run_dir}`);
}

return [{ json: { ok: true, final_message: lines.join('\n'), result: r } }];
'''.strip()

ERROR_CODE = r'''
return [{
  json: {
    ok: false,
    stage: $json.stage || 'error',
    error: $json.error || 'REQUEST_REJECTED',
    message: $json.message || $json.reason || 'Request failed validation or policy gate.',
    input: $json
  }
}];
'''.strip()

print("== JARVIS N8N CONTROL LAYER V1 ==")
print("Fetching workflow:", WID)
workflow = api("GET", f"/api/v1/workflows/{WID}")

backup_path = os.path.join(ART, f"workflow_backup_{WID}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(workflow, f, ensure_ascii=False, indent=2)

changes = []

for name, code in [
    ("02 Normalize Request", NORMALIZE),
    ("04 Safety / Policy Gate", POLICY),
    ("07 Postprocess + Quality Check", POSTPROCESS),
    ("10 Retry Postprocess", RETRY_POST),
    ("11 Build Final Report", REPORT),
    ("13 Build Error Response", ERROR_CODE),
]:
    if set_code(workflow, name, code):
        changes.append(f"patched code node: {name}")

if patch_if(workflow, "03 IF Normalized OK", "={{ $json.ok }}", True, "boolean", "equals"):
    changes.append("patched IF: 03")
if patch_if(workflow, "05 IF Policy Allowed", "={{ $json.policy.decision }}", "allow", "string", "equals"):
    changes.append("patched IF: 05")
if patch_if(workflow, "08 IF Needs Retry", "={{ $json.should_retry }}", True, "boolean", "equals"):
    changes.append("patched IF: 08")

if patch_http_run(workflow, "06 Call Jarvis V5 Factory", retry=False):
    changes.append("patched HTTP: 06")
if patch_http_run(workflow, "09 Auto Retry Improved Prompt", retry=True):
    changes.append("patched HTTP: 09")

# Respond nodes
n = find_node(workflow, "12 Respond Success")
if n:
    p = n.setdefault("parameters", {})
    p["respondWith"] = "json"
    p["responseBody"] = "={{ { ok: $json.ok, message: $json.final_message, result: $json.result } }}"
    p.setdefault("options", {})
    changes.append("patched respond: 12")

n = find_node(workflow, "14 Respond Error")
if n:
    p = n.setdefault("parameters", {})
    p["respondWith"] = "json"
    p["responseBody"] = "={{ $json }}"
    p["options"] = {"responseCode": 400}
    changes.append("patched respond: 14")

# Static validation
problems = []
for n in workflow.get("nodes", []):
    s = json.dumps(n, ensure_ascii=False)
    if "{{ ." in s or "const data = ;" in s or "const r = ;" in s or "const input = .body" in s:
        problems.append({"node": n.get("name"), "problem": "broken expression/code residue"})

patch_path = os.path.join(ART, f"workflow_patched_{WID}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
with open(patch_path, "w", encoding="utf-8") as f:
    json.dump(workflow, f, ensure_ascii=False, indent=2)

if problems:
    print("STATIC VALIDATION FAILED")
    print(json.dumps(problems, ensure_ascii=False, indent=2))
    raise SystemExit(2)

payload = {
    "name": workflow.get("name"),
    "nodes": workflow.get("nodes", []),
    "connections": workflow.get("connections", {}),
    "settings": workflow.get("settings", {}),
}

if workflow.get("staticData") is not None:
    payload["staticData"] = workflow.get("staticData")

print("Updating workflow...")
updated = api("PUT", f"/api/v1/workflows/{WID}", payload)

report = {
    "ok": True,
    "workflow_id": WID,
    "workflow_name": workflow.get("name"),
    "changes": changes,
    "backup_path": backup_path,
    "patch_path": patch_path,
    "jarvis_public_base_url": JARVIS,
    "updated_response_keys": list(updated.keys()) if isinstance(updated, dict) else [],
}

report_path = os.path.join(ART, f"n8n_control_report_{WID}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(json.dumps(report, ensure_ascii=False, indent=2))
print("DONE. Open n8n, refresh workflow, then Execute workflow again.")