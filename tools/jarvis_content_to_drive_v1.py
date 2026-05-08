import os, re, json, time, requests
from pathlib import Path

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV_PATH = PROJECT_ROOT / ".env"
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "content_factory"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def env_value(text, names):
    for name in names:
        m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""

env_text = ENV_PATH.read_text(encoding="utf-8", errors="replace")

openai_key = env_value(env_text, ["OPENAI_API_KEY", "LLM_API_KEY"])
service_json = env_value(env_text, ["GOOGLE_SERVICE_ACCOUNT_JSON"])
oauth_token_json = env_value(env_text, ["GOOGLE_OAUTH_TOKEN_JSON"])

if not openai_key:
    raise SystemExit("ERROR: OPENAI_API_KEY or LLM_API_KEY not found in .env")

prompt = """
Create a real ready-to-use content package for a fictional adult 18+ AI model Instagram account.

Rules:
- fictional adult 18+ only
- no real person imitation
- no minors
- no nudity
- no explicit sexual content
- premium luxury lifestyle, travel, cinematic, Instagram-safe
- language: Russian

Return ONLY valid JSON with:
ok, pipeline, brief_summary, brand_direction, assets_count=12, assets array,
image_generation_pack, google_drive_manifest, operator_report.
"""

print("== GENERATE CONTENT WITH OPENAI ==")

r = requests.post(
    "https://api.openai.com/v1/responses",
    headers={
        "Authorization": f"Bearer {openai_key}",
        "Content-Type": "application/json",
    },
    json={
        "model": "gpt-4.1-mini",
        "input": prompt,
        "temperature": 0.7,
    },
    timeout=180,
)

if r.status_code >= 400:
    raise SystemExit(f"OPENAI ERROR {r.status_code}: {r.text}")

data = r.json()
text = data.get("output_text", "")

if not text and isinstance(data.get("output"), list):
    for item in data["output"]:
        for c in item.get("content", []):
            text += c.get("text", "")

text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

try:
    content = json.loads(text)
except Exception:
    content = {
        "ok": False,
        "error": "OpenAI returned non-json text",
        "raw_text": text,
    }

run_id = time.strftime("%Y%m%d_%H%M%S")
json_path = OUT_DIR / f"content_pack_{run_id}.json"
md_path = OUT_DIR / f"content_pack_{run_id}.md"

json_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

md = ["# Jarvis Content Factory Result", ""]
md.append(f"Run ID: `{run_id}`")
md.append("")
md.append("## Summary")
md.append(str(content.get("brief_summary", "")))
md.append("")
md.append("## Assets")
for a in content.get("assets", []):
    md.append(f"### {a.get('index')}. {a.get('kind')}")
    if a.get("title"): md.append(f"**Title:** {a.get('title')}")
    if a.get("content"): md.append(a.get("content"))
    if a.get("visual_prompt"): md.append(f"**Visual prompt:** {a.get('visual_prompt')}")
    if a.get("script"): md.append(f"**Script:** {a.get('script')}")
    md.append("")
md_path.write_text("\n".join(md), encoding="utf-8")

print("local_json:", json_path)
print("local_md:", md_path)

print("== CONNECT GOOGLE DRIVE ==")

SCOPES = ["https://www.googleapis.com/auth/drive"]

creds = None

if oauth_token_json:
    token_path = Path(oauth_token_json)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

if creds is None and service_json:
    sa_path = Path(service_json)
    if sa_path.exists():
        creds = service_account.Credentials.from_service_account_file(str(sa_path), scopes=SCOPES)

if creds is None:
    raise SystemExit(
        "ERROR: Google Drive credentials not found. Need GOOGLE_OAUTH_TOKEN_JSON or GOOGLE_SERVICE_ACCOUNT_JSON in .env"
    )

drive = build("drive", "v3", credentials=creds)

folder_name = f"Jarvis_Content_Factory_{run_id}"

folder_meta = {
    "name": folder_name,
    "mimeType": "application/vnd.google-apps.folder",
}
folder = drive.files().create(body=folder_meta, fields="id, webViewLink").execute()
folder_id = folder["id"]

def upload_file(path, mime):
    meta = {
        "name": path.name,
        "parents": [folder_id],
    }
    media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
    f = drive.files().create(
        body=meta,
        media_body=media,
        fields="id, name, webViewLink",
    ).execute()
    return f

json_file = upload_file(json_path, "application/json")
md_file = upload_file(md_path, "text/markdown")

# Make folder readable by link
try:
    drive.permissions().create(
        fileId=folder_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
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