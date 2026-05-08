import base64
import json
import re
import time
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV_PATH = PROJECT_ROOT / ".env"
TOKEN_PATH = PROJECT_ROOT / "google_oauth_token_drive.json"
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "content_factory_v3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = ["https://www.googleapis.com/auth/drive"]

def env_value(text, names):
    for name in names:
        m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""

env_text = ENV_PATH.read_text(encoding="utf-8", errors="replace")
openai_key = env_value(env_text, ["OPENAI_API_KEY", "LLM_API_KEY"])

if not openai_key:
    raise SystemExit("ERROR: OPENAI_API_KEY or LLM_API_KEY not found in .env")
if not TOKEN_PATH.exists():
    raise SystemExit(f"ERROR: Google token not found: {TOKEN_PATH}. First run OAuth Drive login.")

run_id = time.strftime("%Y%m%d_%H%M%S")
run_dir = OUT_DIR / f"run_{run_id}"
run_dir.mkdir(parents=True, exist_ok=True)

brief = """
Создай премиальный контент-пак для вымышленной adult 18+ AI-модели для Instagram:
luxury lifestyle, travel, premium aesthetic, cinematic style.
Нужны 12 assets: post, story, reels script, caption, hashtags, bio, carousel plan,
short video prompt, long video prompt, telegram post, X/Twitter post, Google Drive manifest.
"""

system_rules = """
Rules:
- Fictional adult 18+ character only.
- Do not imitate real people, celebrities, private people, or minors.
- No nudity.
- No explicit sexual content.
- Instagram-safe.
- Premium luxury travel/lifestyle visual style.
- Russian language.
Return ONLY valid JSON.
"""

json_schema_prompt = """
Required JSON structure:
{
  "ok": true,
  "pipeline": "jarvis_content_factory_v3",
  "brief_summary": "...",
  "brand_direction": {
    "tone": "...",
    "style": "...",
    "audience": "..."
  },
  "assets_count": 12,
  "assets": [
    {
      "index": 1,
      "kind": "instagram_post",
      "title": "...",
      "content": "...",
      "visual_prompt": "...",
      "hashtags": ["..."],
      "status": "generated"
    }
  ],
  "image_generation_pack": {
    "prompts": [
      {
        "index": 1,
        "filename": "asset_001.png",
        "prompt": "..."
      }
    ]
  },
  "google_drive_manifest": {
    "folder_name": "Jarvis_Content_Factory_V3",
    "files": []
  },
  "operator_report": {
    "content_policy_check": "...",
    "recommendations": "..."
  }
}
Make exactly 12 assets and exactly 6 image prompts.
"""

print("== 1) GENERATE TEXT CONTENT PACK ==")

resp = requests.post(
    "https://api.openai.com/v1/responses",
    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
    json={
        "model": "gpt-4.1-mini",
        "input": system_rules + "\n\n" + brief + "\n\n" + json_schema_prompt,
        "temperature": 0.7
    },
    timeout=180,
)

if resp.status_code >= 400:
    raise SystemExit(f"OPENAI TEXT ERROR {resp.status_code}: {resp.text}")

data = resp.json()
text = data.get("output_text", "")

if not text and isinstance(data.get("output"), list):
    for item in data["output"]:
        for c in item.get("content", []):
            text += c.get("text", "")

text = text.strip()
text = re.sub(r"^```json\s*", "", text, flags=re.I).strip()
text = re.sub(r"^```\s*", "", text).strip()
text = re.sub(r"\s*```$", "", text).strip()

try:
    content = json.loads(text)
except Exception:
    content = {
        "ok": False,
        "pipeline": "jarvis_content_factory_v3",
        "error": "Text model returned invalid JSON",
        "raw_text": text,
    }

json_path = run_dir / "content_pack.json"
json_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")

print("text_json:", json_path)

print("== 2) BUILD FIXED MARKDOWN ==")

md = []
md.append("# Jarvis Content Factory V3 Result")
md.append("")
md.append(f"Run ID: `{run_id}`")
md.append("")
md.append("## Summary")
md.append(str(content.get("brief_summary", "")))
md.append("")
md.append("## Brand Direction")
bd = content.get("brand_direction", {})
if isinstance(bd, dict):
    for k, v in bd.items():
        md.append(f"- **{k}:** {v}")
md.append("")
md.append("## Assets")

assets = content.get("assets", [])
if not isinstance(assets, list):
    assets = []

for i, a in enumerate(assets, start=1):
    if not isinstance(a, dict):
        continue
    idx = a.get("index") or i
    kind = a.get("kind") or a.get("type") or "asset"
    title = a.get("title") or a.get("id") or f"Asset {idx}"
    md.append("")
    md.append(f"### {idx}. {kind} — {title}")
    for field in ["description", "content", "hook", "script", "visual_prompt", "style", "status"]:
        if a.get(field):
            md.append(f"**{field}:** {a.get(field)}")
    if isinstance(a.get("hashtags"), list):
        md.append("**hashtags:** " + " ".join(a["hashtags"]))
    if isinstance(a.get("shot_list"), list):
        md.append("**shot_list:**")
        for s in a["shot_list"]:
            md.append(f"- {s}")
    if isinstance(a.get("slides"), list):
        md.append("**slides:**")
        for s in a["slides"]:
            md.append(f"- {s}")

md.append("")
md.append("## Image Generation Pack")
igp = content.get("image_generation_pack", {})
md.append(json.dumps(igp, ensure_ascii=False, indent=2))
md.append("")
md.append("## Operator Report")
md.append(json.dumps(content.get("operator_report", {}), ensure_ascii=False, indent=2))

md_path = run_dir / "content_pack.md"
md_path.write_text("\n".join(md), encoding="utf-8")

print("markdown:", md_path)

print("== 3) GENERATE REAL IMAGES ==")

prompts = []
igp = content.get("image_generation_pack", {})
if isinstance(igp, dict) and isinstance(igp.get("prompts"), list):
    prompts = igp["prompts"]

if not prompts:
    for i, a in enumerate(assets[:6], start=1):
        prompts.append({
            "index": i,
            "filename": f"asset_{i:03d}.png",
            "prompt": a.get("visual_prompt") or a.get("description") or a.get("content") or f"Premium cinematic luxury lifestyle fictional adult AI model asset {i}"
        })

image_files = []
max_images = min(6, len(prompts))

for p in prompts[:max_images]:
    idx = int(p.get("index") or len(image_files) + 1)
    filename = p.get("filename") or f"asset_{idx:03d}.png"
    if not filename.lower().endswith(".png"):
        filename = Path(filename).stem + ".png"

    raw_prompt = p.get("prompt") or "Premium cinematic luxury lifestyle portrait, fictional adult 18+ AI model, Instagram-safe"
    safe_prompt = (
        raw_prompt
        + "\n\nSafety: fictional adult 18+ character only, no nudity, no explicit sexual content, "
          "no real person, no celebrity, no minor, Instagram-safe luxury fashion editorial."
    )

    print(f"image_{idx}: generating {filename}")

    ir = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
        json={
            "model": "gpt-image-1",
            "prompt": safe_prompt,
            "size": "1024x1536",
            "n": 1
        },
        timeout=240,
    )

    if ir.status_code >= 400:
        err_path = run_dir / f"asset_{idx:03d}_image_error.txt"
        err_path.write_text(ir.text, encoding="utf-8")
        print(f"image_{idx}: ERROR saved to {err_path}")
        continue

    img_data = ir.json()
    b64 = None
    try:
        b64 = img_data["data"][0]["b64_json"]
    except Exception:
        pass

    if not b64:
        err_path = run_dir / f"asset_{idx:03d}_image_no_b64.json"
        err_path.write_text(json.dumps(img_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"image_{idx}: no b64 saved to {err_path}")
        continue

    image_path = run_dir / filename
    image_path.write_bytes(base64.b64decode(b64))
    image_files.append(image_path)
    print("saved:", image_path)

print("images_generated:", len(image_files))

print("== 4) UPLOAD TO GOOGLE DRIVE ==")

creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
drive = build("drive", "v3", credentials=creds)

folder_name = f"Jarvis_Content_Factory_V3_{run_id}"
folder = drive.files().create(
    body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
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
uploaded.append(upload(json_path, "application/json"))
uploaded.append(upload(md_path, "text/markdown"))

for img in image_files:
    uploaded.append(upload(img, "image/png"))

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
    "pipeline": "jarvis_content_factory_v3",
    "run_id": run_id,
    "folder_url": folder.get("webViewLink"),
    "uploaded_files": uploaded,
    "local_run_dir": str(run_dir),
    "images_generated": len(image_files),
}
summary_path = run_dir / "drive_upload_summary.json"
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

print("")
print("== GOOGLE DRIVE RESULT ==")
print("folder_url:", summary["folder_url"])
for f in uploaded:
    print(f"{f.get('name')}: {f.get('webViewLink')}")
print("")
print("local_run_dir:", run_dir)
print("DONE")