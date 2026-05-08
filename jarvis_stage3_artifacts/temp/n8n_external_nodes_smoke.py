from pathlib import Path
import sys
import json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent

agent = JarvisN8nSuperAgent(PROJECT_ROOT)

result = agent.run(
    user_task="Create external API request workflow and test it",
    workflow_kind="http_request_probe",
    activate=True,
    test_webhook=True,
)

print(result.summary)
print("")
print(json.dumps({
    "status": result.status,
    "workflow_kind": result.workflow_kind,
    "workflow_id": result.workflow_id,
    "webhook_test_ok": bool(result.webhook_test_result and result.webhook_test_result.get("ok")),
    "webhook_test_result": result.webhook_test_result,
}, ensure_ascii=False, indent=2, default=str))

if result.status not in {"tested", "active"}:
    raise SystemExit(2)