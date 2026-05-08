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

if not API_KEY:
    raise SystemExit("ERROR: INFLUENCER_API_KEY not found in .env")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def api_get(path):
    r = requests.get(BASE_URL.rstrip("/") + path, headers=HEADERS, timeout=60)
    print("GET", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text)
        r.raise_for_status()
    return r.json()

def api_post(path, payload):
    r = requests.post(BASE_URL.rstrip("/") + path, headers=HEADERS, json=payload, timeout=60)
    print("POST", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text)
        r.raise_for_status()
    return r.json()

print("== CHECK CREDITS ==")
credits = api_get("/billing/credits")
print("credits:", credits)

print("\n== GENERATE REALISTIC FACE OPTIONS ==")

payload = {
    "prompt": (
        "realistic high-end fashion model portrait, fictional adult 25 year old woman, "
        "natural skin texture, symmetrical face, elegant premium look, editorial beauty photography, "
        "soft studio lighting, photorealistic, no nudity, no explicit content"
    ),
    "negative_prompt": (
        "cartoon, anime, plastic skin, uncanny, deformed face, extra fingers, bad anatomy, "
        "child, teen, minor, nudity, explicit content, celebrity, real person"
    ),
    "batch": 4,
    "gender": "female",
    "ethnicity": "White Caucasian",
    "hair_color": "brown hair"
}

created = api_post("/images/generate-face", payload)
print(json.dumps(created, ensure_ascii=False, indent=2))

generation_id = created.get("generation_id")
if not generation_id:
    raise SystemExit("ERROR: no generation_id returned")

print("\n== POLL STATUS ==")

final = None
for i in range(60):
    status = api_get(f"/generations/{generation_id}/status")
    print(f"poll {i+1}: {status.get('status')} completed={status.get('completed_items')} failed={status.get('failed_items')}")

    if status.get("status") in ["completed", "failed"]:
        final = status
        break

    time.sleep(5)

if final is None:
    raise SystemExit("ERROR: timeout waiting for generation")

out_path = OUT / f"face_generation_{generation_id}.json"
out_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n== RESULT ==")
print("saved:", out_path)
print("status:", final.get("status"))
print("result_urls:")
for url in final.get("result_urls", []):
    print(url)

print("\nNEXT:")
print("Pick the best 3-5 face URLs. Then we create a permanent influencer with /influencers/create.")