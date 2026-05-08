from pathlib import Path
import sys
import json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent

agent = JarvisN8nSuperAgent(PROJECT_ROOT)
result = agent.run(
    user_task="Create a night report logger workflow and test it",
    workflow_kind="night_report_logger",
    activate=True,
    test_webhook=True,
)
print(result.summary)
print("")
print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))

if result.status not in {"tested", "active"}:
    raise SystemExit(2)