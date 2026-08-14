from __future__ import annotations

import pytest

from data_analysis_agent.domain import PlanStepDraft, StepStatus, TaskStatus
from data_analysis_agent.repositories import InvalidTaskTransition


def test_task_rejects_invalid_transition(task_repository, project) -> None:
    task = task_repository.create(project.id, "Diagnose East profit decline")

    with pytest.raises(InvalidTaskTransition):
        task_repository.transition(task.id, TaskStatus.COMPLETED)


def test_task_accepts_analysis_lifecycle(task_repository, task) -> None:
    for status in (
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.SYNTHESIZING,
        TaskStatus.COMPLETED,
    ):
        task = task_repository.transition(task.id, status)

    assert task.status is TaskStatus.COMPLETED


def test_plan_revisions_preserve_history(task_repository, task) -> None:
    first = task_repository.revise_plan(
        task.id,
        (PlanStepDraft("Inspect data"),),
        "Initial plan",
    )
    second = task_repository.revise_plan(
        task.id,
        (
            PlanStepDraft("Inspect data"),
            PlanStepDraft("Compare regions"),
        ),
        "Data supports a regional comparison",
    )

    assert second.revision == first.revision + 1
    assert len(task_repository.get_plan_revision(first.id).steps) == 1
    assert len(task_repository.get_plan_revision(second.id).steps) == 2


def test_completing_task_closes_only_the_latest_plan(task_repository, task) -> None:
    first = task_repository.revise_plan(
        task.id,
        (PlanStepDraft("Inspect data"),),
        "Initial plan",
    )
    latest = task_repository.revise_plan(
        task.id,
        (
            PlanStepDraft("Inspect data"),
            PlanStepDraft("Publish evidence-linked report"),
        ),
        "Refined plan",
    )

    for status in (
        TaskStatus.PLANNING,
        TaskStatus.RUNNING,
        TaskStatus.SYNTHESIZING,
        TaskStatus.COMPLETED,
    ):
        task_repository.transition(task.id, status)

    assert tuple(step.status for step in task_repository.get_plan_revision(first.id).steps) == (
        StepStatus.PENDING,
    )
    assert tuple(step.status for step in task_repository.get_plan_revision(latest.id).steps) == (
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
    )


def test_events_have_monotonic_task_sequence(task_repository, task) -> None:
    first = task_repository.append_event(task.id, "task.started", {})
    second = task_repository.append_event(
        task.id,
        "plan.updated",
        {"revision": 1},
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert task_repository.events_after(task.id, 1) == (second,)


def test_messages_are_restored_in_creation_order(task_repository, task) -> None:
    first = task_repository.add_message(task.id, "user", "Why did profit decline?")
    second = task_repository.add_message(task.id, "assistant", "I will inspect the data.")

    assert task_repository.list_messages(task.id) == (first, second)
