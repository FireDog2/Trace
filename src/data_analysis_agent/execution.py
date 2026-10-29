from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event, Timer
from typing import Any, Mapping
from uuid import uuid4

import duckdb
import polars as pl

from data_analysis_agent.datasets import _read_excel
from data_analysis_agent.domain import Artifact, ArtifactType, ExecutionStatus
from data_analysis_agent.python_runtime import run_in_workspace, workspace_exceeded
from data_analysis_agent.repositories import ArtifactRepository, TaskRepository
from data_analysis_agent.settings import Settings
from data_analysis_agent.workspace import WorkspaceBoundaryError, WorkspaceService

READ_ONLY_PREFIXES = frozenset({"select", "with", "describe", "summarize", "explain"})
MUTATING_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|copy|attach|detach|install|load|"
    r"pragma|call|export|import|truncate|vacuum)\b",
    flags=re.IGNORECASE,
)
SAFE_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BANNED_IMPORT_ROOTS = frozenset({"ftplib", "httpx", "requests", "socket", "subprocess", "urllib"})
BANNED_CALLS = frozenset({"__import__", "eval", "exec", "os.popen", "os.system", "shutil.rmtree"})


class ExecutionError(RuntimeError):
    """Base error for analysis execution."""


class ReadOnlyQueryError(ExecutionError):
    """Raised when SQL is not a single read-only statement."""


class InvalidBindingError(ExecutionError):
    """Raised when a SQL table binding is invalid."""


class QueryTimeoutError(ExecutionError):
    """Raised when a bounded local query exceeds its deadline."""


class DatasetQueryError(ExecutionError):
    """Raised when DuckDB rejects a local dataset query."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class UnsafePythonError(ExecutionError):
    """Raised when generated Python requests a prohibited capability."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    preview: tuple[dict[str, Any], ...]
    row_count: int
    artifact: Artifact | None


@dataclass(frozen=True, slots=True)
class PythonResult:
    status: ExecutionStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    code_artifact: Artifact
    output_artifacts: tuple[Artifact, ...]
    error_message: str


class DuckDBExecutor:
    def __init__(
        self,
        workspace: WorkspaceService,
        artifacts: ArtifactRepository,
        timeout_seconds: float = 30,
        max_rows: int = 100_000,
        max_result_bytes: int = 20_000_000,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._workspace = workspace
        self._artifacts = artifacts
        self._timeout_seconds = timeout_seconds
        if max_rows < 1 or max_result_bytes < 1:
            raise ValueError("Query result limits must be positive")
        self._max_rows = max_rows
        self._max_result_bytes = max_result_bytes

    def query(
        self,
        project_id: str,
        sql: str,
        bindings: Mapping[str, str],
        *,
        task_id: str | None = None,
    ) -> QueryResult:
        _validate_read_only_sql(sql)
        connection = duckdb.connect(database=":memory:")
        input_artifact_ids: list[str] = []
        try:
            for table_name, artifact_id in bindings.items():
                if not SAFE_TABLE_NAME.fullmatch(table_name):
                    raise InvalidBindingError(f"Invalid table name: {table_name}")
                artifact = self._artifacts.get(artifact_id)
                if artifact.project_id != project_id:
                    raise InvalidBindingError("Dataset belongs to another project")
                if artifact.artifact_type not in {
                    ArtifactType.SOURCE_DATASET,
                    ArtifactType.DERIVED_DATASET,
                }:
                    raise InvalidBindingError("Binding is not a dataset artifact")
                _register_dataset(connection, table_name, artifact.path)
                input_artifact_ids.append(artifact.id)

            deadline_expired = Event()

            def interrupt_query() -> None:
                deadline_expired.set()
                connection.interrupt()

            watchdog = Timer(self._timeout_seconds, interrupt_query)
            watchdog.daemon = True
            watchdog.start()
            try:
                cursor = connection.execute(sql)
                columns = tuple(item[0] for item in (cursor.description or ()))
                rows: list[tuple[Any, ...]] = []
                result_bytes = 0
                while batch := cursor.fetchmany(min(512, self._max_rows + 1)):
                    for row in batch:
                        result_bytes += len(
                            json.dumps(
                                [_json_scalar(value) for value in row],
                                ensure_ascii=False,
                            ).encode("utf-8")
                        )
                        if len(rows) >= self._max_rows or result_bytes > self._max_result_bytes:
                            raise DatasetQueryError("result_limit")
                        rows.append(row)
            except duckdb.ParserException as exc:
                raise DatasetQueryError("parse_error") from exc
            except duckdb.CatalogException as exc:
                raise DatasetQueryError("unresolved_table") from exc
            except duckdb.BinderException as exc:
                raise DatasetQueryError("unresolved_column") from exc
            except DatasetQueryError:
                raise
            except duckdb.Error as exc:
                if deadline_expired.is_set():
                    raise QueryTimeoutError(
                        f"DuckDB query exceeded the {self._timeout_seconds}-second time limit"
                    ) from exc
                raise DatasetQueryError("query_error") from exc
            except Exception as exc:
                if deadline_expired.is_set():
                    raise QueryTimeoutError(
                        f"DuckDB query exceeded the {self._timeout_seconds}-second time limit"
                    ) from exc
                raise
            finally:
                watchdog.cancel()
            preview = tuple(
                {column: _json_scalar(value) for column, value in zip(columns, row, strict=True)}
                for row in rows[:200]
            )
            path = self._workspace.resolve_inside(
                project_id,
                f"derived/query-{uuid4().hex}.parquet",
            )
            frame = pl.DataFrame(rows, schema=list(columns), orient="row")
            frame.write_parquet(path)
            if path.stat().st_size > self._max_result_bytes:
                path.unlink()
                raise DatasetQueryError("result_limit")
            materialized_artifact = self._artifacts.register(
                project_id=project_id,
                task_id=task_id,
                artifact_type=ArtifactType.DERIVED_DATASET,
                path=path,
                title="Materialized query result",
                description="Complete bounded query result, including empty results",
                producer_execution_id=None,
                input_artifact_ids=tuple(input_artifact_ids),
            )
            return QueryResult(
                columns=columns,
                preview=preview,
                row_count=len(rows),
                artifact=materialized_artifact,
            )
        finally:
            connection.close()


class PythonExecutor:
    def __init__(
        self,
        settings: Settings,
        workspace: WorkspaceService,
        artifacts: ArtifactRepository,
        tasks: TaskRepository,
    ) -> None:
        self._settings = settings
        self._workspace = workspace
        self._artifacts = artifacts
        self._tasks = tasks

    def run(
        self,
        task_id: str,
        code: str,
        declared_outputs: tuple[str, ...],
        *,
        input_artifact_ids: tuple[str, ...] | None = None,
    ) -> PythonResult:
        _validate_python(code)
        task = self._tasks.get(task_id)
        root = self._workspace.project_root(task.project_id)
        execution_id = uuid4().hex
        code_path = self._workspace.resolve_inside(
            task.project_id,
            f"code/analysis-{execution_id}.py",
        )
        code_path.write_text(code, encoding="utf-8")
        code_artifact = self._artifacts.register(
            project_id=task.project_id,
            task_id=task.id,
            artifact_type=ArtifactType.PYTHON_CODE,
            path=code_path,
            title=f"Analysis code {execution_id[:8]}",
            description="Python code generated for an analysis step",
            producer_execution_id=None,
            input_artifact_ids=(),
        )
        declared_paths = self._validate_declared_outputs(
            task.project_id,
            declared_outputs,
        )
        inputs = self._input_artifacts(task.project_id, task.id, input_artifact_ids)
        started = time.monotonic()
        output_artifacts: list[Artifact] = []
        with tempfile.TemporaryDirectory(prefix="trace-python-") as temporary:
            stage = Path(temporary) / "input"
            exported = Path(temporary) / "output"
            stage.mkdir()
            exported.mkdir()
            for directory in ("source", "derived", "charts", "reports", "logs", "code"):
                (stage / directory).mkdir()
            snapshot_bytes = 0
            for artifact in (*inputs, code_artifact):
                source = self._workspace.resolve_inside(task.project_id, artifact.path)
                snapshot_bytes += source.stat().st_size
                if snapshot_bytes > self._settings.python_workspace_mb * 1024 * 1024:
                    raise UnsafePythonError("Python input snapshot exceeds its resource limit")
                destination = stage / source.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            for path in declared_paths:
                (stage / path.relative_to(root)).parent.mkdir(parents=True, exist_ok=True)
            if workspace_exceeded(stage, self._settings):
                raise UnsafePythonError("Python input snapshot exceeds its resource limit")
            before = _snapshot_files(stage)
            completed = run_in_workspace(
                self._settings, stage, code_path.relative_to(root).as_posix(), exported
            )
            exit_code = completed.returncode
            stdout, stderr = completed.stdout, completed.stderr
            error_message = completed.error
            status = (
                ExecutionStatus.TIMED_OUT
                if completed.timed_out
                else ExecutionStatus.FAILED
                if completed.error or completed.returncode != 0
                else ExecutionStatus.SUCCEEDED
            )
            if status is ExecutionStatus.FAILED and not error_message:
                error_message = "Python process exited with an error"
            # Inspect only private exported files. No generated code writes into the real project.
            if workspace_exceeded(exported, self._settings):
                status = ExecutionStatus.FAILED
                error_message = "Python outputs contain unsafe links or exceed resource limits"
            elif status is ExecutionStatus.SUCCEEDED:
                after = _snapshot_files(exported)
                allowed = {path.relative_to(root).as_posix() for path in declared_paths}
                changed_inputs = [name for name in before if before[name] != after.get(name)]
                undeclared = set(after) - set(before) - allowed
                missing = allowed - set(after)
                if changed_inputs:
                    status = ExecutionStatus.FAILED
                    error_message = "Python attempted to modify immutable input data"
                elif undeclared:
                    status = ExecutionStatus.FAILED
                    error_message = "Undeclared output: " + ", ".join(sorted(undeclared))
                elif missing:
                    status = ExecutionStatus.FAILED
                    error_message = "Declared output was not created: " + ", ".join(sorted(missing))
                else:
                    for path in declared_paths:
                        # Exclusive creation also protects concurrent runs with the same output name.
                        with (exported / path.relative_to(root)).open("rb") as output_stream:
                            with path.open("xb") as destination_stream:
                                shutil.copyfileobj(output_stream, destination_stream)
                        output_artifacts.append(
                            self._artifacts.register(
                                project_id=task.project_id,
                                task_id=task.id,
                                artifact_type=_artifact_type_for_output(path),
                                path=path,
                                title=path.name,
                                description="Output produced by isolated Python analysis",
                                producer_execution_id=None,
                                input_artifact_ids=(
                                    code_artifact.id,
                                    *(item.id for item in inputs),
                                ),
                            )
                        )

        return PythonResult(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            code_artifact=code_artifact,
            output_artifacts=tuple(output_artifacts),
            error_message=error_message,
        )

    def _input_artifacts(
        self, project_id: str, task_id: str, artifact_ids: tuple[str, ...] | None
    ) -> tuple[Artifact, ...]:
        if artifact_ids is None:
            artifacts = tuple(
                item
                for item in self._artifacts.list_for_project(project_id)
                if item.artifact_type is ArtifactType.SOURCE_DATASET
                or (item.task_id == task_id and item.artifact_type is ArtifactType.DERIVED_DATASET)
            )
        else:
            artifacts = tuple(self._artifacts.get(item) for item in dict.fromkeys(artifact_ids))
        if len(artifacts) > 64:
            raise UnsafePythonError("Too many Python input artifacts")
        for artifact in artifacts:
            if artifact.project_id != project_id or artifact.artifact_type not in {
                ArtifactType.SOURCE_DATASET,
                ArtifactType.DERIVED_DATASET,
            }:
                raise UnsafePythonError("Python input must be a registered dataset in this project")
        return artifacts

    def _validate_declared_outputs(
        self,
        project_id: str,
        declared_outputs: tuple[str, ...],
    ) -> tuple[Path, ...]:
        paths: list[Path] = []
        for relative_path in declared_outputs:
            try:
                path = self._workspace.resolve_inside(project_id, relative_path)
            except WorkspaceBoundaryError as exc:
                raise UnsafePythonError("Declared output escapes the project") from exc
            relative = path.relative_to(self._workspace.project_root(project_id))
            if not relative.parts or relative.parts[0] not in {
                "derived",
                "charts",
                "reports",
                "logs",
            }:
                raise UnsafePythonError(
                    "Python outputs must be written to a derived artifact directory"
                )
            if path.exists():
                raise UnsafePythonError("Python may not overwrite an existing artifact")
            if path in paths:
                raise UnsafePythonError("Python output paths must be unique")
            path.parent.mkdir(parents=True, exist_ok=True)
            paths.append(path)
        return tuple(paths)


def _validate_read_only_sql(sql: str) -> None:
    without_comments = re.sub(r"/\*.*?\*/|--[^\r\n]*", " ", sql, flags=re.DOTALL)
    without_strings = re.sub(r"'(?:''|[^'])*'", "''", without_comments)
    statements = [part.strip() for part in without_strings.split(";") if part.strip()]
    if len(statements) != 1:
        raise ReadOnlyQueryError("Exactly one read-only SQL statement is required")
    first_word = statements[0].split(maxsplit=1)[0].lower()
    if first_word not in READ_ONLY_PREFIXES or MUTATING_SQL.search(without_strings):
        raise ReadOnlyQueryError("Only a single read-only SQL statement is allowed")


def _register_dataset(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    path: Path,
) -> None:
    extension = path.suffix.lower()
    if extension == ".csv":
        connection.read_csv(str(path)).create_view(table_name)
        return
    if extension == ".parquet":
        connection.read_parquet(str(path)).create_view(table_name)
        return
    if extension == ".xlsx":
        frame, _ = _read_excel(path)
        connection.register(table_name, frame)
        return
    raise InvalidBindingError(f"Unsupported bound dataset: {extension}")


def _validate_python(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise UnsafePythonError(f"Invalid Python syntax: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", 1)[0] for alias in node.names}
            if roots & BANNED_IMPORT_ROOTS:
                raise UnsafePythonError("Python requested a prohibited import")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root in BANNED_IMPORT_ROOTS:
                raise UnsafePythonError("Python requested a prohibited import")
        elif isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if call_name in BANNED_CALLS:
                raise UnsafePythonError(f"Python requested a prohibited call: {call_name}")


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _snapshot_files(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        snapshot[relative] = digest.hexdigest()
    return snapshot


def _artifact_type_for_output(path: Path) -> ArtifactType:
    extension = path.suffix.lower()
    if extension in {".csv", ".parquet", ".xlsx"}:
        return ArtifactType.DERIVED_DATASET
    if extension in {".html", ".png", ".jpg", ".jpeg", ".svg"}:
        return ArtifactType.CHART
    if extension == ".md":
        return ArtifactType.MARKDOWN_REPORT
    if extension == ".py":
        return ArtifactType.PYTHON_CODE
    return ArtifactType.EXECUTION_LOG


def _json_scalar(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value
