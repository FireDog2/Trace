from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Callable, Iterator, Literal

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from data_analysis_agent.agent import AgentRunner
from data_analysis_agent.context import ContextCheckpointRepository
from data_analysis_agent.data_sources import (
    AccessLevel,
    CredentialError,
    DataSource,
    DataSourceRepository,
    PolicySnapshot,
    PolicyVersionConflict,
)
from data_analysis_agent.datasets import DatasetError, DatasetService
from data_analysis_agent.db import connect_database
from data_analysis_agent.domain import Artifact, ArtifactType, Project, Task, TaskStatus
from data_analysis_agent.execution import DuckDBExecutor, PythonExecutor
from data_analysis_agent.knowledge import (
    BusinessKnowledgeEntry,
    BusinessKnowledgeRepository,
)
from data_analysis_agent.knowledge_import import (
    KnowledgeDocumentError,
    extract_knowledge_candidates,
)
from data_analysis_agent.model_config import (
    MODEL_PROVIDER_CATALOG,
    ModelConfigurationError,
    ModelConfigurationRepository,
    ProviderCredentials,
    SecretProtector,
    SecretStorageUnavailable,
    WindowsDpapiProtector,
)
from data_analysis_agent.pdf_export import render_markdown_pdf
from data_analysis_agent.permissions import ApprovalRepository, PermissionPolicy
from data_analysis_agent.postgres import SqlSafetyGateway
from data_analysis_agent.providers import (
    ModelProvider,
    provider_from_credentials,
)
from data_analysis_agent.reporting import ReportService
from data_analysis_agent.repositories import (
    ArtifactRepository,
    ProjectDeletionConflict,
    ProjectRepository,
    RecordNotFoundError,
    RepositoryError,
    TaskDeletionConflict,
    TaskRepository,
)
from data_analysis_agent.scope import GLOBAL_PROJECT_ID
from data_analysis_agent.settings import Settings
from data_analysis_agent.skills import (
    CuratorAgent,
    SkillCandidate,
    SkillCandidateRepository,
    WorkflowCurator,
)
from data_analysis_agent.sql_safety import SqlAstValidator
from data_analysis_agent.team import (
    AgentRole,
    TeamManager,
    TeammateRunner,
    TeamRepository,
)
from data_analysis_agent.tools import ToolRegistry, build_tool_registry
from data_analysis_agent.tracing import (
    AnalysisRun,
    TraceContext,
    TraceEvent,
    TraceRecorder,
    TraceRepository,
    redact_payload,
)
from data_analysis_agent.workspace import WorkspaceError, WorkspaceService


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class UpdateProjectRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectResponse(ApiModel):
    id: str
    name: str
    status: str
    created_at: str
    updated_at: str


class CreateDataSourceRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    dsn: str = Field(min_length=1, max_length=4_000)


class UpdateDataSourceRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    dsn: str | None = Field(default=None, min_length=1, max_length=4_000)


class SaveBusinessKnowledgeRequest(ApiModel):
    kind: str = Field(pattern="^(metric|term|rule)$")
    name: str = Field(min_length=1, max_length=160)
    definition: str = Field(min_length=1, max_length=4_000)
    formula: str | None = Field(default=None, max_length=4_000)
    dimensions: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=30
    )
    synonyms: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=30
    )
    source: str | None = Field(default=None, max_length=1_000)


class BusinessKnowledgeResponse(ApiModel):
    id: str
    project_id: str
    kind: str
    name: str
    definition: str
    formula: str | None
    dimensions: list[str]
    synonyms: list[str]
    source: str | None
    version: int
    created_at: str
    updated_at: str


class BusinessKnowledgeImportResponse(ApiModel):
    file_name: str
    created: list[BusinessKnowledgeResponse]
    skipped_names: list[str]
    category_counts: dict[str, int]


class DataSourceResponse(ApiModel):
    id: str
    project_id: str
    name: str
    dialect: str
    policy_version: int
    created_at: str
    updated_at: str


class PolicyRuleRequest(ApiModel):
    table: str = Field(min_length=1, max_length=255)
    column: str | None = Field(default=None, min_length=1, max_length=255)
    access: AccessLevel


class ReplacePoliciesRequest(ApiModel):
    rules: list[PolicyRuleRequest]
    expected_version: int | None = Field(default=None, ge=1)


class PolicyResponse(ApiModel):
    data_source_id: str
    version: int
    rules: list[PolicyRuleRequest]


class CreateTaskRequest(ApiModel):
    objective: str = Field(min_length=1, max_length=4_000)


class TaskResponse(ApiModel):
    id: str
    project_id: str
    objective: str
    status: str
    created_at: str
    updated_at: str
    pinned: bool


class PinTaskRequest(ApiModel):
    pinned: bool


class MessageResponse(ApiModel):
    id: str
    role: str
    content: str
    created_at: str


class PlanStepResponse(ApiModel):
    id: str
    position: int
    title: str
    status: str


class PlanResponse(ApiModel):
    id: str
    revision: int
    reason: str
    steps: list[PlanStepResponse]


class ArtifactResponse(ApiModel):
    id: str
    project_id: str
    task_id: str | None
    artifact_type: str
    title: str
    description: str
    content_type: str | None
    created_at: str
    content_url: str


class TaskDetailResponse(TaskResponse):
    messages: list[MessageResponse]
    plan: PlanResponse | None
    artifacts: list[ArtifactResponse]


class AgentRunResponse(ApiModel):
    id: str
    task_id: str
    role: str
    objective: str
    status: str
    result_summary: str | None
    error_category: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


class AnalysisRunResponse(ApiModel):
    id: str
    task_id: str
    status: str
    accepted: bool
    started_at: str
    completed_at: str | None
    error_category: str | None


class TraceEventResponse(ApiModel):
    id: str
    run_id: str
    task_id: str
    agent_id: str | None
    parent_event_id: str | None
    sequence: int
    event_type: str
    status: str
    payload: dict[str, object]
    duration_ms: int | None
    attempt: int
    error_category: str | None
    artifact_ids: list[str]


class SkillCandidateDecision(ApiModel):
    approved: bool


class SendMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=20_000)


class ApprovalDecisionRequest(ApiModel):
    approved: bool


class SaveModelConfigurationRequest(ApiModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None


class ProviderDefinitionResponse(ApiModel):
    provider: str
    label: str
    base_url: str | None
    models: list[str]
    default_model: str
    custom_url: bool


class ModelConfigurationResponse(ApiModel):
    provider: str | None
    base_url: str
    model: str
    configured: bool
    has_api_key: bool
    updated_at: str | None
    providers: list[ProviderDefinitionResponse]


ProviderFactory = Callable[[ProviderCredentials, int], ModelProvider]
TeammateProviderFactory = Callable[[], ModelProvider]


@dataclass(slots=True)
class AppServices:
    settings: Settings
    connection: sqlite3.Connection
    workspace: WorkspaceService
    projects: ProjectRepository
    tasks: TaskRepository
    artifacts: ArtifactRepository
    datasets: DatasetService
    approvals: ApprovalRepository
    data_sources: DataSourceRepository
    knowledge: BusinessKnowledgeRepository
    sql_gateway: SqlSafetyGateway
    model_configs: ModelConfigurationRepository
    tools: ToolRegistry
    provider: ModelProvider | None
    provider_factory: ProviderFactory
    secret_protector: SecretProtector
    team_repository: TeamRepository
    team_manager: TeamManager | None
    traces: TraceRepository
    trace_recorder: TraceRecorder
    skill_candidates: SkillCandidateRepository
    context_checkpoints: ContextCheckpointRepository

    def agent(self) -> AgentRunner:
        return AgentRunner(
            settings=self.settings,
            provider=self.resolve_provider(),
            tools=self.tools,
            tasks=self.tasks,
            secret_protector=self.secret_protector,
            traces=self.traces,
        )

    def resolve_provider(self) -> ModelProvider:
        if self.provider is not None:
            return self.provider
        credentials = self.model_configs.credentials()
        return self.provider_factory(
            credentials,
            self.settings.model_timeout_seconds,
        )


def build_services(
    settings: Settings,
    provider: ModelProvider | None = None,
    secret_protector: SecretProtector | None = None,
    provider_factory: ProviderFactory = provider_from_credentials,
    with_team_manager: bool = True,
    teammate_provider_factory: TeammateProviderFactory | None = None,
) -> AppServices:
    """Build services without leaking a newly opened SQLite connection on setup failure."""
    connection = connect_database(settings)
    try:
        return _build_services(
            settings,
            connection,
            provider,
            secret_protector,
            provider_factory,
            with_team_manager,
            teammate_provider_factory,
        )
    except BaseException:
        connection.close()
        raise


def _build_services(
    settings: Settings,
    connection: sqlite3.Connection,
    provider: ModelProvider | None = None,
    secret_protector: SecretProtector | None = None,
    provider_factory: ProviderFactory = provider_from_credentials,
    with_team_manager: bool = True,
    teammate_provider_factory: TeammateProviderFactory | None = None,
) -> AppServices:
    workspace = WorkspaceService(settings)
    projects = ProjectRepository(connection)
    tasks = TaskRepository(connection)
    artifacts = ArtifactRepository(connection, workspace)
    datasets = DatasetService(workspace, artifacts)
    approvals = ApprovalRepository(connection)
    protector = secret_protector or WindowsDpapiProtector()
    model_configs = ModelConfigurationRepository(
        connection,
        protector,
    )
    data_sources = DataSourceRepository(
        connection,
        protector,
        attempt_stale_after_seconds=(settings.postgres_statement_timeout_ms / 1000) * 2,
    )
    knowledge = BusinessKnowledgeRepository(connection)
    reports = ReportService(workspace, artifacts, tasks)
    traces = TraceRepository(connection)
    context_checkpoints = ContextCheckpointRepository(connection)
    sql_gateway = SqlSafetyGateway(
        connection=connection,
        settings=settings,
        sources=data_sources,
        validator=SqlAstValidator(),
        approvals=approvals,
        artifacts=artifacts,
        workspace=workspace,
        tasks=tasks,
    )
    tools = build_tool_registry(
        datasets=datasets,
        duckdb_executor=DuckDBExecutor(
            workspace,
            artifacts,
            timeout_seconds=settings.duckdb_timeout_seconds,
        ),
        python_executor=PythonExecutor(
            settings,
            workspace,
            artifacts,
            tasks,
        ),
        workspace=workspace,
        artifacts=artifacts,
        reports=reports,
        tasks=tasks,
        permission_policy=PermissionPolicy(),
        approvals=approvals,
        sql_gateway=sql_gateway,
        trace_recorder=TraceRecorder(traces),
        include_coordination_tools=True,
        include_database_query=not with_team_manager,
        include_artifact_writes=not with_team_manager,
        knowledge=knowledge,
        include_knowledge_search=not with_team_manager,
    )
    team_repository = TeamRepository(connection, tasks.append_event)
    skill_candidates = SkillCandidateRepository(connection)
    services = AppServices(
        settings=settings,
        connection=connection,
        workspace=workspace,
        projects=projects,
        tasks=tasks,
        artifacts=artifacts,
        datasets=datasets,
        approvals=approvals,
        data_sources=data_sources,
        knowledge=knowledge,
        sql_gateway=sql_gateway,
        model_configs=model_configs,
        tools=tools,
        provider=provider,
        provider_factory=provider_factory,
        secret_protector=protector,
        team_repository=team_repository,
        team_manager=None,
        traces=traces,
        trace_recorder=TraceRecorder(traces),
        skill_candidates=skill_candidates,
        context_checkpoints=context_checkpoints,
    )
    if not with_team_manager:
        return services

    def runner_factory(role: AgentRole, cancel_event):
        def run_and_close(run_id: str):
            worker = None
            try:
                worker = build_services(
                    settings,
                    (
                        provider
                        if role is AgentRole.CURATOR and provider is not None
                        else teammate_provider_factory()
                        if teammate_provider_factory is not None
                        else None
                    ),
                    protector,
                    provider_factory,
                    with_team_manager=False,
                )
                if role is AgentRole.CURATOR:
                    public_model = worker.model_configs.get_public()
                    return CuratorAgent(
                        traces=worker.traces,
                        candidates=worker.skill_candidates,
                        provider=worker.resolve_provider(),
                        teams=worker.team_repository,
                        model_name=public_model.model or "runtime-provider",
                    ).run(run_id)
                return TeammateRunner(
                    settings=settings,
                    provider=worker.resolve_provider(),
                    tools=worker.tools,
                    repository=worker.team_repository,
                    cancel_event=cancel_event,
                    secret_protector=protector,
                    traces=getattr(worker, "traces", None),
                ).run(run_id)
            finally:
                if worker is not None:
                    worker.connection.close()

        class ClosingRunner:
            run = staticmethod(run_and_close)

        return ClosingRunner()

    def worker_failure_handler(run_id: str):
        worker_connection = connect_database(settings)
        try:
            worker_repository = TeamRepository(
                worker_connection, TaskRepository(worker_connection).append_event
            )
            current = worker_repository.get_run(run_id)
            return (
                worker_repository.fail_run(run_id, "provider_error")
                if current.status.value in {"created", "working", "waiting"}
                else current
            )
        finally:
            worker_connection.close()

    services.team_manager = TeamManager(
        team_repository, settings, runner_factory, worker_failure_handler
    )
    tools.configure_team(services.team_manager, team_repository)
    return services


def create_app(
    settings: Settings,
    provider: ModelProvider | None = None,
    secret_protector: SecretProtector | None = None,
    provider_factory: ProviderFactory = provider_from_credentials,
    teammate_provider_factory: TeammateProviderFactory | None = None,
) -> FastAPI:
    services = build_services(
        settings,
        provider,
        secret_protector,
        provider_factory,
        teammate_provider_factory=teammate_provider_factory,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        services.team_repository.mark_interrupted_runs()
        try:
            yield
        finally:
            assert services.team_manager is not None
            services.team_manager.shutdown()
            services.connection.close()

    app = FastAPI(
        title="Data Analysis Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.services = services

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": "Invalid request",
                "error_count": len(exc.errors()),
            },
        )

    @app.exception_handler(RecordNotFoundError)
    async def not_found_handler(_, exc: RecordNotFoundError):
        del exc
        return _json_error(status.HTTP_404_NOT_FOUND, "Resource not found")

    @app.exception_handler(DatasetError)
    @app.exception_handler(WorkspaceError)
    @app.exception_handler(RepositoryError)
    async def domain_error_handler(_, exc: Exception):
        del exc
        return _json_error(status.HTTP_400_BAD_REQUEST, "Invalid request")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/model-config", response_model=ModelConfigurationResponse)
    def get_model_configuration() -> ModelConfigurationResponse:
        return _model_configuration_response(services.model_configs)

    @app.put("/api/model-config", response_model=ModelConfigurationResponse)
    def save_model_configuration(
        request: SaveModelConfigurationRequest,
    ) -> ModelConfigurationResponse:
        try:
            services.model_configs.save(
                request.provider,
                request.model,
                request.base_url,
                request.api_key,
            )
        except SecretStorageUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secure storage is unavailable",
            ) from exc
        except ModelConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid model configuration",
            ) from exc
        return _model_configuration_response(services.model_configs)

    @app.get("/api/projects", response_model=list[ProjectResponse])
    def list_projects() -> list[ProjectResponse]:
        return [_project_response(project) for project in services.projects.list()]

    @app.post(
        "/api/projects",
        response_model=ProjectResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(request: CreateProjectRequest) -> ProjectResponse:
        project = services.projects.create(request.name)
        WorkspaceService(settings).create_project(project.id)
        return _project_response(project)

    @app.put("/api/projects/{project_id}", response_model=ProjectResponse)
    def update_project(
        project_id: str,
        request: UpdateProjectRequest,
    ) -> ProjectResponse:
        try:
            return _project_response(services.projects.update_name(project_id, request.name))
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Project name must not be empty",
            ) from exc

    @app.delete("/api/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_project(project_id: str) -> Response:
        try:
            services.projects.delete(project_id)
            services.workspace.delete_project(project_id)
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            ) from exc
        except ProjectDeletionConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Wait for active tasks to finish before deleting the project",
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/api/projects/{project_id}/knowledge",
        response_model=list[BusinessKnowledgeResponse],
    )
    def list_business_knowledge(
        project_id: str,
    ) -> list[BusinessKnowledgeResponse]:
        services.projects.get(project_id)
        return [
            _business_knowledge_response(entry)
            for entry in services.knowledge.list_for_project(project_id)
        ]

    @app.post(
        "/api/projects/{project_id}/knowledge",
        response_model=BusinessKnowledgeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_business_knowledge(
        project_id: str,
        request: SaveBusinessKnowledgeRequest,
    ) -> BusinessKnowledgeResponse:
        services.projects.get(project_id)
        try:
            entry = services.knowledge.create(
                project_id,
                kind=request.kind,
                name=request.name,
                definition=request.definition,
                formula=request.formula,
                dimensions=tuple(request.dimensions),
                synonyms=tuple(request.synonyms),
                source=request.source,
            )
        except (RepositoryError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Business knowledge entry could not be created",
            ) from exc
        return _business_knowledge_response(entry)

    @app.post(
        "/api/projects/{project_id}/knowledge/import",
        response_model=BusinessKnowledgeImportResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def import_business_knowledge(
        project_id: str,
        file: Annotated[UploadFile, File()],
    ) -> BusinessKnowledgeImportResponse:
        services.projects.get(project_id)
        file_name = Path(file.filename or "knowledge-document").name[:255]
        try:
            content = file.file.read(10 * 1024 * 1024 + 1)
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Knowledge document must not exceed 10 MB",
                )
            candidates = extract_knowledge_candidates(file_name, content)
        except KnowledgeDocumentError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        finally:
            file.file.close()

        known_names = {
            entry.name.casefold() for entry in services.knowledge.list_for_project(project_id)
        }
        created: list[BusinessKnowledgeEntry] = []
        skipped_names: list[str] = []
        for candidate in candidates:
            if candidate.name.casefold() in known_names:
                skipped_names.append(candidate.name)
                continue
            try:
                entry = services.knowledge.create(
                    project_id,
                    kind=candidate.kind,
                    name=candidate.name,
                    definition=candidate.definition,
                    formula=candidate.formula,
                    dimensions=candidate.dimensions,
                    synonyms=candidate.synonyms,
                    source=f"Document import · {file_name}",
                )
            except RepositoryError:
                skipped_names.append(candidate.name)
                continue
            known_names.add(entry.name.casefold())
            created.append(entry)
        category_counts = {
            kind: sum(entry.kind == kind for entry in created)
            for kind in ("metric", "term", "rule")
        }
        return BusinessKnowledgeImportResponse(
            file_name=file_name,
            created=[_business_knowledge_response(entry) for entry in created],
            skipped_names=skipped_names,
            category_counts=category_counts,
        )

    @app.get(
        "/api/knowledge",
        response_model=list[BusinessKnowledgeResponse],
    )
    def list_global_business_knowledge() -> list[BusinessKnowledgeResponse]:
        return [_business_knowledge_response(entry) for entry in services.knowledge.list_all()]

    @app.post(
        "/api/knowledge",
        response_model=BusinessKnowledgeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_global_business_knowledge(
        request: SaveBusinessKnowledgeRequest,
    ) -> BusinessKnowledgeResponse:
        return create_business_knowledge(GLOBAL_PROJECT_ID, request)

    @app.post(
        "/api/knowledge/import",
        response_model=BusinessKnowledgeImportResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def import_global_business_knowledge(
        file: Annotated[UploadFile, File()],
    ) -> BusinessKnowledgeImportResponse:
        return import_business_knowledge(GLOBAL_PROJECT_ID, file)

    @app.put(
        "/api/knowledge/{entry_id}",
        response_model=BusinessKnowledgeResponse,
    )
    def update_business_knowledge(
        entry_id: str,
        request: SaveBusinessKnowledgeRequest,
    ) -> BusinessKnowledgeResponse:
        try:
            entry = services.knowledge.update(
                entry_id,
                kind=request.kind,
                name=request.name,
                definition=request.definition,
                formula=request.formula,
                dimensions=tuple(request.dimensions),
                synonyms=tuple(request.synonyms),
                source=request.source,
            )
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business knowledge entry not found",
            ) from exc
        except (RepositoryError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Business knowledge entry could not be updated",
            ) from exc
        return _business_knowledge_response(entry)

    @app.delete(
        "/api/knowledge/{entry_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_business_knowledge(entry_id: str) -> None:
        try:
            services.knowledge.delete(entry_id)
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Business knowledge entry not found",
            ) from exc

    @app.get(
        "/api/projects/{project_id}/data-sources",
        response_model=list[DataSourceResponse],
    )
    def list_data_sources(project_id: str) -> list[DataSourceResponse]:
        services.projects.get(project_id)
        return [
            _data_source_response(source)
            for source in services.data_sources.list_for_project(project_id)
        ]

    @app.post(
        "/api/projects/{project_id}/data-sources",
        response_model=DataSourceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_data_source(
        project_id: str,
        request: CreateDataSourceRequest,
    ) -> DataSourceResponse:
        services.projects.get(project_id)
        try:
            source = services.data_sources.create(project_id, request.name, request.dsn)
        except CredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secure storage is unavailable",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Data source must use a valid PostgreSQL DSN",
            ) from exc
        except RepositoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Data source name already exists",
            ) from exc
        return _data_source_response(source)

    @app.get(
        "/api/data-sources",
        response_model=list[DataSourceResponse],
    )
    def list_global_data_sources() -> list[DataSourceResponse]:
        return [_data_source_response(source) for source in services.data_sources.list_all()]

    @app.post(
        "/api/data-sources",
        response_model=DataSourceResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_global_data_source(
        request: CreateDataSourceRequest,
    ) -> DataSourceResponse:
        return create_data_source(GLOBAL_PROJECT_ID, request)

    @app.put(
        "/api/data-sources/{source_id}",
        response_model=DataSourceResponse,
    )
    def update_data_source(
        source_id: str,
        request: UpdateDataSourceRequest,
    ) -> DataSourceResponse:
        try:
            source = services.data_sources.update(
                source_id,
                name=request.name,
                dsn=request.dsn,
            )
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Data source not found",
            ) from exc
        except CredentialError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Secure storage is unavailable",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Data source must use a valid PostgreSQL DSN",
            ) from exc
        except RepositoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Data source name already exists",
            ) from exc
        return _data_source_response(source)

    @app.delete(
        "/api/data-sources/{source_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_data_source(source_id: str) -> None:
        try:
            services.data_sources.delete(source_id)
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Data source not found",
            ) from exc
        except RepositoryError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Data source is currently in use",
            ) from exc

    @app.put(
        "/api/data-sources/{source_id}/policies",
        response_model=PolicyResponse,
    )
    def replace_data_source_policies(
        source_id: str,
        request: ReplacePoliciesRequest,
    ) -> PolicyResponse:
        rules = {(rule.table, rule.column): rule.access for rule in request.rules}
        if len(rules) != len(request.rules):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Duplicate data-access policy",
            )
        try:
            snapshot = services.data_sources.replace_policies(
                source_id,
                rules,
                expected_version=request.expected_version,
            )
        except PolicyVersionConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Data-access policy has changed; reload before saving",
            ) from exc
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Data source not found",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid data-access policy",
            ) from exc
        return _policy_response(snapshot)

    @app.get(
        "/api/data-sources/{source_id}/policies",
        response_model=PolicyResponse,
    )
    def get_data_source_policies(source_id: str) -> PolicyResponse:
        try:
            snapshot = services.data_sources.policy_snapshot(source_id)
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Data source not found",
            ) from exc
        return _policy_response(snapshot)

    @app.get("/api/data-sources/{source_id}/schema")
    def inspect_data_source_schema(source_id: str) -> dict[str, object]:
        try:
            services.data_sources.get_public(source_id)
            catalog = services.sql_gateway.inspect_schema(source_id)
        except RecordNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Data source not found",
            ) from exc
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "category": "connection_error",
                    "message": "The PostgreSQL schema could not be inspected",
                },
            )
        return {"data_source_id": source_id, "tables": catalog}

    @app.get(
        "/api/projects/{project_id}/artifacts",
        response_model=list[ArtifactResponse],
    )
    def list_artifacts(project_id: str) -> list[ArtifactResponse]:
        services.projects.get(project_id)
        return [
            _artifact_response(artifact)
            for artifact in services.artifacts.list_for_project(project_id)
        ]

    @app.post(
        "/api/projects/{project_id}/datasets",
        response_model=ArtifactResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def upload_dataset(
        project_id: str,
        file: Annotated[UploadFile, File()],
    ) -> ArtifactResponse:
        services.projects.get(project_id)
        suffix = Path(file.filename or "dataset").suffix
        incoming_root = settings.data_dir / "incoming"
        incoming_root.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                dir=incoming_root,
                delete=False,
            ) as temporary:
                shutil.copyfileobj(file.file, temporary)
                temporary_path = Path(temporary.name)
            artifact = services.datasets.add_file(
                project_id,
                temporary_path,
                display_name=file.filename,
            )
            return _artifact_response(artifact)
        finally:
            file.file.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @app.get("/api/artifacts/{artifact_id}/dataset-inspection")
    def inspect_dataset(artifact_id: str, sample_rows: int = Query(20, ge=0, le=100)):
        return asdict(services.datasets.inspect(artifact_id, sample_rows))

    @app.get("/api/artifacts/{artifact_id}/content")
    def artifact_content(
        artifact_id: str,
        preview: bool = Query(False),
    ) -> FileResponse:
        artifact = services.artifacts.get(artifact_id)
        preview_headers = (
            {
                "Content-Security-Policy": (
                    "sandbox allow-scripts; default-src 'self' data: blob: https:; "
                    "style-src 'self' 'unsafe-inline' https:; "
                    "script-src 'self' 'unsafe-inline' https:; "
                    "img-src 'self' data: blob: https:; "
                    "font-src 'self' data: https:; connect-src 'self' https:"
                ),
                "X-Content-Type-Options": "nosniff",
            }
            if preview
            else None
        )
        return FileResponse(
            artifact.path,
            headers=preview_headers,
            media_type=artifact.content_type,
            filename=artifact.path.name,
            content_disposition_type="inline" if preview else "attachment",
        )

    @app.get("/api/artifacts/{artifact_id}/export")
    def export_report_artifact(
        artifact_id: str,
        format: Literal["markdown", "pdf"] = Query("markdown"),
    ):
        artifact = services.artifacts.get(artifact_id)
        if artifact.artifact_type is not ArtifactType.MARKDOWN_REPORT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only Markdown reports can be exported as documents",
            )
        if format == "markdown":
            return FileResponse(
                artifact.path,
                media_type="text/markdown; charset=utf-8",
                filename=artifact.path.name,
            )
        markdown = artifact.path.read_text(encoding="utf-8")
        pdf = render_markdown_pdf(
            markdown,
            title=artifact.title.removeprefix("Report: "),
            subtitle=artifact.description,
        )
        filename = f"{artifact.path.stem}.pdf"
        return StreamingResponse(
            iter((pdf,)),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get(
        "/api/projects/{project_id}/tasks",
        response_model=list[TaskResponse],
    )
    def list_tasks(project_id: str) -> list[TaskResponse]:
        services.projects.get(project_id)
        return [_task_response(task) for task in services.tasks.list_for_project(project_id)]

    @app.post(
        "/api/projects/{project_id}/tasks",
        response_model=TaskResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_task(
        project_id: str,
        request: CreateTaskRequest,
        background_tasks: BackgroundTasks,
    ) -> TaskResponse:
        services.projects.get(project_id)
        resolved_provider = _configured_provider_or_409(services)
        task = services.tasks.create(project_id, request.objective)
        background_tasks.add_task(
            _run_task,
            settings,
            resolved_provider,
            task.id,
            services.secret_protector,
            services.provider_factory,
            teammate_provider_factory,
        )
        return _task_response(task)

    @app.get("/api/tasks/{task_id}", response_model=TaskDetailResponse)
    def get_task(task_id: str) -> TaskDetailResponse:
        task = services.tasks.get(task_id)
        plan = services.tasks.latest_plan(task_id)
        return TaskDetailResponse(
            **_task_response(task).model_dump(),
            messages=[
                MessageResponse(
                    id=message.id,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in services.tasks.list_messages(task_id)
                if message.role != "assistant_tool_call"
            ],
            plan=(
                PlanResponse(
                    id=plan.id,
                    revision=plan.revision,
                    reason=plan.reason,
                    steps=[
                        PlanStepResponse(
                            id=step.id,
                            position=step.position,
                            title=step.title,
                            status=step.status.value,
                        )
                        for step in plan.steps
                    ],
                )
                if plan is not None
                else None
            ),
            artifacts=[
                _artifact_response(artifact)
                for artifact in services.artifacts.list_for_task(task_id)
            ],
        )

    @app.put("/api/tasks/{task_id}/pin", response_model=TaskResponse)
    def set_task_pinned(task_id: str, request: PinTaskRequest) -> TaskResponse:
        return _task_response(services.tasks.set_pinned(task_id, request.pinned))

    @app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_task(task_id: str) -> Response:
        try:
            services.tasks.soft_delete(task_id)
        except TaskDeletionConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Wait for the task to finish before deleting it",
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/tasks/{task_id}/agents", response_model=list[AgentRunResponse])
    def list_agents(task_id: str) -> list[AgentRunResponse]:
        services.tasks.get(task_id)
        return [_agent_run_response(run) for run in services.team_repository.list_runs(task_id)]

    @app.get("/api/tasks/{task_id}/runs", response_model=list[AnalysisRunResponse])
    def list_analysis_runs(task_id: str) -> list[AnalysisRunResponse]:
        services.tasks.get(task_id)
        return [_analysis_run_response(run) for run in services.traces.list_runs(task_id)]

    @app.get("/api/runs/{run_id}/trace", response_model=list[TraceEventResponse])
    def get_trace(run_id: str) -> list[TraceEventResponse]:
        return [_trace_event_response(event) for event in services.traces.events_for_run(run_id)]

    @app.get("/api/runs/{run_id}/replay")
    def get_replay_bundle(run_id: str) -> dict[str, object]:
        """Return a safe, self-contained replay manifest without re-executing the run."""
        run = services.traces.get_run(run_id)
        task = services.tasks.get(run.task_id)
        events = services.traces.events_for_run(run_id)
        agents = tuple(
            agent
            for agent in services.team_repository.list_runs(task.id)
            if agent.trace_run_id == run_id
        )
        approvals = services.approvals.list_for_task(task.id)
        artifacts = services.artifacts.list_for_task(task.id)
        checkpoints = [
            *services.context_checkpoints.list_for_owner("task", task.id),
            *(
                checkpoint
                for agent in agents
                for checkpoint in services.context_checkpoints.list_for_owner("agent", agent.id)
            ),
        ]
        try:
            candidate: object = _skill_candidate_response(
                services.skill_candidates.get_for_run(run_id)
            )
        except ValueError:
            candidate = None
        sequences = [event.sequence for event in events]
        return {
            "schema_version": 2,
            "run": _analysis_run_response(run),
            "task": _task_response(task),
            "trace": [_trace_event_response(event) for event in events],
            "agents": [_agent_run_response(agent) for agent in agents],
            "approvals": [
                {
                    "id": approval.id,
                    "tool_name": approval.action.tool_name,
                    "arguments": redact_payload(approval.action.arguments),
                    "reason": approval.reason,
                    "status": approval.status,
                    "created_at": approval.created_at,
                    "decided_at": approval.decided_at,
                }
                for approval in approvals
            ],
            "artifacts": [_artifact_response(artifact) for artifact in artifacts],
            "context_checkpoints": [
                {
                    "id": checkpoint.id,
                    "owner_type": checkpoint.owner_type,
                    "owner_id": checkpoint.owner_id,
                    "through_message_id": checkpoint.through_message_id,
                    "source_message_count": checkpoint.source_message_count,
                    "before_tokens": checkpoint.before_tokens,
                    "after_tokens": checkpoint.after_tokens,
                    "reason": checkpoint.reason,
                    "summary_hash": sha256(checkpoint.summary.encode("utf-8")).hexdigest(),
                    "created_at": checkpoint.created_at,
                }
                for checkpoint in checkpoints
            ],
            "skill_candidate": candidate,
            "integrity": {
                "event_count": len(events),
                "first_sequence": sequences[0] if sequences else None,
                "last_sequence": sequences[-1] if sequences else None,
                "sequence_contiguous": sequences == list(range(1, len(sequences) + 1)),
                "agent_count": len(agents),
                "approval_count": len(approvals),
                "artifact_count": len(artifacts),
                "context_checkpoint_count": len(checkpoints),
            },
        }

    @app.post("/api/runs/{run_id}/accept", response_model=AnalysisRunResponse)
    def accept_trace_run(run_id: str) -> AnalysisRunResponse:
        try:
            accepted = services.traces.accept_run(run_id)
            if services.team_manager is not None:
                services.team_manager.ensure_curator(accepted.task_id, accepted.id)
            return _analysis_run_response(accepted)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Run cannot be accepted") from exc

    @app.post("/api/runs/{run_id}/skill-candidates", status_code=status.HTTP_201_CREATED)
    def create_skill_candidate(run_id: str):
        try:
            model_name = services.model_configs.get_public().model or "runtime-provider"
            candidate = WorkflowCurator(
                services.traces, services.skill_candidates, services.resolve_provider()
            ).create_candidate(run_id, model_name)
            return _skill_candidate_response(candidate)
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail="Run cannot produce a skill candidate"
            ) from exc

    @app.get("/api/runs/{run_id}/skill-candidate")
    def get_skill_candidate_for_run(run_id: str):
        services.traces.get_run(run_id)
        try:
            return _skill_candidate_response(services.skill_candidates.get_for_run(run_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail="Skill candidate is not ready",
            ) from exc

    @app.get("/api/skill-candidates/{candidate_id}")
    def get_skill_candidate(candidate_id: str):
        return _skill_candidate_response(services.skill_candidates.get(candidate_id))

    @app.post("/api/skill-candidates/{candidate_id}/decision")
    def decide_skill_candidate(candidate_id: str, request: SkillCandidateDecision):
        try:
            return _skill_candidate_response(
                services.skill_candidates.decide(candidate_id, request.approved)
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409, detail="Skill candidate cannot be decided"
            ) from exc

    @app.post("/api/tasks/{task_id}/messages", response_model=TaskResponse)
    def send_message(
        task_id: str,
        request: SendMessageRequest,
        background_tasks: BackgroundTasks,
    ) -> TaskResponse:
        task = services.tasks.get(task_id)
        if task.status in {
            TaskStatus.NEEDS_INPUT,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        }:
            resolved_provider = _configured_provider_or_409(services)
            background_tasks.add_task(
                _continue_task,
                settings,
                resolved_provider,
                task_id,
                request.content,
                task.status,
                services.secret_protector,
                services.provider_factory,
                teammate_provider_factory,
            )
        else:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Task is already {task.status.value}; wait for it to finish "
                    "before sending another message"
                ),
            )
        return _task_response(task)

    @app.post("/api/approvals/{approval_id}/decision")
    def decide_approval(
        approval_id: str,
        request: ApprovalDecisionRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str | None]:
        resolved_provider = _configured_provider_or_409(services) if request.approved else None
        approval = services.approvals.decide(approval_id, request.approved)
        services.tasks.append_event(
            approval.task_id,
            "approval.decided",
            {"approval_id": approval.id, "status": approval.status},
        )
        runs = services.traces.list_runs(approval.task_id)
        if runs:
            try:
                services.trace_recorder.event(
                    TraceContext(runs[-1].id, approval.task_id),
                    "approval.decided",
                    "completed",
                    {"approval_id": approval.id, "status": approval.status},
                )
            except Exception:
                services.tasks.append_event(
                    approval.task_id,
                    "trace.recording_failed",
                    {"event_type": "approval.decided"},
                )
        if request.approved:
            assert resolved_provider is not None
            background_tasks.add_task(
                _resume_approved_task,
                settings,
                resolved_provider,
                approval.task_id,
                services.secret_protector,
                services.provider_factory,
                teammate_provider_factory,
            )
        return {
            "id": approval.id,
            "status": approval.status,
            "decided_at": approval.decided_at,
        }

    @app.get("/api/tasks/{task_id}/events")
    def task_events(
        task_id: str,
        after: int = Query(0, ge=0),
        follow: bool = False,
    ) -> StreamingResponse:
        services.tasks.get(task_id)
        return StreamingResponse(
            _event_stream(services.tasks, task_id, after, follow),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _event_stream(
    tasks: TaskRepository,
    task_id: str,
    after: int,
    follow: bool,
) -> Iterator[str]:
    sequence = after
    last_heartbeat = time.monotonic()
    while True:
        events = tasks.events_after(task_id, sequence)
        for event in events:
            sequence = event.sequence
            yield (
                f"id: {event.sequence}\n"
                f"event: {event.event_type}\n"
                f"data: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
            )
        if not follow:
            return
        if time.monotonic() - last_heartbeat >= 15:
            yield ": heartbeat\n\n"
            last_heartbeat = time.monotonic()
        time.sleep(0.25)


def _configured_provider_or_409(services: AppServices) -> ModelProvider:
    try:
        return services.resolve_provider()
    except ModelConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "category": "model_not_configured",
                "message": "Configure a model before starting analysis",
            },
        ) from exc


def _run_task(
    settings: Settings,
    provider: ModelProvider,
    task_id: str,
    secret_protector: SecretProtector | None = None,
    provider_factory: ProviderFactory = provider_from_credentials,
    teammate_provider_factory: TeammateProviderFactory | None = None,
) -> None:
    services = build_services(
        settings,
        provider,
        secret_protector,
        provider_factory,
        teammate_provider_factory=teammate_provider_factory,
    )
    try:
        services.agent().run(task_id)
    finally:
        try:
            if services.team_manager is not None:
                services.team_manager.shutdown()
        finally:
            services.connection.close()


def _continue_task(
    settings: Settings,
    provider: ModelProvider,
    task_id: str,
    content: str,
    previous_status: TaskStatus,
    secret_protector: SecretProtector | None = None,
    provider_factory: ProviderFactory = provider_from_credentials,
    teammate_provider_factory: TeammateProviderFactory | None = None,
) -> None:
    services = build_services(
        settings,
        provider,
        secret_protector,
        provider_factory,
        teammate_provider_factory=teammate_provider_factory,
    )
    try:
        if previous_status is TaskStatus.COMPLETED:
            services.agent().follow_up(task_id, content)
        elif previous_status is TaskStatus.FAILED:
            services.agent().retry(task_id, content)
        else:
            services.agent().resume(task_id, content)
    finally:
        try:
            if services.team_manager is not None:
                services.team_manager.shutdown()
        finally:
            services.connection.close()


def _resume_approved_task(
    settings: Settings,
    provider: ModelProvider,
    task_id: str,
    secret_protector: SecretProtector | None = None,
    provider_factory: ProviderFactory = provider_from_credentials,
    teammate_provider_factory: TeammateProviderFactory | None = None,
) -> None:
    services = build_services(
        settings,
        provider,
        secret_protector,
        provider_factory,
        teammate_provider_factory=teammate_provider_factory,
    )
    try:
        manager = services.team_manager

        def resume_waiting_teammates() -> None:
            assert manager is not None
            resumed = manager.resume_waiting(task_id)
            run_ids = tuple(run.id for run in resumed)
            if run_ids:
                manager.await_runs(
                    run_ids,
                    min(settings.teammate_timeout_seconds, 120),
                )

        services.agent().resume_approval(
            task_id,
            resume_waiting_teammates if manager is not None else None,
        )

    finally:
        try:
            if services.team_manager is not None:
                services.team_manager.shutdown()
        finally:
            services.connection.close()


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        status=project.status.value,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _data_source_response(source: DataSource) -> DataSourceResponse:
    return DataSourceResponse(
        id=source.id,
        project_id=source.project_id,
        name=source.name,
        dialect=source.dialect,
        policy_version=source.policy_version,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _business_knowledge_response(
    entry: BusinessKnowledgeEntry,
) -> BusinessKnowledgeResponse:
    return BusinessKnowledgeResponse(
        id=entry.id,
        project_id=entry.project_id,
        kind=entry.kind,
        name=entry.name,
        definition=entry.definition,
        formula=entry.formula,
        dimensions=list(entry.dimensions),
        synonyms=list(entry.synonyms),
        source=entry.source,
        version=entry.version,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _policy_response(snapshot: PolicySnapshot) -> PolicyResponse:
    return PolicyResponse(
        data_source_id=snapshot.data_source_id,
        version=snapshot.version,
        rules=[
            PolicyRuleRequest(table=table, column=column, access=access)
            for (table, column), access in snapshot.rules.items()
        ],
    )


def _task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        objective=task.objective,
        status=task.status.value,
        created_at=task.created_at,
        updated_at=task.updated_at,
        pinned=task.pinned,
    )


def _agent_run_response(run) -> AgentRunResponse:
    return AgentRunResponse(
        id=run.id,
        task_id=run.task_id,
        role=run.role.value,
        objective=run.objective,
        status=run.status.value,
        result_summary=_result_summary(run.result_json),
        error_category=run.error_category,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _analysis_run_response(run: AnalysisRun) -> AnalysisRunResponse:
    return AnalysisRunResponse(
        id=run.id,
        task_id=run.task_id,
        status=run.status,
        accepted=run.accepted,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_category=run.error_category,
    )


def _trace_event_response(event: TraceEvent) -> TraceEventResponse:
    return TraceEventResponse(
        id=event.id,
        run_id=event.run_id,
        task_id=event.task_id,
        agent_id=event.agent_id,
        parent_event_id=event.parent_event_id,
        sequence=event.sequence,
        event_type=event.event_type,
        status=event.status,
        payload=event.payload,
        duration_ms=event.duration_ms,
        attempt=event.attempt,
        error_category=event.error_category,
        artifact_ids=list(event.artifact_ids),
    )


def _skill_candidate_response(candidate: SkillCandidate) -> dict[str, object]:
    return {
        "id": candidate.id,
        "source_run_id": candidate.source_run_id,
        "name": candidate.name,
        "version": candidate.version,
        "markdown": candidate.markdown,
        "status": candidate.status,
        "enabled": candidate.enabled,
        "generated_by_model": candidate.generated_by_model,
        "reviewed_at": candidate.reviewed_at,
        "created_at": candidate.created_at,
    }


def _result_summary(result_json: str | None) -> str | None:
    if result_json is None:
        return None
    try:
        result = json.loads(result_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    summary = result.get("summary")
    return summary[:500] if isinstance(summary, str) else None


def _artifact_response(
    artifact: Artifact,
    *,
    title: str | None = None,
) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        project_id=artifact.project_id,
        task_id=artifact.task_id,
        artifact_type=artifact.artifact_type.value,
        title=title or artifact.title,
        description=artifact.description,
        content_type=artifact.content_type,
        created_at=artifact.created_at,
        content_url=f"/api/artifacts/{artifact.id}/content",
    )


def _model_configuration_response(
    repository: ModelConfigurationRepository,
) -> ModelConfigurationResponse:
    current = repository.get_public()
    return ModelConfigurationResponse(
        provider=current.provider.value if current.provider is not None else None,
        base_url=current.base_url,
        model=current.model,
        configured=current.configured,
        has_api_key=current.has_api_key,
        updated_at=current.updated_at,
        providers=[
            ProviderDefinitionResponse(
                provider=definition.kind.value,
                label=definition.label,
                base_url=definition.base_url,
                models=list(definition.models),
                default_model=definition.default_model,
                custom_url=definition.custom_url,
            )
            for definition in MODEL_PROVIDER_CATALOG
        ],
    )


def _json_error(status_code: int, detail: str):
    return JSONResponse(status_code=status_code, content={"detail": detail})
