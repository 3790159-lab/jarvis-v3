import json, requests
from pathlib import Path

ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
url = "https://influencerstudio.com/api/v1/swagger"

r = requests.get(url, timeout=30)
print("STATUS:", r.status_code)

data = r.json()

out = ROOT / "artifacts" / "influencer_swagger.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

print("SAVED:", out)
print("")
print("== PATHS ==")
for path, methods in data.get("paths", {}).items():
    print(path, "=>", ", ".join(methods.keys()))