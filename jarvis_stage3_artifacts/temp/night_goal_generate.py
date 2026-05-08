from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.services.jarvis_autonomous_goal_generator import JarvisAutonomousGoalGenerator
goals = JarvisAutonomousGoalGenerator(PROJECT_ROOT).generate_goals(count=5, reason=r'''night_iteration_1''')
print(json.dumps([g.title for g in goals], ensure_ascii=False))