from __future__ import annotations

import pytest

from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.skills import SkillCandidateRepository, SkillEligibilityError, SkillStaticValidator, WorkflowCurator
from data_analysis_agent.tracing import TraceRepository

_SAFE = "---\nname: safe-sql-diagnostic\ndescription: Safe read-only SQL analysis.\n---\n## When to use\nFor read-only analysis.\n## Inputs\nA question and approved source.\n## Workflow\nValidate and execute safely.\n## Safety gates\nKeep approvals active.\n## Failure recovery\nReturn a safe error summary.\n"

def test_curator_only_accepts_completed_evidenced_runs(db_connection, task) -> None:
    traces = TraceRepository(db_connection); run = traces.start_run(task.id)
    curator = WorkflowCurator(traces, SkillCandidateRepository(db_connection), ScriptedProvider([ModelResponse(_SAFE, ())]))
    with pytest.raises(SkillEligibilityError): curator.create_candidate(run.id, "scripted")
    traces.append(run.id, task.id, "tool.completed", "completed", {"tool": "query_database"})
    traces.finish_run(run.id, "completed"); traces.accept_run(run.id)
    candidate = curator.create_candidate(run.id, "scripted")
    assert candidate.status == "candidate" and candidate.enabled is False

@pytest.mark.parametrize("unsafe", ["TODO", "Use password secret", "Automatically bypass approval"])
def test_static_validator_rejects_unsafe_text(unsafe: str) -> None:
    with pytest.raises(Exception): SkillStaticValidator().validate(_SAFE + unsafe)
