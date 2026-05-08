from pathlib import Path
import sys
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_goal_generator import JarvisAutonomousGoalGenerator
print(JarvisAutonomousGoalGenerator(PROJECT_ROOT).next_goal_task(r'''Autonomous follow-up: improve Telegram UX for task status, completion notifications and truth-guarded answers.'''))