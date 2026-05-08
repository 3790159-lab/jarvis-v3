from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_task_seeder import JarvisAutonomousTaskSeeder
new_tasks = JarvisAutonomousTaskSeeder(PROJECT_ROOT).seed_from_result(r'''Autonomous strategic goal: inspect Brain Executor lanes and add/plan the next safest execution lane. Prioritize Google Sheets real executor, Telegram notifier, HTTP validation, and file artifact creation. Keep changes reversible and compile-checked.''', r'''compiled_only''', r'''none''')
print(json.dumps(new_tasks, ensure_ascii=False))