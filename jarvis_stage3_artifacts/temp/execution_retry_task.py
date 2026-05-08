from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_execution_verifier import JarvisExecutionVerifier

verification = json.loads(Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\temp\last_verification.json''').read_text(encoding='utf-8'))
print(JarvisExecutionVerifier(PROJECT_ROOT).retry_task_text(r'''Autonomous strategic goal: inspect Brain Executor lanes and add/plan the next safest execution lane. Prioritize Google Sheets real executor, Telegram notifier, HTTP validation, and file artifact creation. Keep changes reversible and compile-checked.''', verification))