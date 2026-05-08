from app.runtime_policy import classify_error_message, compute_retry_delay, should_retry_error


def test_classify_configuration_error():
    assert classify_error_message("Assigned agent not found or disabled: critic_agent") == "configuration"


def test_classify_policy_block_error():
    assert classify_error_message("Unsafe shell command blocked by deny pattern: \\btaskkill\\b") == "policy_block"


def test_retry_delay_caps():
    assert compute_retry_delay(1) == 2.0
    assert compute_retry_delay(2) == 4.0
    assert compute_retry_delay(10) == 60.0


def test_configuration_error_is_not_retryable():
    decision = should_retry_error("Assigned agent not found or disabled: critic_agent", 1, "dev")
    assert decision.retryable is False
    assert decision.category == "configuration"


def test_transient_error_is_retryable():
    decision = should_retry_error("Timeout while contacting dependency", 1, "dev")
    assert decision.retryable is True
    assert decision.category == "transient"
    assert decision.delay_seconds > 0
