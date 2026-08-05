from __future__ import annotations

from enum import StrEnum


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
