import re
import json
import time
import requests
from pathlib import Path

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV = ROOT / ".env"
OUT = ROOT / "jarvis_stage3_artifacts" / "influencer_v4"
OUT.mkdir(parents=True, exist_ok=True)

def get_env(name):
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""

API_KEY = get_env("INFLUENCER_API_KEY")
BASE_URL = get_env("INFLUENCER_BASE_URL") or "https://influencerstudio.com/api/v1"
WORKSPACE_ID = get_env("INFLUENCER_WORKSPACE_ID")
INFLUENCER_ID = get_env("INFLUENCER_ID")

if not API_KEY:
    raise SystemExit("ERROR: INFLUENCER_API_KEY not found")
if not WORKSPACE_ID:
    raise SystemExit("ERROR: INFLUENCER_WORKSPACE_ID not found")
if not INFLUENCER_ID:
    raise SystemExit("ERROR: INFLUENCER_ID not found")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def get(path):
    r = requests.get(BASE_URL.rstrip("/") + path, headers=HEADERS, timeout=60)
    print("GET", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text)
        r.raise_for_status()
    return r.json()

def post(path, payload):
    r = requests.post(BASE_URL.rstrip("/") + path, headers=HEADERS, json=payload, timeout=60)
    print("POST", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text)
        r.raise_for_status()
    return r.json()

print("== CONFIG ==")
print("BASE_URL:", BASE_URL)
print("WORKSPACE_ID:", WORKSPACE_ID)
print("INFLUENCER_ID:", INFLUENCER_ID)

print("\n== CREDITS ==")
credits = get("/billing/credits")
print("credits:", credits)

print("\n== GENERATE INFLUENCER PHOTOS ==")

payload = {
    "influencer_id": INFLUENCER_ID,
    "workspace_id": WORKSPACE_ID,
    "prompt": (
        "ultra realistic premium Instagram lifestyle photoshoot, "
        "luxury brunette female influencer, elegant black outfit, "
        "cinematic golden hour lighting, natural skin texture, realistic human proportions, "
        "high-end editorial photography, 85mm lens, shallow depth of field, "
        "luxury hotel balcony, ocean view, confident calm expression, "
        "no nudity, no explicit content"
    ),
    "camera_style": "pro",
    "batch": 4,
    "settings": {
        "aspect_ratio": "9:16"
    }
}

created = post("/influencers/generate", payload)
print(json.dumps(created, ensure_ascii=False, indent=2))

generation_id = created.get("generation_id")
if not generation_id:
    raise SystemExit("ERROR: no generation_id returned")

print("\n== POLL STATUS ==")

final = None
for i in range(90):
    status = get(f"/generations/{generation_id}/status")
    print(
        f"poll {i+1}: status={status.get('status')} "
        f"completed={status.get('completed_items')} "
        f"failed={status.get('failed_items')}"
    )

    if status.get("status") in ["completed", "failed"]:
        final = status
        break

    time.sleep(5)

if final is None:
    raise SystemExit("ERROR: timeout waiting for generation")

out_path = OUT / f"influencer_generation_{generation_id}.json"
out_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n== RESULT ==")
print("saved:", out_path)
print("status:", final.get("status"))
print("credits_used:", final.get("credits_used"))

print("\nresult_urls:")
for url in final.get("result_urls", []):
    print(url)

print("\nitems:")
for item in final.get("items", []):
    print(item.get("status"), item.get("url"))

print("\nDONE")