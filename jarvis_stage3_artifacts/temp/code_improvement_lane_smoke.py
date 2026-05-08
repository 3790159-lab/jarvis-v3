from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_brain_executor import JarvisBrainExecutor

executor = JarvisBrainExecutor(PROJECT_ROOT)
result = executor.execute("Night self-fix: improve UTF-8, fix errors, improve night loop safety and save lessons")

print(result.human_summary)
print(json.dumps({
    "status": result.status,
    "primary_result": result.primary_result,
    "next_actions": result.next_actions,
}, ensure_ascii=False, indent=2, default=str))

if result.status not in {"completed", "completed_with_warnings"}:
    raise SystemExit(2)