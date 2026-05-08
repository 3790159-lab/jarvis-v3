from pathlib import Path
import sys

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_specialist import JarvisN8nSpecialist

agent = JarvisN8nSpecialist(PROJECT_ROOT)
result = agent.handle_task(
    task="Prepare Jarvis n8n workflow blueprint",
    deploy=False,
    test_webhook=False,
)
print(agent.format_human_summary(result))