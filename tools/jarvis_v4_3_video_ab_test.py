import json, re, time, requests
from pathlib import Path
from urllib.parse import urlparse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV = ROOT / ".env"
TOKEN_PATH = ROOT / "google_oauth_token_drive.json"
SRC = ROOT / "jarvis_stage3_artifacts" / "influencer_v4"
OUT = ROOT / "jarvis_stage3_artifacts" / "influencer_v4_video_ab"
OUT.mkdir(parents=True, exist_ok=True)

def env(name):
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""

API_KEY = env("INFLUENCER_API_KEY")
BASE_URL = env("INFLUENCER_BASE_URL") or "https://influencerstudio.com/api/v1"
WORKSPACE_ID = env("INFLUENCER_WORKSPACE_ID")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def api_post(path, payload):
    r = requests.post(BASE_URL.rstrip("/") + path, headers=HEADERS, json=payload, timeout=90)
    print("POST", path, payload.get("model"), "->", r.status_code)
    if r.status_code >= 400:
        print(r.text[:1500])
        return None
    return r.json()

def api_get(path):
    r = requests.get(BASE_URL.rstrip("/") + path, headers=HEADERS, timeout=90)
    if r.status_code >= 400:
        print("GET ERROR", r.status_code, r.text[:1000])
        return None
    return r.json()

def latest_image_json():
    files = sorted(SRC.glob("influencer_generation_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No influencer image generation json found.")
    return files[0]

run_id = time.strftime("%Y%m%d_%H%M%S")
run_dir = OUT / f"run_{run_id}"
run_dir.mkdir(parents=True, exist_ok=True)

img_data = json.loads(latest_image_json().read_text(encoding="utf-8"))
image_urls = img_data.get("result_urls", []) or [x.get("url") for x in img_data.get("items", []) if x.get("url")]
if not image_urls:
    raise SystemExit("No image urls found.")

best_image = image_urls[0]
print("best_image:", best_image)

models = ["veo-3.1", "sora-2", "kling-3"]

prompt = (
    "A realistic luxury Instagram reel from the provided first frame. "
    "Keep the same woman and face identity perfectly stable. "
    "Very subtle natural motion only: slight breathing, small head turn, gentle hair movement, tiny camera push-in. "
    "Do not change face, do not change outfit, do not change body shape. "
    "No morphing, no warping, no extra fingers, no distorted hands, no exaggerated movement. "
    "Cinematic golden hour, luxury hotel balcony, ocean background, premium fashion editorial, natural human motion."
)

created_jobs = []

for model in models:
    payload = {
        "prompt": prompt,
        "model": model,
        "first_frame_image": best_image,
        "settings": {
            "aspect_ratio": "9:16",
            "duration": 5
        },
        "workspace_id": WORKSPACE_ID
    }
    res = api_post("/videos/generate", payload)
    if res and res.get("generation_id"):
        created_jobs.append({"model": model, "generation_id": res["generation_id"], "created": res})

print("jobs:", created_jobs)

results = []

for job in created_jobs:
    gid = job["generation_id"]
    model = job["model"]
    final = None

    print(f"\n== POLL {model} {gid} ==")
    for i in range(120):
        st = api_get(f"/generations/{gid}/status")
        if not st:
            time.sleep(5)
            continue

        print(f"{model} poll {i+1}: {st.get('status')} completed={st.get('completed_items')} failed={st.get('failed_items')}")

        if st.get("status") in ["completed", "failed"]:
            final = st
            break
        time.sleep(5)

    path = run_dir / f"{model}_{gid}.json"
    path.write_text(json.dumps(final or job, ensure_ascii=False, indent=2), encoding="utf-8")

    urls = []
    if final:
        urls = final.get("result_urls", []) or [x.get("url") for x in final.get("items", []) if x.get("url")]

    results.append({"model": model, "generation_id": gid, "status": final, "urls": urls, "json_path": str(path)})

print("\n== DOWNLOAD VIDEOS ==")

video_files = []
for r in results:
    for idx, url in enumerate(r["urls"], 1):
        if not url:
            continue
        parsed = urlparse(url)
        name = Path(parsed.path).name or f"{r['model']}_{idx}.mp4"
        if not name.lower().endswith(".mp4"):
            name = f"{r['model']}_{idx}.mp4"
        local = run_dir / f"{r['model']}_{idx}_{name}"
        try:
            rr = requests.get(url, timeout=240)
            rr.raise_for_status()
            local.write_bytes(rr.content)
            video_files.append(local)
            print("saved:", local)
        except Exception as e:
            print("download failed:", url, e)

print("\n== UPLOAD TO DRIVE ==")

creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), ["https://www.googleapis.com/auth/drive"])
drive = build("drive", "v3", credentials=creds)

folder = drive.files().create(
    body={"name": f"Jarvis_V4_3_Video_AB_{run_id}", "mimeType": "application/vnd.google-apps.folder"},
    fields="id, webViewLink"
).execute()
folder_id = folder["id"]

def upload(path, mime):
    return drive.files().create(
        body={"name": path.name, "parents": [folder_id]},
        media_body=MediaFileUpload(str(path), mimetype=mime, resumable=False),
        fields="id, name, webViewLink"
    ).execute()

uploaded = []
for vf in video_files:
    uploaded.append(upload(vf, "video/mp4"))

summary_path = run_dir / "ab_test_summary.json"
summary_path.write_text(json.dumps({
    "ok": True,
    "run_id": run_id,
    "best_image": best_image,
    "models": models,
    "results": results,
    "local_videos": [str(x) for x in video_files],
}, ensure_ascii=False, indent=2), encoding="utf-8")
uploaded.append(upload(summary_path, "application/json"))

try:
    drive.permissions().create(fileId=folder_id, body={"type": "anyone", "role": "reader"}, fields="id").execute()
except Exception as e:
    print("share_warning:", e)

folder = drive.files().get(fileId=folder_id, fields="id, webViewLink").execute()

print("\n== RESULT ==")
print("folder_url:", folder.get("webViewLink"))
for f in uploaded:
    print(f"{f.get('name')}: {f.get('webViewLink')}")
print("local_run_dir:", run_dir)
print("DONE")