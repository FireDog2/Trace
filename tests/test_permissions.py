from __future__ import annotations

import pytest

from data_analysis_agent.permissions import (
    ApprovalAlreadyDecided,
    ApprovalRepository,
    PermissionLevel,
    PermissionPolicy,
    ToolAction,
)


@pytest.fixture
def policy() -> PermissionPolicy:
    return PermissionPolicy()


@pytest.fixture
def approval_repository(db_connection) -> ApprovalRepository:
    return ApprovalRepository(db_connection)


@pytest.mark.parametrize(
    "tool_name",
    [
        "inspect_workspace",
        "inspect_dataset",
        "query_data",
        "query_database",
        "run_python",
        "save_artifact",
        "write_report",
        "update_plan",
        "request_user_input",
    ],
)
def test_low_risk_project_actions_are_allowed(
    policy: PermissionPolicy,
    tool_name: str,
) -> None:
    decision = policy.classify(ToolAction(tool_name, {"project_id": "p1"}))

    assert decision.level is PermissionLevel.ALLOW


def test_external_path_requires_approval(policy: PermissionPolicy, tmp_path) -> None:
    decision = policy.classify(
        ToolAction("read_external_file", {"path": str(tmp_path / "outside.csv")})
    )

    assert decision.level is PermissionLevel.REQUIRE_APPROVAL


def test_source_overwrite_is_denied(policy: PermissionPolicy) -> None:
    decision = policy.classify(
        ToolAction("run_python", {"declared_outputs": ["source/data.csv"]})
    )

    assert decision.level is PermissionLevel.DENY


def test_query_database_sql_is_deferred_to_the_gateway(
    policy: PermissionPolicy,
) -> None:
    decision = policy.classify(
        ToolAction("query_database", {"sql": "DROP TABLE operations"})
    )

    assert decision.level is PermissionLevel.ALLOW


def test_lead_coordination_tools_are_automatic(policy: PermissionPolicy) -> None:
    for tool_name in ("spawn_teammate", "list_teammates", "await_teammates", "cancel_teammate"):
        assert policy.classify(ToolAction(tool_name, {})).level is PermissionLevel.ALLOW


def test_approval_decision_is_persisted_once(
    approval_repository: ApprovalRepository,
    task,
) -> None:
    approval = approval_repository.request(
        task.id,
        ToolAction("network_access", {"url": "https://example.invalid"}),
        "Retrieve an external benchmark",
    )

    decided = approval_repository.decide(approval.id, approved=True)

    assert decided.status == "approved"
    assert decided.decided_at is not None
    with pytest.raises(ApprovalAlreadyDecided):
        approval_repository.decide(approval.id, approved=False)


def test_approved_action_can_be_consumed_only_once(
    approval_repository: ApprovalRepository,
    task,
) -> None:
    action = ToolAction("overwrite_artifact", {"path": "derived/result.csv"})
    approval = approval_repository.request(task.id, action, "Replace derived output")
    approval_repository.decide(approval.id, approved=True)

    assert approval_repository.consume_approved(task.id, action) is True
    assert approval_repository.consume_approved(task.id, action) is False
