from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_analysis_agent.db import migrate
from data_analysis_agent.repositories import ArtifactRepository, ProjectRepository
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
