from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent

agent = JarvisN8nSuperAgent(PROJECT_ROOT)
result = agent.run(
    user_task="Create dynamic pipeline with webhook, validation, external API, transformation, decision and final operator report",
    workflow_kind="dynamic_pipeline",
    activate=True,
    test_webhook=True,
)
print(result.summary)
print(json.dumps({
    "status": result.status,
    "kind": result.workflow_kind,
    "workflow_id": result.workflow_id,
    "test_ok": bool(result.webhook_test_result and result.webhook_test_result.get("ok")),
}, ensure_ascii=False, indent=2))
if result.status != "tested":
    raise SystemExit(2)