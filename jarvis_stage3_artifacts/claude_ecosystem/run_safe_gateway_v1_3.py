from app.services.claude_ecosystem_execution_bridge import run_safe_gateway_plan
import json

result = run_safe_gateway_plan(
    project_root=r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''',
    limit=5,
)

print(json.dumps(result, ensure_ascii=False, indent=2))