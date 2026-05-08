import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV = ROOT / ".env"
TOKEN_PATH = ROOT / "google_oauth_token_drive.json"
OUT = ROOT / "jarvis_stage3_artifacts" / "influencer_v4_media"
SRC = ROOT / "jarvis_stage3_artifacts" / "influencer_v4"

OUT.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/drive"]

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
if not TOKEN_PATH.exists():
    raise SystemExit(f"ERROR: Google Drive token not found: {TOKEN_PATH}")

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

def find_latest_generation_json():
    files = sorted(SRC.glob("influencer_generation_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"ERROR: no influencer_generation_*.json found in {SRC}")
    return files[0]

def safe_filename_from_url(url, index):
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if not name:
        name = f"asset_{index:03d}.png"
    if "." not in name:
        name += ".png"
    return name

run_id = time.strftime("%Y%m%d_%H%M%S")
run_dir = OUT / f"run_{run_id}"
run_dir.mkdir(parents=True, exist_ok=True)

print("== LOAD LATEST GENERATED IMAGE URLS ==")
latest_json = find_latest_generation_json()
data = json.loads(latest_json.read_text(encoding="utf-8"))

urls = data.get("result_urls", [])
if not urls:
    urls = [item.get("url") for item in data.get("items", []) if item.get("url")]

urls = [u for u in urls if u]

if not urls:
    raise SystemExit("ERROR: no result_urls found in latest generation json")

print("source_json:", latest_json)
print("urls:", len(urls))

print("\n== DOWNLOAD IMAGES ==")
image_paths = []

for i, url in enumerate(urls, start=1):
    filename = f"v4_photo_{i:03d}_" + safe_filename_from_url(url, i)
    path = run_dir / filename

    r = requests.get(url, timeout=120)
    if r.status_code >= 400:
        print("download_failed:", url, r.status_code)
        continue

    path.write_bytes(r.content)
    image_paths.append(path)
    print("saved:", path)

if not image_paths:
    raise SystemExit("ERROR: no images downloaded")

print("\n== GOOGLE DRIVE UPLOAD ==")
creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
drive = build("drive", "v3", credentials=creds)

folder_name = f"Jarvis_V4_Influencer_Media_{run_id}"
folder = drive.files().create(
    body={
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    },
    fields="id, webViewLink"
).execute()

folder_id = folder["id"]

def upload_file(path, mime):
    return drive.files().create(
        body={
            "name": path.name,
            "parents": [folder_id],
        },
        media_body=MediaFileUpload(str(path), mimetype=mime, resumable=False),
        fields="id, name, webViewLink"
    ).execute()

uploaded = []

for path in image_paths:
    uploaded.append(upload_file(path, "image/png"))

summary_path = run_dir / "v4_media_summary.json"
summary = {
    "ok": True,
    "run_id": run_id,
    "source_json": str(latest_json),
    "image_urls": urls,
    "local_images": [str(p) for p in image_paths],
    "drive_folder_id": folder_id,
}
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
uploaded.append(upload_file(summary_path, "application/json"))

try:
    drive.permissions().create(
        fileId=folder_id,
        body={"type": "anyone", "role": "reader"},
        fields="id"
    ).execute()
except Exception as e:
    print("share_warning:", str(e))

folder = drive.files().get(fileId=folder_id, fields="id, webViewLink").execute()
folder_url = folder.get("webViewLink")

print("\n== GENERATE VIDEO FROM BEST IMAGE ==")

best_image_url = urls[0]

video_payload = {
    "prompt": (
        "ultra realistic luxury Instagram reel, the same influencer slowly walking on a luxury hotel balcony, "
        "golden hour lighting, ocean view, cinematic camera movement, elegant confident expression, "
        "premium fashion editorial style, no nudity, no explicit content"
    ),
    "model": "kling-3",
    "first_frame_image": best_image_url,
    "settings": {
        "aspect_ratio": "9:16",
        "duration": 5
    },
    "workspace_id": WORKSPACE_ID
}

video_created = api_post("/videos/generate", video_payload)
video_generation_id = video_created.get("generation_id")

video_final = None
video_urls = []

if video_generation_id:
    print("video_generation_id:", video_generation_id)
    for i in range(90):
        status = api_get(f"/generations/{video_generation_id}/status")
        print(f"video poll {i+1}: {status.get('status')} completed={status.get('completed_items')} failed={status.get('failed_items')}")

        if status.get("status") in ["completed", "failed"]:
            video_final = status
            video_urls = status.get("result_urls", [])
            break

        time.sleep(5)

    video_status_path = run_dir / f"video_generation_{video_generation_id}.json"
    video_status_path.write_text(json.dumps(video_final or video_created, ensure_ascii=False, indent=2), encoding="utf-8")
    uploaded.append(upload_file(video_status_path, "application/json"))

print("\n== GOOGLE DRIVE RESULT ==")
print("folder_url:", folder_url)

print("\nuploaded_files:")
for f in uploaded:
    print(f"{f.get('name')}: {f.get('webViewLink')}")

print("\nvideo_urls:")
for u in video_urls:
    print(u)

print("\nlocal_run_dir:", run_dir)
print("DONE")