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
SRC = ROOT / "jarvis_stage3_artifacts" / "influencer_v4"
OUT = ROOT / "jarvis_stage3_artifacts" / "influencer_v4_sensual_test"
OUT.mkdir(parents=True, exist_ok=True)

def env(name):
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""

API_KEY = env("INFLUENCER_API_KEY")
BASE_URL = env("INFLUENCER_BASE_URL") or "https://influencerstudio.com/api/v1"
WORKSPACE_ID = env("INFLUENCER_WORKSPACE_ID")
INFLUENCER_ID = env("INFLUENCER_ID")

if not API_KEY or not WORKSPACE_ID or not INFLUENCER_ID:
    raise SystemExit("ERROR: missing INFLUENCER_API_KEY / WORKSPACE_ID / INFLUENCER_ID")
if not TOKEN_PATH.exists():
    raise SystemExit("ERROR: Google Drive token not found")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def api_post(path, payload):
    r = requests.post(BASE_URL.rstrip("/") + path, headers=HEADERS, json=payload, timeout=90)
    print("POST", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text[:2000])
        r.raise_for_status()
    return r.json()

def api_get(path):
    r = requests.get(BASE_URL.rstrip("/") + path, headers=HEADERS, timeout=90)
    print("GET", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text[:1500])
        r.raise_for_status()
    return r.json()

def latest_image_json():
    files = sorted(SRC.glob("influencer_generation_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("ERROR: no influencer_generation_*.json found")
    return files[0]

run_id = time.strftime("%Y%m%d_%H%M%S")
run_dir = OUT / f"run_{run_id}"
run_dir.mkdir(parents=True, exist_ok=True)

img_data = json.loads(latest_image_json().read_text(encoding="utf-8"))
image_urls = img_data.get("result_urls", []) or [x.get("url") for x in img_data.get("items", []) if x.get("url")]
image_urls = [u for u in image_urls if u]

if not image_urls:
    raise SystemExit("ERROR: no image URLs found")

best_image = image_urls[0]
print("best_image:", best_image)

prompt = (
    "Create a premium sensual fashion Instagram reel from the reference image. "
    "The same woman remains fully dressed in an elegant fitted luxury outfit. "
    "She stands in a stylish hotel suite near a large mirror and slowly shifts her weight from one leg to the other, "
    "subtle hip shift, elegant confident posture, slight turn of shoulders, soft natural smile, gentle hair movement. "
    "Camera slowly pushes in from a low fashion-editorial angle, but keep it tasteful, luxury, and Instagram-safe. "
    "Keep face identity stable, preserve outfit and body proportions. "
    "No nudity, no explicit sexual content, no twerking, no exaggerated movement, no body deformation, "
    "no warped hands, no morphing, no camera shake. "
    "Ultra realistic motion, cinematic lighting, natural skin texture, premium fashion editorial style."
)

payload = {
    "influencer_id": INFLUENCER_ID,
    "prompt": prompt,
    "model": "kling-3",
    "first_frame_image": best_image,
    "settings": {
        "aspect_ratio": "9:16",
        "duration": 5
    },
    "workspace_id": WORKSPACE_ID
}

print("\n== GENERATE SENSUAL FASHION VIDEO ==")
created = api_post("/videos/generate", payload)
print(json.dumps(created, ensure_ascii=False, indent=2))

generation_id = created.get("generation_id")
if not generation_id:
    raise SystemExit("ERROR: no generation_id returned")

print("\n== POLL STATUS ==")
final = None
for i in range(120):
    status = api_get(f"/generations/{generation_id}/status")
    print(f"poll {i+1}: {status.get('status')} completed={status.get('completed_items')} failed={status.get('failed_items')}")
    if status.get("status") in ["completed", "failed"]:
        final = status
        break
    time.sleep(5)

if not final:
    raise SystemExit("ERROR: timeout waiting video")

status_path = run_dir / f"sensual_video_{generation_id}.json"
status_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

video_urls = final.get("result_urls", []) or [x.get("url") for x in final.get("items", []) if x.get("url")]
video_urls = [u for u in video_urls if u and ".mp4" in u.lower()]

print("\n== DOWNLOAD VIDEO ==")
video_files = []

for i, url in enumerate(video_urls, 1):
    parsed = urlparse(url)
    name = Path(parsed.path).name or f"sensual_video_{i:03d}.mp4"
    if not name.lower().endswith(".mp4"):
        name = f"sensual_video_{i:03d}.mp4"

    local = run_dir / f"sensual_fashion_{i:03d}_{name}"
    r = requests.get(url, timeout=240)
    r.raise_for_status()
    local.write_bytes(r.content)
    video_files.append(local)
    print("saved:", local)

if not video_files:
    print(json.dumps(final, ensure_ascii=False, indent=2)[:3000])
    raise SystemExit("ERROR: no video files downloaded")

print("\n== UPLOAD TO GOOGLE DRIVE ==")

creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), ["https://www.googleapis.com/auth/drive"])
drive = build("drive", "v3", credentials=creds)

folder = drive.files().create(
    body={
        "name": f"Jarvis_V4_5_Sensual_Fashion_Test_{run_id}",
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

uploaded = [upload(status_path, "application/json")]

for vf in video_files:
    uploaded.append(upload(vf, "video/mp4"))

try:
    drive.permissions().create(
        fileId=folder_id,
        body={"type": "anyone", "role": "reader"},
        fields="id"
    ).execute()
except Exception as e:
    print("share_warning:", e)

folder = drive.files().get(fileId=folder_id, fields="id, webViewLink").execute()

print("\n== RESULT ==")
print("folder_url:", folder.get("webViewLink"))
for f in uploaded:
    print(f"{f.get('name')}: {f.get('webViewLink')}")

print("\nsource_image:", best_image)
print("local_run_dir:", run_dir)
print("DONE")