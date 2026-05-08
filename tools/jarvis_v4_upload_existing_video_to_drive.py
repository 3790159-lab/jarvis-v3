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
TOKEN_PATH = ROOT / "google_oauth_token_drive.json"
MEDIA_DIR = ROOT / "jarvis_stage3_artifacts" / "influencer_v4_media"

SCOPES = ["https://www.googleapis.com/auth/drive"]

if not TOKEN_PATH.exists():
    raise SystemExit(f"ERROR: Google token not found: {TOKEN_PATH}")

def newest_file(pattern):
    files = sorted(MEDIA_DIR.rglob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    return files[0]

video_json = newest_file("video_generation_*.json")
if not video_json:
    raise SystemExit(f"ERROR: no video_generation_*.json found in {MEDIA_DIR}")

run_dir = video_json.parent

print("== LOAD VIDEO STATUS JSON ==")
print("video_json:", video_json)
print("run_dir:", run_dir)

data = json.loads(video_json.read_text(encoding="utf-8"))

video_urls = data.get("result_urls", [])
if not video_urls:
    video_urls = [item.get("url") for item in data.get("items", []) if item.get("url")]

video_urls = [u for u in video_urls if u and str(u).lower().endswith(".mp4")]

if not video_urls:
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    raise SystemExit("ERROR: no mp4 video URLs found in video status JSON")

print("\n== DOWNLOAD VIDEO ==")

video_files = []

for i, url in enumerate(video_urls, 1):
    try:
        parsed = urlparse(url)
        original_name = Path(parsed.path).name or f"v4_video_{i:03d}.mp4"
        if not original_name.lower().endswith(".mp4"):
            original_name = f"v4_video_{i:03d}.mp4"

        video_path = run_dir / f"v4_video_{i:03d}_{original_name}"

        r = requests.get(url, timeout=180)
        r.raise_for_status()

        video_path.write_bytes(r.content)

        print(f"saved video: {video_path}")
        video_files.append(video_path)

    except Exception as e:
        print(f"video download error: {e}")

if not video_files:
    raise SystemExit("ERROR: video download failed")

print("\n== UPLOAD VIDEOS TO DRIVE ==")

creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
drive = build("drive", "v3", credentials=creds)

folder_name = f"Jarvis_V4_Videos_{time.strftime('%Y%m%d_%H%M%S')}"
folder = drive.files().create(
    body={
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    },
    fields="id, webViewLink"
).execute()

folder_id = folder["id"]

uploaded = []

for path in video_files:
    try:
        file_metadata = {
            "name": path.name,
            "parents": [folder_id]
        }

        media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=False)

        file = drive.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink"
        ).execute()

        uploaded.append(file)
        print(f"uploaded video: {file.get('webViewLink')}")

    except Exception as e:
        print(f"upload error: {e}")

try:
    drive.permissions().create(
        fileId=folder_id,
        body={"type": "anyone", "role": "reader"},
        fields="id"
    ).execute()
except Exception as e:
    print("share_warning:", str(e))

folder = drive.files().get(fileId=folder_id, fields="id, webViewLink").execute()

summary = {
    "ok": True,
    "source_video_json": str(video_json),
    "video_urls": video_urls,
    "local_videos": [str(p) for p in video_files],
    "drive_folder_url": folder.get("webViewLink"),
    "uploaded": uploaded,
}

summary_path = run_dir / "v4_video_drive_upload_summary.json"
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n== VIDEO DRIVE RESULT ==")
print("folder_url:", folder.get("webViewLink"))

for f in uploaded:
    print(f"{f.get('name')}: {f.get('webViewLink')}")

print("\nlocal_summary:", summary_path)
print("DONE")