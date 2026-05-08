import os, json, urllib.request, urllib.error, datetime, re

BASE = os.environ["N8N_BASE_URL"].rstrip("/")
KEY = os.environ["N8N_API_KEY"]
JARVIS = os.environ["JARVIS_PUBLIC_BASE_URL"].rstrip("/")
HINT = os.environ.get("WORKFLOW_NAME_HINT", "Jarvis V5 Super Hybrid").lower()

ROOT = os.getcwd()
ART = os.path.join(ROOT, "jarvis_stage3_artifacts", "n8n_full_autonomy_v3_1")
os.makedirs(ART, exist_ok=True)

def ts():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def api(method, path, body=None):
    url = BASE + path
    headers = {
        "X-N8N-API-KEY": KEY,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed HTTP {e.code}: {detail}") from e

def save(name, data):
    path = os.path.join(ART, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path

def pick_workflow():
    res = api("GET", "/api/v1/workflows")
    items = res.get("data", res if isinstance(res, list) else [])

    scored = []
    for w in items:
        name = str(w.get("name", ""))
        low = name.lower()
        score = 0
        for part in HINT.split():
            if part in low:
                score += 20
        for part in ["jarvis", "v5", "super", "hybrid", "content", "factory"]:
            if part in low:
                score += 10
        if w.get("active"):
            score += 2
        scored.append({
            "id": w.get("id"),
            "name": name,
            "active": w.get("active"),
            "updatedAt": w.get("updatedAt"),
            "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    save("workflow_candidates_" + ts() + ".json", scored)

    print("TOP WORKFLOWS:")
    for c in scored[:10]:
        print(f"score={c['score']:>3} id={c['id']} active={c['active']} name={c['name']}")

    if not scored or scored[0]["score"] <= 0:
        raise SystemExit("Could not find target workflow. Use better name hint.")

    return scored[0]["id"], scored[0]

def node(wf, name):
    for n in wf.get("nodes", []):
        if n.get("name") == name:
            return n
    return None

def code(wf, name, js):
    n = node(wf, name)
    if not n:
        return "missing " + name
    n.setdefault("parameters", {})["jsCode"] = js
    return "patched " + name

def ifnode(wf, name, left, right, typ="boolean"):
    n = node(wf, name)
    if not n:
        return "missing " + name
    n.setdefault("parameters", {})["conditions"] = {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
        "conditions": [{
            "id": re.sub(r"[^a-z0-9]+", "_", name.lower()),
            "leftValue": left,
            "rightValue": right,
            "operator": {"type": typ, "operation": "equals"}
        }],
        "combinator": "and"
    }
    return "patched " + name

def httpnode(wf, name, retry=False):
    n = node(wf, name)
    if not n:
        return "missing " + name
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
    return "patched " + name

NORMALIZE = """
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
""".strip()

POLICY = """
const data = $json;
const blockedTerms = ['minor', 'underage', 'child', 'teen nude', 'illegal'];
const text = String(data.prompt || '').toLowerCase();
const blocked = blockedTerms.some(t => text.includes(t));

if (blocked) {
  return [{ json: { ok: false, stage: 'policy_gate', decision: 'blocked', reason: 'Prompt matched forbidden/safety blocked term.', prompt: data.prompt, run_meta: data.run_meta } }];
}

let risk = 'low';
if (data.video_enabled) risk = 'medium';
if (data.image_batch > 4) risk = 'medium';

return [{ json: { ...data, stage: 'policy_gate', policy: { decision: 'allow', risk, checked_at: new Date().toISOString() } } }];
""".strip()

POSTPROCESS = """
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

return [{ json: {
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
} }];
""".strip()

RETRYPOST = """
const r = $json;
return [{ json: {
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
} }];
""".strip()

REPORT = """
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

return [{ json: { ok: true, final_message: lines.join('\\n'), result: r } }];
""".strip()

ERROR = """
return [{ json: {
  ok: false,
  stage: $json.stage || 'error',
  error: $json.error || 'REQUEST_REJECTED',
  message: $json.message || $json.reason || 'Request failed validation or policy gate.',
  input: $json
} }];
""".strip()

def main():
    print("== JARVIS N8N FULL AUTONOMY V3.1 ==")
    print("Base:", BASE)
    print("Jarvis:", JARVIS)
    print("Hint:", HINT)

    wid, candidate = pick_workflow()
    print("SELECTED:", candidate)

    wf = api("GET", "/api/v1/workflows/" + wid)
    backup_path = save("backup_" + wid + "_" + ts() + ".json", wf)

    changes = []
    changes.append(code(wf, "02 Normalize Request", NORMALIZE))
    changes.append(code(wf, "04 Safety / Policy Gate", POLICY))
    changes.append(code(wf, "07 Postprocess + Quality Check", POSTPROCESS))
    changes.append(code(wf, "10 Retry Postprocess", RETRYPOST))
    changes.append(code(wf, "11 Build Final Report", REPORT))
    changes.append(code(wf, "13 Build Error Response", ERROR))

    changes.append(ifnode(wf, "03 IF Normalized OK", "={{ $json.ok }}", True, "boolean"))
    changes.append(ifnode(wf, "05 IF Policy Allowed", "={{ $json.policy.decision }}", "allow", "string"))
    changes.append(ifnode(wf, "08 IF Needs Retry", "={{ $json.should_retry }}", True, "boolean"))

    changes.append(httpnode(wf, "06 Call Jarvis V5 Factory", False))
    changes.append(httpnode(wf, "09 Auto Retry Improved Prompt", True))

    n = node(wf, "12 Respond Success")
    if n:
        p = n.setdefault("parameters", {})
        p["respondWith"] = "json"
        p["responseBody"] = "={{ { ok: $json.ok, message: $json.final_message, result: $json.result } }}"
        p.setdefault("options", {})
        changes.append("patched 12 Respond Success")

    n = node(wf, "14 Respond Error")
    if n:
        p = n.setdefault("parameters", {})
        p["respondWith"] = "json"
        p["responseBody"] = "={{ $json }}"
        p["options"] = {"responseCode": 400}
        changes.append("patched 14 Respond Error")

    raw = json.dumps(wf, ensure_ascii=False)
    bad = []
    for pattern in ["{{ .", "const data = ;", "const r = ;", "const input = .body", "trycloudflare.co/api"]:
        if pattern in raw:
            bad.append(pattern)

    patched_path = save("patched_" + wid + "_" + ts() + ".json", wf)

    if bad:
        report = {"ok": False, "bad_patterns": bad, "candidate": candidate, "changes": changes, "backup": backup_path, "patched": patched_path}
        save("failed_report_" + wid + "_" + ts() + ".json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(2)

    payload = {
        "name": wf.get("name"),
        "nodes": wf.get("nodes", []),
        "connections": wf.get("connections", {}),
        "settings": wf.get("settings", {}),
    }
    if wf.get("staticData") is not None:
        payload["staticData"] = wf.get("staticData")

    updated = api("PUT", "/api/v1/workflows/" + wid, payload)

    report = {
        "ok": True,
        "workflow_id": wid,
        "workflow_name": wf.get("name"),
        "candidate": candidate,
        "changes": changes,
        "backup": backup_path,
        "patched": patched_path,
              "Refresh n8n page",
            "Click Execute workflow",
            "Send webhook-test request",
            "If ok, Save and Activate"
        ]
    }
    report_path = save("success_report_" + wid + "_" + ts() + ".json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("REPORT:", report_path)
    print("DONE")

main()