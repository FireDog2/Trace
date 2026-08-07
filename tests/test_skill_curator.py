from __future__ import annotations

import pytest

from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.skills import (
    CuratorAgent,
    SkillCandidateRepository,
    SkillEligibilityError,
    SkillStaticValidator,
    WorkflowCurator,
)
from data_analysis_agent.team import AgentRunStatus, ROLE_TOOLS, TeamRepository
from data_analysis_agent.team_types import AgentRole
from data_analysis_agent.tracing import TraceContext, TraceRepository

_SAFE = "---\nname: safe-sql-diagnostic\ndescription: Safe read-only SQL analysis.\n---\n## When to use\nFor read-only analysis.\n## Inputs\nA question and approved source.\n## Workflow\nValidate and execute safely.\n## Safety gates\nKeep approvals active.\n## Failure recovery\nReturn a safe error summary.\n"

def test_curator_only_accepts_completed_evidenced_runs(db_connection, task) -> None:
    traces = TraceRepository(db_connection); run = traces.start_run(task.id)
    provider = ScriptedProvider([ModelResponse(_SAFE, ())])
    curator = WorkflowCurator(traces, SkillCandidateRepository(db_connection), provider)
    with pytest.raises(SkillEligibilityError): curator.create_candidate(run.id, "scripted")
    traces.append(run.id, task.id, "tool.completed", "completed", {"tool": "query_database"}, attempt=2)
    traces.finish_run(run.id, "completed"); traces.accept_run(run.id)
    candidate = curator.create_candidate(run.id, "scripted")
    assert candidate.status == "candidate" and candidate.enabled is False
    evidence = provider.requests[0].messages[0].content
    assert '"tool":"query_database"' in evidence
    assert '"attempt":2' in evidence

@pytest.mark.parametrize("unsafe", ["TODO", "Use password secret", "Automatically bypass approval"])
def test_static_validator_rejects_unsafe_text(unsafe: str) -> None:
    with pytest.raises(Exception): SkillStaticValidator().validate(_SAFE + unsafe)


def test_durable_curator_has_no_tools_and_creates_one_disabled_candidate(
    db_connection, task
) -> None:
    traces = TraceRepository(db_connection)
    source = traces.start_run(task.id)
    traces.append(source.id, task.id, "tool.completed", "completed", {"tool": "query_database"})
    traces.finish_run(source.id, "completed")
    traces.accept_run(source.id)
    teams = TeamRepository(db_connection)
    curator_run = teams.create_run(
        task.id,
        AgentRole.CURATOR,
        "Curate accepted run",
        trace_context=TraceContext(source.id, task.id),
    )
    candidates = SkillCandidateRepository(db_connection)
    curator = CuratorAgent(
        traces=traces,
        candidates=candidates,
        provider=ScriptedProvider([ModelResponse(_SAFE, ())]),
        teams=teams,
        model_name="scripted-curator",
    )

    completed = curator.run(curator_run.id)
    replayed = curator.run(curator_run.id)

    assert ROLE_TOOLS[AgentRole.CURATOR] == frozenset()
    assert completed.status is AgentRunStatus.COMPLETED
    assert replayed.id == completed.id
    candidate = candidates.get_for_run(source.id)
    assert candidate.enabled is False and candidate.status == "candidate"
    assert db_connection.execute(
        "SELECT COUNT(*) FROM skill_candidates WHERE source_run_id=?", (source.id,)
    ).fetchone()[0] == 1


def test_curator_fails_safely_when_source_run_was_not_accepted(
    db_connection, task
) -> None:
    traces = TraceRepository(db_connection)
    source = traces.start_run(task.id)
    traces.append(source.id, task.id, "tool.completed", "completed", {})
    traces.finish_run(source.id, "completed")
    teams = TeamRepository(db_connection)
    curator_run = teams.create_run(
        task.id,
        AgentRole.CURATOR,
        "Must not curate",
        trace_context=TraceContext(source.id, task.id),
    )

    failed = CuratorAgent(
        traces=traces,
        candidates=SkillCandidateRepository(db_connection),
        provider=ScriptedProvider([ModelResponse(_SAFE, ())]),
        teams=teams,
        model_name="scripted-curator",
    ).run(curator_run.id)

    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == "skill_ineligible"
    assert db_connection.execute("SELECT COUNT(*) FROM skill_candidates").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("response", "category"),
    [
        (None, "provider_error"),
        (ModelResponse("invalid skill", ()), "invalid_skill_candidate"),
    ],
)
def test_curator_provider_and_validation_failures_are_durable_and_safe(
    db_connection, task, response, category
) -> None:
    traces = TraceRepository(db_connection)
    source = traces.start_run(task.id)
    traces.append(source.id, task.id, "tool.completed", "completed", {})
    traces.finish_run(source.id, "completed")
    traces.accept_run(source.id)
    teams = TeamRepository(db_connection)
    run = teams.create_run(
        task.id, AgentRole.CURATOR, "Curate",
        trace_context=TraceContext(source.id, task.id),
    )
    provider = ScriptedProvider([] if response is None else [response])

    failed = CuratorAgent(
        traces=traces,
        candidates=SkillCandidateRepository(db_connection),
        provider=provider,
        teams=teams,
        model_name="scripted-curator",
    ).run(run.id)

    assert failed.status is AgentRunStatus.FAILED
    assert failed.error_category == category


def test_approved_candidate_remains_disabled(db_connection, task) -> None:
    traces = TraceRepository(db_connection)
    source = traces.start_run(task.id)
    traces.finish_run(source.id, "completed")
    traces.accept_run(source.id)
    candidates = SkillCandidateRepository(db_connection)
    candidate = candidates.create(source.id, "safe-sql-diagnostic", _SAFE, "scripted")

    approved = candidates.decide(candidate.id, True)

    assert approved.status == "approved"
    assert approved.enabled is False
