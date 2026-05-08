from pathlib import Path
import sys, json

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_execution_verifier import JarvisExecutionVerifier

result_path = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\night_v6_2_brain_safe\runs\nightv62_brain_safe_20260427_031908\iteration_004_retry.json''')
result = json.loads(result_path.read_text(encoding='utf-8-sig'))
verifier = JarvisExecutionVerifier(PROJECT_ROOT)
report = verifier.verify(r'''Retry with strict evidence gate: Autonomous strategic goal: inspect Brain Executor lanes and add/plan the next safest execution lane. Prioritize Google Sheets real executor, Telegram notifier, HTTP validation, and file artifact creation. Keep changes reversible and compile-checked.

Previous verification failed. Claim type: google_sheet_creation. Missing evidence: ['spreadsheet_id', 'spreadsheet_url']. Do not claim success unless real API result/artifact is returned. If impossible, return blocked reason and exact missing connector/credential.''', result, attempt=2, max_attempts=2)
print(json.dumps(report, ensure_ascii=False, default=str))