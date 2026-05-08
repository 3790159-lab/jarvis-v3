from app.execution_semantics import (
    classify_error_message,
    compute_retry_delay,
    recompute_mission_status,
    should_retry_error,
)


def test_classify_configuration_error():
    assert classify_error_message("Assigned agent not found or disabled: critic_agent") == "configuration"


def test_classify_policy_block():
    assert classify_error_message("Unsafe shell command blocked by deny pattern") == "policy_block"


def test_retry_delay_caps():
    assert compute_retry_delay(1) == 2.0
    assert compute_retry_delay(2) == 4.0
    assert compute_retry_delay(10) == 60.0


def test_non_retryable_configuration():
    decision = should_retry_error("Assigned agent not found or disabled: critic_agent", 1)
    assert decision.retryable is False
    assert decision.category == "configuration"


def test_retryable_transient():
    decision = should_retry_error("Timeout while contacting dependency", 1)
    assert decision.retryable is True
    assert decision.category == "transient"
    assert decision.delay_seconds > 0


def test_recompute_completed_mission():
    summary = recompute_mission_status([
        {"status": "completed"},
        {"status": "completed"},
    ])
    assert summary["all_completed"] is True
    assert summary["recommended_mission_status"] == "completed"


def test_recompute_failed_mission():
    summary = recompute_mission_status([
        {"status": "completed"},
        {"status": "failed"},
    ])
    assert summary["has_blocking_failure"] is True
    assert summary["recommended_mission_status"] == "failed"
