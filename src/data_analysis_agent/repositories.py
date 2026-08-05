from __future__ import annotations

import json
import mimetypes
import sqlite3
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.db import utc_now
from data_analysis_agent.domain import (
    Artifact,
    ArtifactType,
    Message,
    PlanRevision,
    PlanStep,
    PlanStepDraft,
    Project,
    ProjectStatus,
    StepStatus,
    Task,
    TaskEvent,
    TaskStatus,
)
from data_analysis_agent.workspace import WorkspaceService


class RepositoryError(RuntimeError):
    """Base error for persistence operations."""


class RecordNotFoundError(RepositoryError):
    """Raised when a requested record does not exist."""


class ArtifactOwnershipError(RepositoryError):
    """Raised when artifact provenance crosses project boundaries."""


class InvalidArtifactError(RepositoryError):
    """Raised when an artifact path or relation is invalid."""


class InvalidTaskTransition(RepositoryError):
    """Raised when a task state change violates the lifecycle."""


TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.UNDERSTANDING: frozenset(
        {TaskStatus.PLANNING, TaskStatus.NEEDS_INPUT, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.PLANNING: frozenset(
        {TaskStatus.RUNNING, TaskStatus.NEEDS_INPUT, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SYNTHESIZING,
            TaskStatus.NEEDS_INPUT,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.SYNTHESIZING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.NEEDS_INPUT, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.NEEDS_INPUT: frozenset(
        {
            TaskStatus.UNDERSTANDING,
            TaskStatus.PLANNING,
            TaskStatus.RUNNING,
            TaskStatus.SYNTHESIZING,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.FAILED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.COMPLETED: frozenset(),
}


class ProjectRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection

    def create(self, name: str) -> Project:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Project name must not be empty")
        project_id = str(uuid4())
        now = utc_now()
        self._connection.execute(
            "INSERT INTO projects(id, name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, normalized_name, ProjectStatus.ACTIVE, now, now),
        )
        self._connection.commit()
        return Project(
            id=project_id,
            name=normalized_name,
            status=ProjectStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )

    def get(self, project_id: str) -> Project:
        row = self._connection.execute(
            "SELECT id, name, status, created_at, updated_at "
            "FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Project not found: {project_id}")
        return Project(
            id=row["id"],
            name=row["name"],
            status=ProjectStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self) -> tuple[Project, ...]:
        rows = self._connection.execute(
            "SELECT id, name, status, created_at, updated_at "
            "FROM projects ORDER BY created_at, rowid"
        ).fetchall()
        return tuple(
            Project(
                id=row["id"],
                name=row["name"],
                status=ProjectStatus(row["status"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )


class TaskRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection

    def create(self, project_id: str, objective: str) -> Task:
        normalized_objective = objective.strip()
        if not normalized_objective:
            raise ValueError("Task objective must not be empty")
        task_id = str(uuid4())
        now = utc_now()
        self._connection.execute(
            "INSERT INTO tasks(id, project_id, objective, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_id,
                project_id,
                normalized_objective,
                TaskStatus.UNDERSTANDING,
                now,
                now,
            ),
        )
        self._connection.commit()
        return Task(
            id=task_id,
            project_id=project_id,
            objective=normalized_objective,
            status=TaskStatus.UNDERSTANDING,
            created_at=now,
            updated_at=now,
        )

    def get(self, task_id: str) -> Task:
        row = self._connection.execute(
            "SELECT id, project_id, objective, status, created_at, updated_at "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Task not found: {task_id}")
        return _task_from_row(row)

    def transition(self, task_id: str, target: TaskStatus) -> Task:
        current = self.get(task_id)
        if target not in TASK_TRANSITIONS[current.status]:
            raise InvalidTaskTransition(f"{current.status} -> {target}")
        updated_at = utc_now()
        self._connection.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (target, updated_at, task_id),
        )
        self._connection.commit()
        return Task(
            id=current.id,
            project_id=current.project_id,
            objective=current.objective,
            status=target,
            created_at=current.created_at,
            updated_at=updated_at,
        )

    def add_message(self, task_id: str, role: str, content: str) -> Message:
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError(f"Unsupported message role: {role}")
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Message content must not be empty")
        self.get(task_id)
        message = Message(
            id=str(uuid4()),
            task_id=task_id,
            role=role,
            content=normalized_content,
            created_at=utc_now(),
        )
        self._connection.execute(
            "INSERT INTO messages(id, task_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                message.id,
                message.task_id,
                message.role,
                message.content,
                message.created_at,
            ),
        )
        self._connection.commit()
        return message

    def list_messages(self, task_id: str) -> tuple[Message, ...]:
        rows = self._connection.execute(
            "SELECT id, task_id, role, content, created_at FROM messages "
            "WHERE task_id = ? ORDER BY created_at, rowid",
            (task_id,),
        ).fetchall()
        return tuple(
            Message(
                id=row["id"],
                task_id=row["task_id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    def revise_plan(
        self,
        task_id: str,
        steps: tuple[PlanStepDraft, ...],
        reason: str,
    ) -> PlanRevision:
        if not steps:
            raise ValueError("A plan must contain at least one step")
        if any(not step.title.strip() for step in steps):
            raise ValueError("Plan step titles must not be empty")
        self.get(task_id)
        revision_id = str(uuid4())
        created_at = utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            latest = self._connection.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM plan_revisions WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            revision = int(latest) + 1
            self._connection.execute(
                "INSERT INTO plan_revisions(id, task_id, revision, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (revision_id, task_id, revision, reason.strip(), created_at),
            )
            stored_steps = tuple(
                PlanStep(
                    id=str(uuid4()),
                    plan_revision_id=revision_id,
                    position=position,
                    title=draft.title.strip(),
                    status=StepStatus.PENDING,
                    created_at=created_at,
                )
                for position, draft in enumerate(steps, start=1)
            )
            self._connection.executemany(
                "INSERT INTO plan_steps("
                "id, plan_revision_id, position, title, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (
                        step.id,
                        step.plan_revision_id,
                        step.position,
                        step.title,
                        step.status,
                        step.created_at,
                    )
                    for step in stored_steps
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return PlanRevision(
            id=revision_id,
            task_id=task_id,
            revision=revision,
            reason=reason.strip(),
            steps=stored_steps,
            created_at=created_at,
        )

    def get_plan_revision(self, revision_id: str) -> PlanRevision:
        revision_row = self._connection.execute(
            "SELECT id, task_id, revision, reason, created_at "
            "FROM plan_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        if revision_row is None:
            raise RecordNotFoundError(f"Plan revision not found: {revision_id}")
        step_rows = self._connection.execute(
            "SELECT id, plan_revision_id, position, title, status, created_at "
            "FROM plan_steps WHERE plan_revision_id = ? ORDER BY position",
            (revision_id,),
        ).fetchall()
        return PlanRevision(
            id=revision_row["id"],
            task_id=revision_row["task_id"],
            revision=revision_row["revision"],
            reason=revision_row["reason"],
            steps=tuple(_plan_step_from_row(row) for row in step_rows),
            created_at=revision_row["created_at"],
        )

    def append_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> TaskEvent:
        self.get(task_id)
        event_id = str(uuid4())
        created_at = utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            latest = self._connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM task_events WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            sequence = int(latest) + 1
            self._connection.execute(
                "INSERT INTO task_events("
                "id, task_id, sequence, event_type, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    task_id,
                    sequence,
                    event_type,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    created_at,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return TaskEvent(
            id=event_id,
            task_id=task_id,
            sequence=sequence,
            event_type=event_type,
            payload=dict(payload),
            created_at=created_at,
        )

    def events_after(self, task_id: str, sequence: int) -> tuple[TaskEvent, ...]:
        rows = self._connection.execute(
            "SELECT id, task_id, sequence, event_type, payload_json, created_at "
            "FROM task_events WHERE task_id = ? AND sequence > ? ORDER BY sequence",
            (task_id, sequence),
        ).fetchall()
        return tuple(_task_event_from_row(row) for row in rows)

    def list_for_project(self, project_id: str) -> tuple[Task, ...]:
        rows = self._connection.execute(
            "SELECT id, project_id, objective, status, created_at, updated_at "
            "FROM tasks WHERE project_id = ? ORDER BY created_at, rowid",
            (project_id,),
        ).fetchall()
        return tuple(_task_from_row(row) for row in rows)

    def start_tool_execution(
        self,
        task_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> str:
        self.get(task_id)
        execution_id = str(uuid4())
        self._connection.execute(
            "INSERT INTO tool_executions("
            "id, task_id, step_id, tool_name, arguments_json, status, started_at"
            ") VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (
                execution_id,
                task_id,
                tool_name,
                json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                "running",
                utc_now(),
            ),
        )
        self._connection.commit()
        return execution_id

    def finish_tool_execution(
        self,
        execution_id: str,
        status: str,
        error_category: str | None = None,
        error_message: str | None = None,
    ) -> None:
        cursor = self._connection.execute(
            "UPDATE tool_executions SET status = ?, error_category = ?, "
            "error_message = ?, completed_at = ? WHERE id = ?",
            (status, error_category, error_message, utc_now(), execution_id),
        )
        self._connection.commit()
        if cursor.rowcount == 0:
            raise RecordNotFoundError(f"Tool execution not found: {execution_id}")


class ArtifactRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        workspace: WorkspaceService,
    ) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._workspace = workspace

    def register(
        self,
        project_id: str,
        task_id: str | None,
        artifact_type: ArtifactType,
        path: Path,
        title: str,
        description: str,
        producer_execution_id: str | None,
        input_artifact_ids: tuple[str, ...],
    ) -> Artifact:
        resolved_path = self._workspace.resolve_inside(project_id, path)
        if not resolved_path.is_file():
            raise InvalidArtifactError(f"Artifact file does not exist: {path}")
        relative_path = self._workspace.relative_path(project_id, resolved_path)
        if task_id is not None:
            task = self._connection.execute(
                "SELECT project_id FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None or task["project_id"] != project_id:
                raise ArtifactOwnershipError("Task does not belong to the project")

        unique_inputs = tuple(dict.fromkeys(input_artifact_ids))
        if unique_inputs:
            placeholders = ",".join("?" for _ in unique_inputs)
            rows = self._connection.execute(
                f"SELECT id, project_id FROM artifacts WHERE id IN ({placeholders})",
                unique_inputs,
            ).fetchall()
            if len(rows) != len(unique_inputs) or any(
                row["project_id"] != project_id for row in rows
            ):
                raise ArtifactOwnershipError(
                    "All input artifacts must exist in the same project"
                )

        artifact_id = str(uuid4())
        created_at = utc_now()
        content_type, _ = mimetypes.guess_type(resolved_path.name)
        try:
            self._connection.execute(
                "INSERT INTO artifacts("
                "id, project_id, task_id, artifact_type, path, title, description, "
                "content_type, producer_execution_id, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    project_id,
                    task_id,
                    artifact_type,
                    relative_path,
                    title.strip(),
                    description.strip(),
                    content_type,
                    producer_execution_id,
                    created_at,
                ),
            )
            self._connection.executemany(
                "INSERT INTO artifact_inputs(artifact_id, input_artifact_id) "
                "VALUES (?, ?)",
                ((artifact_id, input_id) for input_id in unique_inputs),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise InvalidArtifactError(str(exc)) from exc

        return Artifact(
            id=artifact_id,
            project_id=project_id,
            task_id=task_id,
            artifact_type=artifact_type,
            path=resolved_path,
            title=title.strip(),
            description=description.strip(),
            content_type=content_type,
            producer_execution_id=producer_execution_id,
            input_artifact_ids=unique_inputs,
            created_at=created_at,
        )

    def get(self, artifact_id: str) -> Artifact:
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"Artifact not found: {artifact_id}")
        input_rows = self._connection.execute(
            "SELECT input_artifact_id FROM artifact_inputs "
            "WHERE artifact_id = ? ORDER BY rowid",
            (artifact_id,),
        ).fetchall()
        return Artifact(
            id=row["id"],
            project_id=row["project_id"],
            task_id=row["task_id"],
            artifact_type=ArtifactType(row["artifact_type"]),
            path=self._workspace.resolve_inside(row["project_id"], row["path"]),
            title=row["title"],
            description=row["description"],
            content_type=row["content_type"],
            producer_execution_id=row["producer_execution_id"],
            input_artifact_ids=tuple(row["input_artifact_id"] for row in input_rows),
            created_at=row["created_at"],
        )

    def list_for_project(self, project_id: str) -> tuple[Artifact, ...]:
        rows = self._connection.execute(
            "SELECT id FROM artifacts WHERE project_id = ? ORDER BY created_at, rowid",
            (project_id,),
        ).fetchall()
        return tuple(self.get(row["id"]) for row in rows)

    def list_for_task(self, task_id: str) -> tuple[Artifact, ...]:
        rows = self._connection.execute(
            "SELECT id FROM artifacts WHERE task_id = ? ORDER BY created_at, rowid",
            (task_id,),
        ).fetchall()
        return tuple(self.get(row["id"]) for row in rows)

    def set_producer(
        self,
        artifact_ids: tuple[str, ...],
        execution_id: str,
    ) -> None:
        if not artifact_ids:
            return
        placeholders = ",".join("?" for _ in artifact_ids)
        cursor = self._connection.execute(
            f"UPDATE artifacts SET producer_execution_id = ? "
            f"WHERE id IN ({placeholders}) AND producer_execution_id IS NULL",
            (execution_id, *artifact_ids),
        )
        self._connection.commit()
        if cursor.rowcount != len(set(artifact_ids)):
            raise InvalidArtifactError("Could not assign producing execution")

    def record_evidence(
        self,
        report_artifact_id: str,
        evidence_artifact_ids: tuple[str, ...],
    ) -> None:
        report = self.get(report_artifact_id)
        unique_evidence = tuple(dict.fromkeys(evidence_artifact_ids))
        for evidence_id in unique_evidence:
            evidence = self.get(evidence_id)
            if evidence.project_id != report.project_id:
                raise ArtifactOwnershipError(
                    "Report evidence must belong to the same project"
                )
        self._connection.executemany(
            "INSERT INTO evidence_links(report_artifact_id, evidence_artifact_id) "
            "VALUES (?, ?)",
            ((report_artifact_id, evidence_id) for evidence_id in unique_evidence),
        )
        self._connection.commit()

    def evidence_for(self, report_artifact_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT evidence_artifact_id FROM evidence_links "
            "WHERE report_artifact_id = ? ORDER BY rowid",
            (report_artifact_id,),
        ).fetchall()
        return tuple(row["evidence_artifact_id"] for row in rows)


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        project_id=row["project_id"],
        objective=row["objective"],
        status=TaskStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _plan_step_from_row(row: sqlite3.Row) -> PlanStep:
    return PlanStep(
        id=row["id"],
        plan_revision_id=row["plan_revision_id"],
        position=row["position"],
        title=row["title"],
        status=StepStatus(row["status"]),
        created_at=row["created_at"],
    )


def _task_event_from_row(row: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        id=row["id"],
        task_id=row["task_id"],
        sequence=row["sequence"],
        event_type=row["event_type"],
        payload=json.loads(row["payload_json"]),
        created_at=row["created_at"],
    )
