from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest

import data_analysis_agent.python_runtime as runtime
from data_analysis_agent.domain import ExecutionStatus
from data_analysis_agent.execution import PythonExecutor, UnsafePythonError
from data_analysis_agent.prompts import PromptContext, assemble_prompt
from data_analysis_agent.providers import ModelResponse
from data_analysis_agent.settings import Settings
from data_analysis_agent.team import (
    ROLE_TOOLS,
    AgentRole,
    AgentRunStatus,
    TeammateRunner,
    TeamRepository,
)
from data_analysis_agent.tools import ToolCall


def test_sql_permissions_and_analysis_prompt(tool_registry, tool_context):
    names = {item.name for item in tool_registry.restrict(ROLE_TOOLS[AgentRole.SQL]).schemas()}
    assert "run_python" in names
    assert {"query_database", "query_data", "inspect_dataset"} <= names
    assert not {"write_report", "spawn_teammate", "save_artifact"} & names
    assert "run_python" not in ROLE_TOOLS[AgentRole.KNOWLEDGE]
    assert "run_python" in ROLE_TOOLS[AgentRole.REPORT]
    prompt = assemble_prompt(PromptContext(role="sql", enabled_tools=tuple(names))).text
    for text in ("Python/Pandas", "CSV", "Excel", "read-only", "input_artifact_ids"):
        assert text in prompt
    result = tool_registry.restrict(ROLE_TOOLS[AgentRole.KNOWLEDGE]).execute(
        ToolCall("denied", "run_python", {"code": "print(1)"}), tool_context
    )
    assert not result.success and result.error_category == "unknown_tool"


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_sql_agent_pandas_to_validated_handoff(
    extension,
    settings,
    python_executor,
    db_connection,
    tool_registry,
    dataset_service,
    task,
    tmp_path,
    artifact_repository,
    workspace,
):
    import pandas as pd

    file = tmp_path / f"input.{extension}"
    frame = pd.DataFrame({"region": ["East", "West", "East"], "profit": [10, 50, 20]})
    if extension == "csv":
        frame.to_csv(file, index=False)
    else:
        frame.to_excel(file, index=False)
    artifact = dataset_service.add_file(task.project_id, file)
    relative = workspace.relative_path(task.project_id, artifact.path)
    # The first pandas import in a fresh interpreter can take more than one second on Windows.
    python_executor._settings = replace(settings, python_timeout_seconds=15)

    class Provider:
        calls = 0

        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                reader = "read_csv" if extension == "csv" else "read_excel"
                return ModelResponse(
                    "",
                    (
                        ToolCall(
                            "pandas",
                            "run_python",
                            {
                                "code": (
                                    f"import pandas as pd\ndf = pd.{reader}({relative!r})\n"
                                    "df.groupby('region', as_index=False)['profit'].sum()"
                                    ".to_csv('derived/grouped.csv', index=False)"
                                ),
                                "declared_outputs": ["derived/grouped.csv"],
                                "input_artifact_ids": [artifact.id],
                            },
                        ),
                    ),
                )
            result = json.loads(request.messages[-1].content)
            assert result["success"], result
            return ModelResponse(
                json.dumps(
                    {
                        "protocol_version": "1",
                        "status": "ready",
                        "summary": "Aggregated profit",
                        "limitations": [],
                        "output_artifact_ids": [result["output"]["artifact_ids"][1]],
                    }
                ),
                (),
            )

    repository = TeamRepository(db_connection)
    run = repository.create_run(task.id, AgentRole.SQL, "Aggregate CSV/Excel profit using pandas")
    completed = TeammateRunner(
        settings=replace(settings, python_timeout_seconds=15),
        provider=Provider(),
        tools=tool_registry,
        repository=repository,
    ).run(run.id)
    assert completed.status is AgentRunStatus.COMPLETED
    handoff = json.loads(completed.result_json)
    output_id = handoff["output_artifact_ids"][0]
    output = artifact_repository.get(output_id)
    assert pd.read_csv(output.path).to_dict("records") == [
        {"region": "East", "profit": 30},
        {"region": "West", "profit": 50},
    ]
    assert json.loads(completed.handoff_validation_json)["status"] == "validated"
    assert db_connection.execute(
        "SELECT 1 FROM artifact_inputs WHERE artifact_id=? AND input_artifact_id=?",
        (output_id, artifact.id),
    ).fetchone()


def test_python_never_modifies_real_input(python_executor, dataset_artifact, task):
    original = dataset_artifact.path.read_bytes()
    result = python_executor.run(
        task.id,
        "from pathlib import Path\nnext(Path('source').glob('*.csv')).write_text('bad')",
        (),
    )
    assert result.status is ExecutionStatus.FAILED
    assert "immutable input" in result.error_message
    assert dataset_artifact.path.read_bytes() == original


def test_python_limits_stdout_without_unbounded_capture(python_executor, task):
    result = python_executor.run(task.id, "print('x' * 1000000)", ())
    assert result.status is ExecutionStatus.FAILED
    assert "byte limit" in result.error_message
    assert len(result.stdout.encode()) <= python_executor._settings.python_output_limit_bytes


def test_python_workspace_limit_prevents_artifact_publication(python_executor, task, workspace):
    python_executor._settings = replace(python_executor._settings, python_workspace_mb=16)
    result = python_executor.run(
        task.id,
        (
            "from pathlib import Path\nPath('derived/large.csv').write_bytes(b'x' * (17 * 1024 * 1024))"
        ),
        ("derived/large.csv",),
    )
    assert result.status is ExecutionStatus.FAILED
    assert "resource limit" in result.error_message
    assert not result.output_artifacts
    assert not workspace.resolve_inside(task.project_id, "derived/large.csv").exists()


@pytest.mark.parametrize("code", ["import socket", "import subprocess", "eval('1 + 1')"])
def test_python_prohibited_capabilities_fail_before_execution(python_executor, task, code):
    with pytest.raises(UnsafePythonError, match="prohibited"):
        python_executor.run(task.id, code, ())


def test_python_rejects_missing_and_unsafe_outputs(python_executor, task, workspace):
    with pytest.raises(UnsafePythonError):
        python_executor.run(task.id, "print(1)", ("../outside.csv",))
    result = python_executor.run(task.id, "print(1)", ("derived/missing.csv",))
    assert result.status is ExecutionStatus.FAILED
    assert "not created" in result.error_message
    assert not workspace.resolve_inside(task.project_id, "derived/missing.csv").exists()


def test_explicit_empty_input_and_private_cwd(python_executor, dataset_artifact, task, monkeypatch):
    monkeypatch.setenv("TRACE_SECRET_TEST", "private-token")
    result = python_executor.run(
        task.id,
        (
            "import os\nfrom pathlib import Path\n"
            "assert not list(Path('source').glob('*'))\n"
            "assert os.environ.get('TRACE_SECRET_TEST') is None\n"
            "assert 'trace-python-' in str(Path.cwd())\n"
        ),
        (),
        input_artifact_ids=(),
    )
    assert result.status is ExecutionStatus.SUCCEEDED


def test_cross_project_input_denied(
    python_executor, task, dataset_service, project_repository, workspace, operations_csv
):
    other = project_repository.create("Other")
    workspace.create_project(other.id)
    artifact = dataset_service.add_file(other.id, operations_csv)
    with pytest.raises(UnsafePythonError, match="this project"):
        python_executor.run(task.id, "print(1)", (), input_artifact_ids=(artifact.id,))


def test_docker_policy_has_limits_and_only_private_readonly_mount(settings, tmp_path):
    secure = replace(settings, python_backend="docker")
    command = runtime.docker_command(secure, "docker", "test", tmp_path, "code/a.py")
    for flag in (
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--pull=never",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--log-driver=none",
    ):
        assert flag in command
    assert command[command.index("--memory") + 1] == "1024m"
    assert command[command.index("--memory-swap") + 1] == "1024m"
    assert command[command.index("--cpus") + 1] == "1.0"
    assert command[command.index("--pids-limit") + 1] == "64"
    mount = command[command.index("--mount") + 1]
    assert mount == f"type=bind,source={tmp_path},target=/input,readonly"
    assert str(settings.data_dir) not in " ".join(command)
    assert "size=256m" in command[command.index("--tmpfs") + 1]


def test_missing_docker_fails_without_executing_host(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    monkeypatch.setattr(runtime, "bounded_process", lambda *a, **k: pytest.fail("host executed"))
    result = runtime.run_in_workspace(
        replace(settings, python_backend="docker"), tmp_path, "code.py", tmp_path / "out"
    )
    assert result.returncode is None and "no local fallback" in result.error


@pytest.mark.parametrize("failure", ["start", "exec", "timeout", "copy", "success"])
def test_docker_always_removes_container_and_never_falls_back(
    settings,
    tmp_path,
    monkeypatch,
    failure,
):
    calls = []
    cleanup = []
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "docker")

    def process(command, **kwargs):
        calls.append(command)
        operation = command[1]
        if (failure, operation) in {("start", "run"), ("exec", "exec"), ("copy", "cp")}:
            return runtime.ProcessResult(125, stderr="private daemon path")
        if failure == "timeout" and operation == "exec":
            return runtime.ProcessResult(-9, error="time limit", timed_out=True)
        return runtime.ProcessResult(0)

    monkeypatch.setattr(runtime, "bounded_process", process)
    monkeypatch.setattr(
        runtime.subprocess, "run", lambda command, **kwargs: cleanup.append(command)
    )
    stage = tmp_path / "stage"
    exported = tmp_path / "out"
    stage.mkdir()
    exported.mkdir()
    result = runtime.run_in_workspace(
        replace(settings, python_backend="docker"), stage, "code.py", exported
    )
    assert cleanup and cleanup[0][:3] == ["docker", "rm", "--force"]
    assert all(command[0] == "docker" for command in calls)
    assert "private daemon path" not in result.stderr + result.error
    assert not result.error if failure == "success" else result.error


@pytest.mark.parametrize(
    "name,value",
    [
        ("python_backend", "unknown"),
        ("python_timeout_seconds", 0),
        ("python_memory_mb", 0),
        ("python_cpus", 0),
        ("python_pids_limit", 0),
        ("python_workspace_mb", 0),
        ("python_output_limit_bytes", 0),
        ("python_image", "--privileged"),
    ],
)
def test_python_settings_reject_invalid_limits(settings, name, value):
    with pytest.raises(ValueError):
        replace(settings, **{name: value})


def test_python_environment_settings_default_secure(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACE_PYTHON_BACKEND", raising=False)
    assert Settings.local(tmp_path).python_backend == "docker"
    monkeypatch.setenv("TRACE_PYTHON_MEMORY_MB", "512")
    monkeypatch.setenv("TRACE_PYTHON_BACKEND", "trusted_local")
    assert Settings.local(tmp_path).python_memory_mb == 512
    assert Settings.local(tmp_path).python_backend == "trusted_local"


def test_docker_client_context_is_not_forwarded_to_generated_code(monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    assert runtime.docker_environment()["DOCKER_CONTEXT"] == "desktop-linux"
    assert "DOCKER_CONTEXT" not in runtime.minimal_environment()
    assert "OPENAI_API_KEY" not in runtime.docker_environment()


def test_bounded_process_spawn_failure_is_structured(settings, tmp_path):
    result = runtime.bounded_process(
        [str(tmp_path / "nonexistent-python")],
        cwd=tmp_path,
        settings=settings,
        environment=runtime.minimal_environment(),
    )
    assert result.returncode is None and "unavailable" in result.error


@pytest.mark.skipif(
    os.environ.get("TRACE_TEST_DOCKER") != "1", reason="TRACE_TEST_DOCKER is not set"
)
def test_live_docker_pandas_isolation(
    settings, workspace, artifact_repository, task_repository, task, tmp_path
):
    executor = PythonExecutor(
        replace(settings, python_backend="docker", python_timeout_seconds=30),
        workspace,
        artifact_repository,
        task_repository,
    )
    result = executor.run(
        task.id,
        (
            "import pandas as pd\n"
            "from pathlib import Path\n"
            "assert not Path('/var/run/docker.sock').exists()\n"
            "pd.DataFrame({'value': [42]}).to_csv('derived/live.csv', index=False)"
        ),
        ("derived/live.csv",),
    )
    assert result.status is ExecutionStatus.SUCCEEDED


@pytest.mark.skipif(
    os.environ.get("TRACE_TEST_DOCKER") != "1", reason="TRACE_TEST_DOCKER is not set"
)
def test_live_docker_enforces_network_user_and_readonly_mount(settings, tmp_path):
    stage = tmp_path / "input"
    exported = tmp_path / "output"
    stage.mkdir()
    exported.mkdir()
    (stage / "check.py").write_text(
        "import os, socket\nfrom pathlib import Path\n"
        "assert os.getuid() == 65534\n"
        "try:\n Path('/input/escape').write_text('bad')\n"
        "except OSError:\n pass\nelse:\n raise AssertionError('input mount is writable')\n"
        "try:\n socket.create_connection(('1.1.1.1', 80), timeout=1)\n"
        "except OSError:\n pass\nelse:\n raise AssertionError('network is enabled')\n",
        encoding="utf-8",
    )
    # Bypass AST intentionally: the transport must enforce policy even for hostile Python.
    result = runtime.run_in_workspace(
        replace(settings, python_backend="docker", python_timeout_seconds=30),
        stage,
        "check.py",
        exported,
    )
    assert result.returncode == 0 and not result.error
