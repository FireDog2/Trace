from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_analysis_agent.db import migrate
from data_analysis_agent.datasets import DatasetService
from data_analysis_agent.repositories import (
    ArtifactRepository,
    ProjectRepository,
    TaskRepository,
)
from data_analysis_agent.settings import Settings
from data_analysis_agent.workspace import WorkspaceService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "app-data"
    return Settings(data_dir=data_dir, database_path=data_dir / "metadata.sqlite3")


@pytest.fixture
def db_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def project_repository(db_connection) -> ProjectRepository:
    return ProjectRepository(db_connection)


@pytest.fixture
def workspace(settings: Settings) -> WorkspaceService:
    return WorkspaceService(settings)


@pytest.fixture
def project(project_repository: ProjectRepository, workspace: WorkspaceService):
    created = project_repository.create("Operating review")
    workspace.create_project(created.id)
    return created


@pytest.fixture
def artifact_repository(db_connection, workspace: WorkspaceService) -> ArtifactRepository:
    return ArtifactRepository(db_connection, workspace)


@pytest.fixture
def dataset_service(
    workspace: WorkspaceService,
    artifact_repository: ArtifactRepository,
) -> DatasetService:
    return DatasetService(workspace, artifact_repository)


@pytest.fixture
def operations_csv() -> Path:
    return Path(__file__).parent / "fixtures" / "operations.csv"


@pytest.fixture
def task_repository(db_connection) -> TaskRepository:
    return TaskRepository(db_connection)


@pytest.fixture
def task(task_repository: TaskRepository, project):
    return task_repository.create(project.id, "Diagnose East profit decline")
