import re, json, time, requests
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

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def req(method, path, payload=None):
    url = BASE_URL.rstrip("/") + path
    r = requests.request(method, url, headers=HEADERS, json=payload, timeout=60)
    print(method, path, "->", r.status_code)
    try:
        print(r.text[:1000])
    except Exception:
        pass
    return r

print("== CREDITS ==")
req("GET", "/billing/credits")

print("\n== TRY DISCOVER WORKSPACE ==")
candidate_paths = [
    "/workspaces",
    "/workspace",
    "/team/workspaces",
    "/teams/workspaces",
    "/projects",
    "/influencers",
]

for p in candidate_paths:
    r = req("GET", p)
    if r.status_code == 200:
        try:
            data = r.json()
            out = OUT / ("probe_" + p.strip("/").replace("/", "_") + ".json")
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

if not WORKSPACE_ID:
    print("\nNO INFLUENCER_WORKSPACE_ID in .env")
    print("Open Influencer Studio dashboard and look for workspace/team/project id.")
    print("Often it appears in URL or account/workspace settings.")
    print("Then set INFLUENCER_WORKSPACE_ID=<id> in .env and run again.")
    raise SystemExit(0)

print("\n== RETRY FACE GENERATION WITH WORKSPACE_ID ==")

payload = {
    "prompt": "realistic high-end fashion model portrait, fictional adult 25 year old woman, natural skin texture, elegant premium look, editorial beauty photography, photorealistic, no nudity, no explicit content",
    "negative_prompt": "cartoon, anime, plastic skin, uncanny, deformed face, bad anatomy, child, teen, minor, nudity, explicit content, celebrity, real person",
    "batch": 4,
    "gender": "female",
    "ethnicity": "White Caucasian",
    "hair_color": "brown hair",
    "workspace_id": WORKSPACE_ID
}

r = req("POST", "/images/generate-face", payload)
if r.status_code >= 400:
    raise SystemExit("Face generation failed.")

created = r.json()
generation_id = created.get("generation_id")
print("generation_id:", generation_id)

if not generation_id:
    raise SystemExit("No generation_id returned.")

print("\n== POLL STATUS ==")
final = None
for i in range(60):
    sr = requests.get(BASE_URL.rstrip() + f"/generations/{generation_id}/status", headers=HEADERS, timeout=60)
    print("poll", i + 1, sr.status_code, sr.text[:500])
    if sr.status_code == 200:
        data = sr.json()
        if data.get("status") in ["completed", "failed"]:
            final = data
            break
    time.sleep(5)

if final:
    out = OUT / f"face_generation_{generation_id}.json"
    out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== RESULT URLS ==")
    for u in final.get("result_urls", []):
        print(u)