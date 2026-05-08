from pathlib import Path
import sys
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_task_seeder import JarvisAutonomousTaskSeeder
print(JarvisAutonomousTaskSeeder(PROJECT_ROOT).next_task(r'''Night improvement: add service connector registry for Telegram Google Sheets Gmail Calendar n8n HTTP API'''))