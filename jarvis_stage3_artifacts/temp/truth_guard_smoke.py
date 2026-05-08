from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_truth_guard import JarvisTruthGuard

guard = JarvisTruthGuard(PROJECT_ROOT)

fake = guard.guard_summary("РЎРѕР·РґР°Р№ РіСѓРіР» С‚Р°Р±Р»РёС†Сѓ", {"summary": "created"})
real_n8n = guard.guard_summary("РЎРѕР·РґР°Р№ n8n workflow", {"workflow_id": "abc123", "status": "tested"})

print(json.dumps({"fake_sheet": fake, "real_n8n": real_n8n}, ensure_ascii=False, indent=2))

assert fake["ok"] is False
assert real_n8n["ok"] is True