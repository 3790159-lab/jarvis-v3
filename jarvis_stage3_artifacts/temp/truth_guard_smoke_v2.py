from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_truth_guard import JarvisTruthGuard
guard = JarvisTruthGuard(PROJECT_ROOT)

fake = guard.guard_summary("РЎРѕР·РґР°Р№ РіСѓРіР» С‚Р°Р±Р»РёС†Сѓ", {"summary": "created"})
real_sheet = guard.guard_summary("РЎРѕР·РґР°Р№ РіСѓРіР» С‚Р°Р±Р»РёС†Сѓ", {
    "spreadsheet_id": "abc",
    "spreadsheet_url": "https://docs.google.com/spreadsheets/d/abc"
})
real_n8n = guard.guard_summary("РЎРѕР·РґР°Р№ n8n workflow", {"workflow_id": "wf123", "status": "tested"})
codefix = guard.guard_summary("Night improvement: improve n8n dynamic pipeline builder", {
    "lane": "code_improvement_lane",
    "run_id": "codefix_123",
    "status": "completed"
})

print(json.dumps({
    "fake_sheet": fake,
    "real_sheet": real_sheet,
    "real_n8n": real_n8n,
    "codefix": codefix
}, ensure_ascii=False, indent=2))

assert fake["ok"] is False
assert real_sheet["ok"] is True
assert real_n8n["ok"] is True
assert codefix["ok"] is True