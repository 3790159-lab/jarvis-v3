from app.services.self_healing import check_backend, recommend_recovery
from app.hooks.hooks import run_hooks
from app.agents.qa_agent import QAAgent
from app.agents.architect_agent import ArchitectAgent
from app.services.claude_ecosystem_registry import get_claude_ecosystem_capabilities

snap = check_backend()
print("self_healing:", snap.to_dict())
print("recovery:", recommend_recovery(snap))
print("hook:", run_hooks("ecosystem_v1_1_smoke", {"source": "powershell"}))
print("qa:", QAAgent().run({"checks": ["compile", "health"]}))
print("architect:", ArchitectAgent().run({}))
print("registry:", get_claude_ecosystem_capabilities()["schema"])