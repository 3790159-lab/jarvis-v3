from pathlib import Path
import sys
import json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
TASK_PATH = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\temp\brain_executor_task.txt''')

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_brain_executor import JarvisBrainExecutor

task = TASK_PATH.read_text(encoding="utf-8")

executor = JarvisBrainExecutor(PROJECT_ROOT)
result = executor.execute(task)

print(result.human_summary)
print(json.dumps({
    "status": result.status,
    "execution_id": result.execution_id,
    "primary_result": result.primary_result,
    "next_actions": result.next_actions,
}, ensure_ascii=False, indent=2, default=str))

if result.status not in {"completed", "completed_with_warnings"}:
    raise SystemExit(2)