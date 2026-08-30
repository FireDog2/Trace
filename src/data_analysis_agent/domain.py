from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TaskStatus(StrEnum):
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    RUNNING = "running"
    SYNTHESIZING = "synthesizing"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ArtifactType(StrEnum):
    SOURCE_DATASET = "source_dataset"
    DERIVED_DATASET = "derived_dataset"
    SQL = "sql"
    PYTHON_CODE = "python_code"
    CHART = "chart"
    TABLE_PREVIEW = "table_preview"
    EXECUTION_LOG = "execution_log"
    MARKDOWN_REPORT = "markdown_report"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class Project:
    id: str
    name: str
    status: ProjectStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Artifact:
    id: str
    project_id: str
    task_id: str | None
    artifact_type: ArtifactType
    path: Path
    title: str
    description: str
    content_type: str | None
    producer_execution_id: str | None
    input_artifact_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    project_id: str
    objective: str
    status: TaskStatus
    created_at: str
    updated_at: str
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class Message:
    id: str
    task_id: str
    role: str
    content: str
    created_at: str


@dataclass(frozen=True, slots=True)
class PlanStepDraft:
    title: str


@dataclass(frozen=True, slots=True)
class PlanStep:
    id: str
    plan_revision_id: str
    position: int
    title: str
    status: StepStatus
    created_at: str


@dataclass(frozen=True, slots=True)
class PlanRevision:
    id: str
    task_id: str
    revision: int
    reason: str
    steps: tuple[PlanStep, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class TaskEvent:
    id: str
    task_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str
