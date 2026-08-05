from __future__ import annotations

import json
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Iterator

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from data_analysis_agent.agent import AgentRunner
from data_analysis_agent.datasets import DatasetError, DatasetService
from data_analysis_agent.db import connect_database
from data_analysis_agent.domain import Artifact, Project, Task, TaskStatus
from data_analysis_agent.execution import DuckDBExecutor, PythonExecutor
from data_analysis_agent.permissions import ApprovalRepository, PermissionPolicy
from data_analysis_agent.providers import ModelProvider
from data_analysis_agent.reporting import ReportService
from data_analysis_agent.repositories import (
    ArtifactRepository,
    ProjectRepository,
    RecordNotFoundError,
    RepositoryError,
    TaskRepository,
)
from data_analysis_agent.settings import Settings
from data_analysis_agent.tools import ToolRegistry, build_tool_registry
from data_analysis_agent.workspace import WorkspaceError, WorkspaceService


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateProjectRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectResponse(ApiModel):
    id: str
    name: str
    status: str
    created_at: str
    updated_at: str


class CreateTaskRequest(ApiModel):
    objective: str = Field(min_length=1, max_length=4_000)


class TaskResponse(ApiModel):
    id: str
    project_id: str
    objective: str
    status: str
    created_at: str
    updated_at: str


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


class SendMessageRequest(ApiModel):
    content: str = Field(min_length=1, max_length=20_000)


class ApprovalDecisionRequest(ApiModel):
    approved: bool


@dataclass(slots=True)
class AppServices:
    settings: Settings
    connection: object
    projects: ProjectRepository
    tasks: TaskRepository
    artifacts: ArtifactRepository
    datasets: DatasetService
    approvals: ApprovalRepository
    tools: ToolRegistry
    provider: ModelProvider | None

    def agent(self) -> AgentRunner:
        if self.provider is None:
            raise RuntimeError("No model provider is configured")
        return AgentRunner(
            settings=self.settings,
            provider=self.provider,
            tools=self.tools,
            tasks=self.tasks,
        )


def build_services(
    settings: Settings,
    provider: ModelProvider | None = None,
) -> AppServices:
    connection = connect_database(settings)
    workspace = WorkspaceService(settings)
    projects = ProjectRepository(connection)
    tasks = TaskRepository(connection)
    artifacts = ArtifactRepository(connection, workspace)
    datasets = DatasetService(workspace, artifacts)
    approvals = ApprovalRepository(connection)
    reports = ReportService(workspace, artifacts, tasks)
    tools = build_tool_registry(
        datasets=datasets,
        duckdb_executor=DuckDBExecutor(workspace, artifacts),
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
    )
    return AppServices(
        settings=settings,
        connection=connection,
        projects=projects,
        tasks=tasks,
        artifacts=artifacts,
        datasets=datasets,
        approvals=approvals,
        tools=tools,
        provider=provider,
    )


def create_app(
    settings: Settings,
    provider: ModelProvider | None = None,
) -> FastAPI:
    services = build_services(settings, provider)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
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

    @app.exception_handler(RecordNotFoundError)
    async def not_found_handler(_, exc: RecordNotFoundError):
        return _json_error(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(DatasetError)
    @app.exception_handler(WorkspaceError)
    @app.exception_handler(RepositoryError)
    async def domain_error_handler(_, exc: Exception):
        return _json_error(status.HTTP_400_BAD_REQUEST, str(exc))

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
            artifact = services.datasets.add_file(project_id, temporary_path)
            return _artifact_response(artifact, title=file.filename or artifact.title)
        finally:
            file.file.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @app.get("/api/artifacts/{artifact_id}/dataset-inspection")
    def inspect_dataset(artifact_id: str, sample_rows: int = Query(20, ge=0, le=100)):
        return asdict(services.datasets.inspect(artifact_id, sample_rows))

    @app.get("/api/artifacts/{artifact_id}/content")
    def artifact_content(artifact_id: str) -> FileResponse:
        artifact = services.artifacts.get(artifact_id)
        return FileResponse(
            artifact.path,
            media_type=artifact.content_type,
            filename=artifact.path.name,
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
        task = services.tasks.create(project_id, request.objective)
        if provider is not None:
            background_tasks.add_task(_run_task, settings, provider, task.id)
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

    @app.post("/api/tasks/{task_id}/messages", response_model=TaskResponse)
    def send_message(
        task_id: str,
        request: SendMessageRequest,
        background_tasks: BackgroundTasks,
    ) -> TaskResponse:
        task = services.tasks.get(task_id)
        if task.status.value == "needs_input":
            if provider is None:
                services.tasks.add_message(task_id, "user", request.content)
                task = services.tasks.transition(task_id, TaskStatus.UNDERSTANDING)
            else:
                background_tasks.add_task(
                    _resume_task,
                    settings,
                    provider,
                    task_id,
                    request.content,
                )
        else:
            services.tasks.add_message(task_id, "user", request.content)
        return _task_response(task)

    @app.post("/api/approvals/{approval_id}/decision")
    def decide_approval(
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> dict[str, str | None]:
        approval = services.approvals.decide(approval_id, request.approved)
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


def _run_task(settings: Settings, provider: ModelProvider, task_id: str) -> None:
    services = build_services(settings, provider)
    try:
        services.agent().run(task_id)
    finally:
        services.connection.close()


def _resume_task(
    settings: Settings,
    provider: ModelProvider,
    task_id: str,
    content: str,
) -> None:
    services = build_services(settings, provider)
    try:
        services.agent().resume(task_id, content)
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


def _task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        objective=task.objective,
        status=task.status.value,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


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


def _json_error(status_code: int, detail: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})
