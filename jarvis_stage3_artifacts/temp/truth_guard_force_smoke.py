from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.services.jarvis_truth_guard as mod
from app.services.jarvis_truth_guard import JarvisTruthGuard

print("IMPORTED_FROM:", mod.__file__)

guard = JarvisTruthGuard(PROJECT_ROOT)

fake = guard.guard_summary("РЎРѕР·РґР°Р№ РіСѓРіР» С‚Р°Р±Р»РёС†Сѓ", {"summary": "created"})
real = guard.guard_summary("РЎРѕР·РґР°Р№ РіСѓРіР» С‚Р°Р±Р»РёС†Сѓ", {
    "spreadsheet_id": "abc",
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/abc"
})
n8n = guard.guard_summary("РЎРѕР·РґР°Р№ n8n workflow", {"workflow_id": "wf123", "status": "tested"})

print(json.dumps({"fake": fake, "real": real, "n8n": n8n}, ensure_ascii=False, indent=2))

assert fake["ok"] is False, fake
assert fake["claim_type"] == "google_sheet_creation", fake
assert real["ok"] is True, real
assert n8n["ok"] is True, n8n