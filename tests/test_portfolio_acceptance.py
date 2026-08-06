"""Deterministic component acceptance path; it uses a fake PostgreSQL driver, not a live DB."""
from __future__ import annotations

from data_analysis_agent.data_sources import AccessLevel
from data_analysis_agent.domain import ArtifactType
from data_analysis_agent.permissions import ApprovalRepository
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.reporting import ReportService
from data_analysis_agent.skills import CuratorAgent, SkillCandidateRepository
from data_analysis_agent.team import AgentRole, AgentRunStatus, ROLE_TOOLS, TeamRepository
from data_analysis_agent.tracing import TraceContext, TraceRepository

_SKILL = """---
name: safe-sql-diagnostic
description: Safe read-only diagnostic.
---
## When to use
Use for governed PostgreSQL analysis.
## Inputs
Question and approved source.
## Workflow
Validate, explain, then query.
## Safety gates
Keep approvals active.
## Failure recovery
Return a safe retry summary.
"""


def test_portfolio_golden_path_is_safe_observable_and_replayable(
    db_connection, project, task, workspace, artifact_repository, source_repository,
    sql_gateway, fake_driver, task_repository,
) -> None:
    """Exercise real gateway/report/trace/skill boundaries without an API key."""
    source = source_repository.create(project.id, "Business review", "postgresql://demo:secret@localhost/review")
    source_repository.replace_policies(source.id, {
        ("orders", None): AccessLevel.ALLOWED,
        ("orders", "customer_email"): AccessLevel.APPROVAL_REQUIRED,
    })
    team = TeamRepository(db_connection)
    teammates = [
        team.create_run(task.id, AgentRole.KNOWLEDGE, "Find profit definition"),
        team.create_run(task.id, AgentRole.SQL, "Query regional profit"),
        team.create_run(task.id, AgentRole.REPORT, "Write evidence-linked conclusion"),
    ]
    for teammate in teammates:
        team.transition_run(teammate.id, AgentRunStatus.WORKING)
        team.complete_run(teammate.id, '{"status":"handoff"}')
    persisted = team.list_runs(task.id)
    assert {item.role for item in persisted} == {AgentRole.KNOWLEDGE, AgentRole.SQL, AgentRole.REPORT}
    assert all(item.status is AgentRunStatus.COMPLETED for item in persisted)
    assert "query_database" not in ROLE_TOOLS[AgentRole.KNOWLEDGE]
    assert "query_database" in ROLE_TOOLS[AgentRole.SQL]
    assert "query_database" not in ROLE_TOOLS[AgentRole.REPORT]

    restricted = sql_gateway.execute(task.id, project.id, source.id, "SELECT region, customer_email FROM orders")
    assert restricted.status == "approval_required" and restricted.approval_id
    ApprovalRepository(db_connection).decide(restricted.approval_id, approved=True)
    approved = sql_gateway.execute(task.id, project.id, source.id, "SELECT region, customer_email FROM orders")
    assert approved.status == "succeeded" and approved.artifact_id

    fake_driver.query_error = TimeoutError("statement timeout on postgresql://demo:secret@localhost/review")
    timeout = sql_gateway.execute(task.id, project.id, source.id, "SELECT region FROM orders")
    assert timeout.error_category == "query_timeout" and timeout.retryable
    fake_driver.query_error = None
    retry = sql_gateway.execute(task.id, project.id, source.id, "SELECT region FROM orders")
    assert retry.status == "succeeded" and retry.artifact_id

    report = ReportService(workspace, artifact_repository, task_repository).publish(
        task.id, f"# Review\n\n## Key Findings\n\nSafe retry evidence: artifact://{retry.artifact_id}")
    assert report.artifact_type is ArtifactType.MARKDOWN_REPORT
    assert artifact_repository.evidence_for(report.id) == (retry.artifact_id,)

    traces = TraceRepository(db_connection)
    run = traces.start_run(task.id)
    traces.append(run.id, task.id, "agent.started", "completed", {"role": "lead"})
    traces.append(run.id, task.id, "tool.completed", "retryable_error", {"tool": "query_database"}, attempt=1, error_category="query_timeout")
    traces.append(run.id, task.id, "tool.retry", "retrying", {"tool": "query_database", "remaining": 1}, attempt=1, error_category="query_timeout")
    traces.append(run.id, task.id, "tool.completed", "completed", {"tool": "query_database"}, attempt=2, artifact_ids=(retry.artifact_id,))
    traces.finish_run(run.id, "completed")
    accepted = traces.accept_run(run.id)
    curator_run = team.create_run(
        task.id,
        AgentRole.CURATOR,
        "Curate accepted workflow",
        trace_context=TraceContext(accepted.id, task.id),
    )
    candidates = SkillCandidateRepository(db_connection)
    curated = CuratorAgent(
        traces=traces,
        candidates=candidates,
        provider=ScriptedProvider([ModelResponse(_SKILL, ())]),
        teams=team,
        model_name="deterministic-curator",
    ).run(curator_run.id)
    candidate = candidates.get_for_run(accepted.id)
    assert accepted.accepted and curated.status is AgentRunStatus.COMPLETED
    assert curated.trace_run_id == accepted.id and ROLE_TOOLS[AgentRole.CURATOR] == frozenset()
    assert candidate.enabled is False and candidate.status == "candidate"
