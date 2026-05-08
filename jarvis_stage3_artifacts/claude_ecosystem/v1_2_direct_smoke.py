from app.services.claude_ecosystem_orchestrator import run_ecosystem_preflight, run_before_night_mode

r1 = run_ecosystem_preflight({"source": "direct_smoke"}, stage="direct_smoke")
print("preflight_status:", r1["status"])
print("schema:", r1["schema"])

r2 = run_before_night_mode({"source": "direct_smoke"})
print("night_preflight_status:", r2["status"])
print("risks:", r2["risks"])