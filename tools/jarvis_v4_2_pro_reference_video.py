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
MEDIA_DIR = ROOT / "jarvis_stage3_artifacts" / "influencer_v4_media"

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
    raise SystemExit("ERROR: Google Drive token not found")

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

def latest_image_generation_json():
    files = sorted(
        (ROOT / "jarvis_stage3_artifacts" / "influencer_v4").glob("influencer_generation_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    if not files:
        raise SystemExit("ERROR: no influencer_generation_*.json found")
    return files[0]

run_id = time.strftime("%Y%m%d_%H%M%S")
run_dir = MEDIA_DIR / f"pro_video_{run_id}"
run_dir.mkdir(parents=True, exist_ok=True)

print("== LOAD BEST REFERENCE IMAGE ==")
img_json = latest_image_generation_json()
img_data = json.loads(img_json.read_text(encoding="utf-8"))

image_urls = img_data.get("result_urls", [])
if not image_urls:
    image_urls = [item.get("url") for item in img_data.get("items", []) if item.get("url")]

image_urls = [u for u in image_urls if u]

if not image_urls:
    raise SystemExit("ERROR: no image URLs found")

best_image = image_urls[0]
print("best_image:", best_image)

print("\n== CREATE PRO REFERENCE VIDEO ==")

payload = {
    "influencer_id": INFLUENCER_ID,
    "start_image_url": best_image,
    "prompt": (
        "@Element1 stands on a luxury hotel balcony at golden hour, ocean in the background. "
        "She slowly turns her head toward the camera, gives a subtle natural smile, hair moves gently in the breeze. "
        "Realistic human motion, minimal movement, elegant posture, cinematic luxury fashion reel, "
        "natural skin texture, realistic eyes, stable face identity, no morphing, no distorted hands, "
        "no exaggerated body movement, no nudity, no explicit content."
    ),
    "duration": "5",
    "aspect_ratio": "9:16",
    "workspace_id": WORKSPACE_ID
}

created = api_post("/videos/reference-to-video", payload)
generation_id = created.get("generation_id")
print(json.dumps(created, ensure_ascii=False, indent=2))

if not generation_id:
    raise SystemExit("ERROR: no generation_id")

print("\n== POLL VIDEO STATUS ==")

final = None
for i in range(100):
    status = api_get(f"/generations/{generation_id}/status")
    print(f"poll {i+1}: {status.get('status')} completed={status.get('completed_items')} failed={status.get('failed_items')}")
    if status.get("status") in ["completed", "failed"]:
        final = status
        break
    time.sleep(5)

if not final:
    raise SystemExit("ERROR: video timeout")

status_path = run_dir / f"reference_video_{generation_id}.json"
status_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

video_urls = final.get("result_urls", [])
if not video_urls:
    video_urls = [item.get("url") for item in final.get("items", []) if item.get("url")]

video_urls = [u for u in video_urls if u]

print("\n== DOWNLOAD VIDEO ==")

video_files = []
for i, url in enumerate(video_urls, 1):
    parsed = urlparse(url)
    name = Path(parsed.path).name or f"pro_reference_video_{i:03d}.mp4"
    if not name.lower().endswith(".mp4"):
        name = f"pro_reference_video_{i:03d}.mp4"

    path = run_dir / f"pro_reference_video_{i:03d}_{name}"
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    path.write_bytes(r.content)
    video_files.append(path)
    print("saved:", path)

print("\n== UPLOAD TO GOOGLE DRIVE ==")

creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
drive = build("drive", "v3", credentials=creds)

folder = drive.files().create(
    body={
        "name": f"Jarvis_V4_Pro_Video_{run_id}",
        "mimeType": "application/vnd.google-apps.folder",
    },
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
uploaded.append(upload(status_path, "application/json"))

for path in video_files:
    uploaded.append(upload(path, "video/mp4"))

try:
    drive.permissions().create(
        fileId=folder_id,
        body={"type": "anyone", "role": "reader"},
        fields="id"
    ).execute()
except Exception as e:
    print("share_warning:", str(e))

folder = drive.files().get(fileId=folder_id, fields="id, webViewLink").execute()

print("\n== PRO VIDEO RESULT ==")
print("folder_url:", folder.get("webViewLink"))
for f in uploaded:
    print(f"{f.get('name')}: {f.get('webViewLink')}")

print("\nsource_image:", best_image)
print("local_run_dir:", run_dir)
print("DONE")