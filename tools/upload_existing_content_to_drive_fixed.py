import time
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "content_factory"
TOKEN_PATH = PROJECT_ROOT / "google_oauth_token_drive.json"

SCOPES = ["https://www.googleapis.com/auth/drive"]

if not TOKEN_PATH.exists():
    raise SystemExit(f"ERROR: token not found: {TOKEN_PATH}")

json_files = sorted(OUT_DIR.glob("content_pack_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
md_files = sorted(OUT_DIR.glob("content_pack_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)

if not json_files or not md_files:
    raise SystemExit("ERROR: no generated content files found")

json_path = json_files[0]
md_path = md_files[0]

print("json:", json_path)
print("md:", md_path)

creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
drive = build("drive", "v3", credentials=creds)

run_id = time.strftime("%Y%m%d_%H%M%S")
folder_name = f"Jarvis_Content_Factory_{run_id}"

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

json_file = upload_file(json_path, "application/json")
md_file = upload_file(md_path, "text/markdown")

try:
    drive.permissions().create(
        fileId=folder_id,
        body={"type": "anyone", "role": "reader"},
        fields="id"
    ).execute()
except Exception as e:
    print("share_warning:", str(e))

folder = drive.files().get(fileId=folder_id, fields="id, webViewLink").execute()

print("")
print("== GOOGLE DRIVE RESULT ==")
print("folder_url:", folder.get("webViewLink"))
print("json_url:", json_file.get("webViewLink"))
print("markdown_url:", md_file.get("webViewLink"))
print("")
print("DONE")