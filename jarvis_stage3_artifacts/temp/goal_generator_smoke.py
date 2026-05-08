from pathlib import Path
import sys, json
PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_autonomous_goal_generator import JarvisAutonomousGoalGenerator

gen = JarvisAutonomousGoalGenerator(PROJECT_ROOT)
goals = gen.generate_goals(count=5, reason="smoke")
status = gen.status()
next_task = gen.next_goal_task("fallback task")

print(json.dumps({
    "generated": [g.__dict__ for g in goals],
    "status": status,
    "next_task": next_task
}, ensure_ascii=False, indent=2, default=str))

assert len(goals) >= 3
assert "Autonomous strategic goal" in next_task
assert status["queue_size"] >= 0