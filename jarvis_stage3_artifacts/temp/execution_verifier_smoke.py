from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_execution_verifier import JarvisExecutionVerifier

v = JarvisExecutionVerifier(PROJECT_ROOT)

fake_sheet = v.verify("РЎРѕР·РґР°Р№ РіСѓРіР» С‚Р°Р±Р»РёС†Сѓ", {"execution": {"primary_result": {"summary": "created"}}})
real_code = v.verify("Improve code", {"execution": {"primary_result": {"lane": "code_improvement_lane", "run_id": "codefix_1", "status": "completed"}}})
real_n8n = v.verify("РЎРѕР·РґР°Р№ n8n workflow", {"execution": {"primary_result": {"workflow_id": "wf1", "status": "tested"}}})

print(json.dumps({
    "fake_sheet": fake_sheet,
    "real_code": real_code,
    "real_n8n": real_n8n
}, ensure_ascii=False, indent=2, default=str))

assert fake_sheet["verdict"] == "needs_retry", fake_sheet
assert real_code["verdict"] == "verified_completed", real_code
assert real_n8n["verdict"] == "verified_completed", real_n8n