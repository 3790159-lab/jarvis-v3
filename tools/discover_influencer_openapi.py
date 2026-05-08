import re, json, requests
from pathlib import Path

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
ENV = ROOT / ".env"

def get_env(name):
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^{re.escape(name)}=(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""

key = get_env("INFLUENCER_API_KEY")
headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}

candidates = [
    "https://influencerstudio.com/api/v1/openapi.json",
    "https://influencerstudio.com/openapi.json",
    "https://influencerstudio.com/swagger.json",
    "https://influencerstudio.com/api-docs.json",
    "https://influencerstudio.com/docs/openapi.json",
    "https://influencerstudio.com/api/v1/docs",
]

for url in candidates:
    print("\nTRY:", url)
    try:
        r = requests.get(url, headers=headers, timeout=30)
        print("STATUS:", r.status_code)
        print(r.text[:1000])

        if r.status_code == 200 and "paths" in r.text:
            out = ROOT / "artifacts" / "influencer_openapi.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(r.text, encoding="utf-8")
            data = r.json()
            print("\n== PATHS ==")
            for p, methods in data.get("paths", {}).items():
                print(p, list(methods.keys()))
            print("\nSAVED:", out)
            break
    except Exception as e:
        print("ERROR:", e)