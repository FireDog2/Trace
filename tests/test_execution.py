from __future__ import annotations

from data_analysis_agent.domain import ArtifactType, ExecutionStatus
import threading

import pytest

import data_analysis_agent.execution as execution_module
from data_analysis_agent.execution import DuckDBExecutor, QueryTimeoutError, ReadOnlyQueryError


def test_duckdb_rejects_mutating_sql(duckdb_executor, dataset_artifact) -> None:
    try:
        duckdb_executor.query(
            dataset_artifact.project_id,
            "DROP TABLE operations",
            {"operations": dataset_artifact.id},
        )
    except ReadOnlyQueryError as error:
        assert "read-only" in str(error).lower()
    else:
        raise AssertionError("Mutating SQL was executed")


def test_duckdb_queries_registered_csv(duckdb_executor, dataset_artifact) -> None:
    result = duckdb_executor.query(
        dataset_artifact.project_id,
        "SELECT region, SUM(profit) AS profit "
        "FROM operations GROUP BY region ORDER BY region",
        {"operations": dataset_artifact.id},
    )

    assert result.columns == ("region", "profit")
    assert result.row_count == 3
    assert result.preview[0] == {"region": "East", "profit": 208}


def test_duckdb_timeout_interrupts_and_closes_connection(
    monkeypatch, workspace, artifact_repository, project
) -> None:
    interrupted = threading.Event()

    class BlockingConnection:
        closed = False

        def execute(self, sql: str):
            assert sql == "SELECT 1"
            assert interrupted.wait(1)
            raise RuntimeError("query interrupted")

        def interrupt(self) -> None:
            interrupted.set()

        def close(self) -> None:
            self.closed = True

    connection = BlockingConnection()
    monkeypatch.setattr(execution_module.duckdb, "connect", lambda **_: connection)
    executor = DuckDBExecutor(workspace, artifact_repository, timeout_seconds=0.01)

    with pytest.raises(QueryTimeoutError, match="time limit"):
        executor.query(project.id, "SELECT 1", {})

    assert interrupted.is_set()
    assert connection.closed is True


def test_python_timeout_is_structured(python_executor, task) -> None:
    result = python_executor.run(task.id, "while True: pass", ())

    assert result.status is ExecutionStatus.TIMED_OUT
    assert "time limit" in result.error_message.lower()


def test_python_rejects_and_removes_undeclared_output(
    python_executor,
    workspace,
    task,
) -> None:
    result = python_executor.run(
        task.id,
        "from pathlib import Path\n"
        "Path('derived/hidden.txt').write_text('x', encoding='utf-8')",
        (),
    )

    assert result.status is ExecutionStatus.FAILED
    assert "undeclared output" in result.error_message.lower()
    assert not workspace.resolve_inside(task.project_id, "derived/hidden.txt").exists()


def test_python_registers_declared_output(python_executor, task) -> None:
    result = python_executor.run(
        task.id,
        "from pathlib import Path\n"
        "Path('derived/result.csv').write_text('metric,value\\nprofit,208\\n', encoding='utf-8')",
        ("derived/result.csv",),
    )

    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.stdout == ""
    assert len(result.output_artifacts) == 1
    assert result.output_artifacts[0].artifact_type is ArtifactType.DERIVED_DATASET
