from app.services.ultra_upgrade_engine import build_ultra_upgrade_plan, build_full_creator_package
from app.services.claude_ecosystem_execution_bridge import run_safe_night_preflight
import json

ultra = build_ultra_upgrade_plan({"source": "direct_v1_3_smoke"})
print("ultra_status:", ultra["status"])

creator = build_full_creator_package({
    "source": "direct_v1_3_smoke",
    "product_name": "Jarvis Operator Command Center",
    "goal": "Operator dashboard for missions, approvals, artifacts and night mode",
})
print("creator_status:", creator["status"])
print("creator_package:", creator["data"]["package_path"])

night = run_safe_night_preflight({"source": "direct_v1_3_smoke"})
print("night_status:", night["status"])