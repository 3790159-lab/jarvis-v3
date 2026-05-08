from app.services.autonomy_control import AutonomyControlService


def test_score_agents_runs():
    service = AutonomyControlService()
    result = service.score_agents()
    assert result["status"] == "ok"
    assert isinstance(result["agent_scores"], dict)


def test_evaluate_action_requires_approval_for_risky_combo():
    service = AutonomyControlService()
    service.score_agents()

    result = service.evaluate_action(
        agent_id="executor_core",
        action_type="execute",
        requested_tools=["shell", "http"],
        objective="Run risky operation"
    )
    assert result["status"] == "ok"
    assert result["decision"]["requires_approval"] is True


def test_approval_queue_roundtrip():
    service = AutonomyControlService()
    service.score_agents()

    result = service.evaluate_action(
        agent_id="executor_core",
        action_type="high_risk",
        requested_tools=["shell"],
        objective="Delete or destructive action"
    )
    approvals = service.list_approvals()
    assert approvals["count"] >= 1

    approval_id = approvals["approval_queue"][-1]["approval_id"]
    decided = service.decide_approval(approval_id, "approved")
    assert decided["status"] == "ok"
    assert decided["approval"]["status"] == "approved"
