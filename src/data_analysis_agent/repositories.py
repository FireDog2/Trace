from __future__ import annotations

import mimetypes
import sqlite3
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.db import utc_now
from data_analysis_agent.domain import (
    Artifact,
    ArtifactType,
    Project,
    ProjectStatus,
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
