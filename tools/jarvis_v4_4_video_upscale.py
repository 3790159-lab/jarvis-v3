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
MEDIA_ROOTS = [
    ROOT / "jarvis_stage3_artifacts" / "influencer_v4_video_ab",
    ROOT / "jarvis_stage3_artifacts" / "influencer_v4_media",
]
OUT = ROOT / "jarvis_stage3_artifacts" / "influencer_v4_upscaled"
OUT.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/drive"]

def env(name):
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""

API_KEY = env("INFLUENCER_API_KEY")
BASE_URL = env("INFLUENCER_BASE_URL") or "https://influencerstudio.com/api/v1"
WORKSPACE_ID = env("INFLUENCER_WORKSPACE_ID")

if not API_KEY:
    raise SystemExit("ERROR: INFLUENCER_API_KEY not found")
if not WORKSPACE_ID:
    raise SystemExit("ERROR: INFLUENCER_WORKSPACE_ID not found")
if not TOKEN_PATH.exists():
    raise SystemExit(f"ERROR: Google token not found: {TOKEN_PATH}")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

def api_post(path, payload):
    url = BASE_URL.rstrip("/") + path
    r = requests.post(url, headers=HEADERS, json=payload, timeout=90)
    print("POST", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text[:2000])
        return None
    return r.json()

def api_get(path):
    url = BASE_URL.rstrip("/") + path
    r = requests.get(url, headers=HEADERS, timeout=90)
    print("GET", path, "->", r.status_code)
    if r.status_code >= 400:
        print(r.text[:1500])
        return None
    return r.json()

def find_latest_video_url():
    json_files = []
    for root in MEDIA_ROOTS:
        if root.exists():
            json_files.extend(root.rglob("*.json"))

    json_files = sorted(json_files, key=lambda p: p.stat().st_mtime, reverse=True)

    for p in json_files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        urls = []
        if isinstance(data, dict):
            urls.extend(data.get("video_urls", []) or [])
            urls.extend(data.get("result_urls", []) or [])

            for item in data.get("items", []) or []:
                u = item.get("url")
                if u:
                    urls.append(u)

            for item in data.get("uploaded_files", []) or []:
                u = item.get("webViewLink")
                if u:
                    urls.append(u)

        mp4s = [u for u in urls if isinstance(u, str) and ".mp4" in u.lower()]
        if mp4s:
            return mp4s[0], p

    raise SystemExit("ERROR: no mp4 video URL found in previous JSON files")

def poll_generation(gid):
    final = None
    for i in range(120):
        status = api_get(f"/generations/{gid}/status")
        if not status:
            time.sleep(5)
            continue

        print(
            f"poll {i+1}: status={status.get('status')} "
            f"completed={status.get('completed_items')} failed={status.get('failed_items')}"
        )

        if status.get("status") in ["completed", "failed"]:
            final = status
            break

        time.sleep(5)

    return final

def extract_video_urls(data):
    if not data:
        return []
    urls = data.get("result_urls", []) or []
    for item in data.get("items", []) or []:
        if item.get("url"):
            urls.append(item.get("url"))
    return [u for u in urls if u and ".mp4" in u.lower()]

run_id = time.strftime("%Y%m%d_%H%M%S")
run_dir = OUT / f"run_{run_id}"
run_dir.mkdir(parents=True, exist_ok=True)

print("== FIND LATEST VIDEO ==")
video_url, source_json = find_latest_video_url()
print("source_json:", source_json)
print("video_url:", video_url)

print("\n== TRY UPSCALE ==")

upscale_attempts = [
    {
        "name": "topaz",
        "endpoint": "/videos/upscale/topaz",
        "payloads": [
            {
                "video_url": video_url,
                "workspace_id": WORKSPACE_ID,
                "scale": 2,
                "resolution": "1080p"
            },
            {
                "input_video": video_url,
                "workspace_id": WORKSPACE_ID,
                "scale": 2
            },
        ],
    },
    {
        "name": "seedvr",
        "endpoint": "/videos/upscale/seedvr",
        "payloads": [
            {
                "video_url": video_url,
                "workspace_id": WORKSPACE_ID,
                "scale": 2,
                "enhance": True
            },
            {
                "input_video": video_url,
                "workspace_id": WORKSPACE_ID
            },
        ],
    },
    {
        "name": "freepik",
        "endpoint": "/videos/upscale/freepik",
        "payloads": [
            {
                "video_url": video_url,
                "workspace_id": WORKSPACE_ID,
                "scale": 2
            },
            {
                "input_video": video_url,
                "workspace_id": WORKSPACE_ID
            },
        ],
    },
]

created = None
chosen = None

for attempt in upscale_attempts:
    for payload in attempt["payloads"]:
        print(f"\nTRY {attempt['name']} payload keys={list(payload.keys())}")
        res = api_post(attempt["endpoint"], payload)
        if res and res.get("generation_id"):
            created = res
            chosen = attempt["name"]
            break
    if created:
        break

if not created:
    raise SystemExit("ERROR: all upscale attempts failed. Need exact upscale schema from Swagger.")

generation_id = created["generation_id"]
created_path = run_dir / f"upscale_created_{chosen}_{generation_id}.json"
created_path.write_text(json.dumps(created, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n== UPSCALE QUEUED ==")
print("chosen:", chosen)
print("generation_id:", generation_id)
print(json.dumps(created, ensure_ascii=False, indent=2))

print("\n== POLL UPSCALE STATUS ==")
final = poll_generation(generation_id)

if not final:
    raise SystemExit("ERROR: upscale timeout")

final_path = run_dir / f"upscale_final_{chosen}_{generation_id}.json"
final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

upscaled_urls = extract_video_urls(final)

print("\n== UPSCALE RESULT URLS ==")
for u in upscaled_urls:
    print(u)

if not upscaled_urls:
    print(json.dumps(final, ensure_ascii=False, indent=2)[:4000])
    raise SystemExit("ERROR: upscale completed but no mp4 URLs found")

print("\n== DOWNLOAD UPSCALED VIDEO ==")

video_files = []

for i, url in enumerate(upscaled_urls, 1):
    parsed = urlparse(url)
    name = Path(parsed.path).name or f"upscaled_{chosen}_{i:03d}.mp4"
    if not name.lower().endswith(".mp4"):
        name = f"upscaled_{chosen}_{i:03d}.mp4"

    path = run_dir / f"upscaled_{chosen}_{i:03d}_{name}"

    r = requests.get(url, timeout=300)
    r.raise_for_status()
    path.write_bytes(r.content)

    video_files.append(path)
    print("saved:", path)

print("\n== UPLOAD TO GOOGLE DRIVE ==")

creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
drive = build("drive", "v3", credentials=creds)

folder = drive.files().create(
    body={
        "name": f"Jarvis_V4_4_Upscaled_Video_{run_id}",
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
uploaded.append(upload(created_path, "application/json"))
uploaded.append(upload(final_path, "application/json"))

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

summary = {
    "ok": True,
    "chosen_upscaler": chosen,
    "source_video_url": video_url,
    "source_json": str(source_json),
    "generation_id": generation_id,
    "upscaled_urls": upscaled_urls,
    "local_files": [str(p) for p in video_files],
    "drive_folder_url": folder.get("webViewLink"),
    "uploaded": uploaded,
}
summary_path = run_dir / "upscale_summary.json"
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
uploaded.append(upload(summary_path, "application/json"))

print("\n== UPSCALED DRIVE RESULT ==")
print("folder_url:", folder.get("webViewLink"))
for f in uploaded:
    print(f"{f.get('name')}: {f.get('webViewLink')}")

print("\nlocal_run_dir:", run_dir)
print("DONE")