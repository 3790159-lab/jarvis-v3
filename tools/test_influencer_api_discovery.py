import re, json, requests
from pathlib import Path

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV = ROOT / ".env"

def get_env(name):
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""

key = get_env("INFLUENCER_API_KEY")
base = get_env("INFLUENCER_BASE_URL") or "https://influencerstudio.com/api/v1"

if not key:
    raise SystemExit("NO INFLUENCER_API_KEY in .env")

headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

print("BASE:", base)
print("KEY:", key[:6] + "..." + key[-4:])

paths = [
    "/me",
    "/account",
    "/user",
    "/models",
    "/influencers",
    "/images",
    "/videos",
    "/generations",
]

for p in paths:
    url = base.rstrip("/") + p
    try:
        r = requests.get(url, headers=headers, timeout=30)
        print(f"\nGET {p} -> {r.status_code}")
        print(r.text[:1000])
    except Exception as e:
        print(f"\nGET {p} -> ERROR {e}")