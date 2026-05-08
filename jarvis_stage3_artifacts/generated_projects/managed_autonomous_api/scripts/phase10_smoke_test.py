from app.services.autonomy_control import AutonomyControlService

service = AutonomyControlService()

scores = service.score_agents()
evaluation = service.evaluate_action(
    agent_id="executor_core",
    action_type="execute",
    requested_tools=["shell", "http"],
    objective="Run risky operation for smoke test"
)
approvals = service.list_approvals()

print("PHASE10_SMOKE_OK")
print({
    "scores_count": len(scores["agent_scores"]),
    "requires_approval": evaluation["decision"]["requires_approval"],
    "approval_queue_count": approvals["count"]
})
