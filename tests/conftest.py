from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_analysis_agent.api import create_app
from data_analysis_agent.data_sources import DataSourceRepository
from data_analysis_agent.datasets import DatasetService
from data_analysis_agent.db import migrate
from data_analysis_agent.domain import ArtifactType
from data_analysis_agent.execution import DuckDBExecutor, PythonExecutor
from data_analysis_agent.permissions import ApprovalRepository, PermissionPolicy
from data_analysis_agent.postgres import SqlSafetyGateway
from data_analysis_agent.providers import ModelResponse, ScriptedProvider
from data_analysis_agent.reporting import ReportService
from data_analysis_agent.repositories import (
    ArtifactRepository,
    ProjectRepository,
    TaskRepository,
)
from data_analysis_agent.settings import Settings
from data_analysis_agent.sql_safety import SqlAstValidator
from data_analysis_agent.tools import ToolContext, build_tool_registry
from data_analysis_agent.workspace import WorkspaceService


class RecordingProtector:
    def protect(self, value: str) -> bytes:
        return b"protected:" + value.encode()

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"protected:").decode()


class FakePostgresDriver:
    def __init__(self) -> None:
        self.catalog = {
            "orders": ("region", "amount", "customer_email"),
        }
        self.plan = {
            "Plan": {
                "Total Cost": 42.0,
                "Plan Rows": 12,
                "Node Type": "Index Scan",
            }
        }
        self.columns = ("region", "amount")
        self.rows = (("East", 10),)
        self.inspect_error: BaseException | None = None
        self.explain_error: BaseException | None = None
        self.query_error: BaseException | None = None
        self.on_explain = None
        self.on_query = None
        self.explained_sql: list[tuple[str, int]] = []
        self.queried_sql: list[tuple[str, int, int]] = []

    def inspect_schema(self, dsn: str):
        if self.inspect_error is not None:
            raise self.inspect_error
        return self.catalog

    def explain(self, dsn: str, sql: str, *, statement_timeout_ms: int):
        if self.explain_error is not None:
            raise self.explain_error
        self.explained_sql.append((sql, statement_timeout_ms))
        if self.on_explain is not None:
            self.on_explain()
        return self.plan

    def query(
        self,
        dsn: str,
        sql: str,
        *,
        statement_timeout_ms: int,
        row_limit: int,
    ):
        if self.query_error is not None:
            raise self.query_error
        self.queried_sql.append((sql, statement_timeout_ms, row_limit))
        if self.on_query is not None:
            self.on_query()
        return self.columns, self.rows


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "app-data"
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "metadata.sqlite3",
        python_timeout_seconds=1,
        python_backend="trusted_local",  # Deterministic, repository-owned test programs only.
    )


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
def source_repository(db_connection) -> DataSourceRepository:
    return DataSourceRepository(db_connection, RecordingProtector())


@pytest.fixture
def approval_repository(db_connection) -> ApprovalRepository:
    return ApprovalRepository(db_connection)


@pytest.fixture
def fake_driver() -> FakePostgresDriver:
    return FakePostgresDriver()


@pytest.fixture
def sql_gateway(
    db_connection,
    settings,
    source_repository,
    approval_repository,
    artifact_repository,
    workspace,
    task_repository,
    fake_driver,
) -> SqlSafetyGateway:
    return SqlSafetyGateway(
        connection=db_connection,
        settings=settings,
        sources=source_repository,
        validator=SqlAstValidator(),
        approvals=approval_repository,
        artifacts=artifact_repository,
        workspace=workspace,
        tasks=task_repository,
        driver=fake_driver,
    )


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


@pytest.fixture
def dataset_artifact(dataset_service: DatasetService, project, operations_csv: Path):
    return dataset_service.add_file(project.id, operations_csv)


@pytest.fixture
def duckdb_executor(
    workspace: WorkspaceService,
    artifact_repository: ArtifactRepository,
) -> DuckDBExecutor:
    return DuckDBExecutor(workspace, artifact_repository)


@pytest.fixture
def python_executor(
    settings: Settings,
    workspace: WorkspaceService,
    artifact_repository: ArtifactRepository,
    task_repository: TaskRepository,
) -> PythonExecutor:
    return PythonExecutor(settings, workspace, artifact_repository, task_repository)


@pytest.fixture
def chart_artifact(workspace, artifact_repository, task):
    path = workspace.resolve_inside(task.project_id, "charts/logistics-cost-trend.html")
    path.write_text("<html><body>chart</body></html>", encoding="utf-8")
    return artifact_repository.register(
        project_id=task.project_id,
        task_id=task.id,
        artifact_type=ArtifactType.CHART,
        path=path,
        title="Logistics cost trend",
        description="East region logistics cost trend",
        producer_execution_id=None,
        input_artifact_ids=(),
    )


@pytest.fixture
def report_service(workspace, artifact_repository, task_repository) -> ReportService:
    return ReportService(workspace, artifact_repository, task_repository)


@pytest.fixture
def tool_registry(
    dataset_service,
    duckdb_executor,
    python_executor,
    workspace,
    artifact_repository,
    report_service,
    task_repository,
    db_connection,
):
    return build_tool_registry(
        datasets=dataset_service,
        duckdb_executor=duckdb_executor,
        python_executor=python_executor,
        workspace=workspace,
        artifacts=artifact_repository,
        reports=report_service,
        tasks=task_repository,
        permission_policy=PermissionPolicy(),
        approvals=ApprovalRepository(db_connection),
    )


@pytest.fixture
def tool_context(task) -> ToolContext:
    return ToolContext(project_id=task.project_id, task_id=task.id)


@pytest.fixture
def api_client(settings: Settings) -> Iterator[TestClient]:
    provider = ScriptedProvider(
        [ModelResponse(text="Deterministic analysis complete.", tool_calls=())]
    )
    with TestClient(create_app(settings, provider)) as client:
        yield client
