from data_analysis_agent.recovery import RecoveryPolicy, RecoveryState


def test_retryable_failure_is_bounded_and_reports_remaining_attempts() -> None:
    state = RecoveryState(RecoveryPolicy(max_attempts=2))

    first = state.record("query_database:hash", "query_timeout", retryable=True)
    second = state.record("query_database:hash", "query_timeout", retryable=True)

    assert (first.attempt, first.remaining, first.retry_allowed) == (1, 1, True)
    assert first.action == "regenerate"
    assert (second.attempt, second.remaining, second.retry_allowed) == (2, 0, False)
    assert second.action == "fallback"
    assert second.fallback_category == "tool_retry_exhausted"


def test_non_retryable_failure_never_consumes_retry_budget() -> None:
    state = RecoveryState(RecoveryPolicy(max_attempts=2))

    denied = state.record("query_database:hash", "permission_denied", retryable=False)

    assert denied.attempt == 1
    assert denied.remaining == 0
    assert denied.retry_allowed is False
    assert denied.action == "stop"
    assert state.attempts_for("query_database:hash") == 0


def test_retry_budget_is_scoped_to_tool_signature() -> None:
    state = RecoveryState(RecoveryPolicy(max_attempts=2))

    state.record("query_data:first", "query_timeout", retryable=True)
    changed = state.record("query_data:changed", "query_timeout", retryable=True)

    assert changed.attempt == 1
    assert changed.remaining == 1
    assert state.exhausted("query_data:first") is False
    state.record("query_data:first", "query_timeout", retryable=True)
    assert state.exhausted("query_data:first") is True
