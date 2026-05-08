import os, re, json, time
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV_PATH = PROJECT_ROOT / ".env"
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "content_factory"
TOKEN_PATH = PROJECT_ROOT / "google_oauth_token_drive.json"

SCOPES = ["https://www.googleapis.com/auth/drive"]

def env_value(text, names):
    for name in names:
        m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""

def set_env_line(text, key, value):
    line = f"{key}={value}"
    if re.search(rf"^{re.escape(key)}=.*$", text, re.MULTILINE):
        return re.sub(rf"^{re.escape(key)}=.*$", line, text, flags=re.MULTILINE)
    return text.rstrip() + "\n" + line + "\n"

env_text = ENV_PATH.read_text(encoding="utf-8", errors="replace")

client_secret_path = env_value(env_text, ["GOOGLE_OAUTH_CLIENT_SECRET_JSON", "GOOGLE_CLIENT_SECRET_JSON"])

if not client_secret_path or not Path(client_secret_path).exists():
    raise SystemExit(
        "ERROR: GOOGLE_OAUTH_CLIENT_SECRET_JSON not found in .env or file does not exist. "
        "Нужно скачать OAuth Client JSON из Google Cloud Console и прописать путь в .env."
    )

print("== OPEN GOOGLE LOGIN IN BROWSER ==")
print("Client secret:", client_secret_path)

flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
creds = flow.run_local_server(port=8080, prompt='consent')

TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

env_text = set_env_line(env_text, "GOOGLE_OAUTH_TOKEN_JSON", str(TOKEN_PATH))
ENV_PATH.write_text(env_text, encoding="utf-8")

print("new_token:", TOKEN_PATH)

print("== FIND LATEST CONTENT FILES ==")

json_files = sorted(OUT_DIR.glob("content_pack_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
md_files = sorted(OUT_DIR.glob("content_pack_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)

if not json_files or not md_files:
    raise SystemExit("ERROR: no generated content files found in jarvis_stage3_artifacts/content_factory")

json_path = json_files[0]
md_path = md_files[0]

print("json:", json_path)
print("md:", md_path)

print("== UPLOAD TO GOOGLE DRIVE ==")

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
    uploaded = drive.files().create(
        body={
            "name": path.name,
            "parents": [folder_id],
        },
        media_body=MediaFileUpload(str(path), mimetype=mime, resumable=False),
        fields="id, name, webViewLink"
    ).execute()
    return uploaded

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