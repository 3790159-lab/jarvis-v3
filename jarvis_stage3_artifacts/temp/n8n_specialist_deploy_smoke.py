from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(r'''C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram''')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_n8n_specialist import JarvisN8nSpecialist

agent = JarvisN8nSpecialist(PROJECT_ROOT)

result = agent.handle_task(
    task="Deploy Jarvis Night Report Webhook workflow to n8n",
    deploy=True,
    test_webhook=False,
)

payload = result.deploy_result or {}
workflow_id = None

body = payload.get("body")
if isinstance(body, dict):
    workflow_id = body.get("id") or body.get("data", {}).get("id")

print(agent.format_human_summary(result))
print("")
print("DEPLOY_RESULT_JSON:")
print(json.dumps(payload, ensure_ascii=False, indent=2))
print("")
print("WORKFLOW_ID:", workflow_id)

out = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_specialist" / "runtime" / "latest_deploy_result.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({
    "workflow_id": workflow_id,
    "result": result.__dict__,
    "deploy_result": payload,
}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

if not payload.get("ok"):
    raise SystemExit(2)

if workflow_id and "True".lower() == "true":
    activate = agent._http_request(
        f"{agent.base_url}/api/v1/workflows/{workflow_id}/activate",
        method="POST",
        headers={"X-N8N-API-KEY": agent.api_key},
        timeout=30,
    )
    print("")
    print("ACTIVATE_RESULT_JSON:")
    print(json.dumps(activate, ensure_ascii=False, indent=2))

    act_out = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_specialist" / "runtime" / "latest_activate_result.json"
    act_out.write_text(json.dumps(activate, ensure_ascii=False, indent=2), encoding="utf-8")

    if not activate.get("ok"):
        raise SystemExit(3)

if "True".lower() == "true":
    # Test URL normally requires "Listen for test event" in editor.
    # Production URL requires activated/published workflow.
    latest = agent.latest_result()
    url = None
    if latest.get("found"):
        bp = latest["result"].get("blueprint") or {}
        url = bp.get("production_webhook_url") if "True".lower() == "true" else bp.get("test_webhook_url")
    if url:
        test = agent.test_webhook(url, {"source": "jarvis", "kind": "deploy_smoke", "message": "hello from Jarvis"})
        print("")
        print("WEBHOOK_TEST_JSON:")
        print(json.dumps(test, ensure_ascii=False, indent=2))