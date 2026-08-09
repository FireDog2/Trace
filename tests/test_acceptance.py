from __future__ import annotations

import json
from pathlib import Path

from data_analysis_agent.api import build_services
from data_analysis_agent.domain import ArtifactType, TaskStatus
from data_analysis_agent.providers import ModelRequest, ModelResponse
from data_analysis_agent.tools import ToolCall


class TestProtector:
    """Deterministic secret protection for cross-platform acceptance tests."""

    def protect(self, value: str) -> bytes:
        return b"test-protected:" + value.encode("utf-8")

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"test-protected:").decode("utf-8")


class OperatingDiagnosisProvider:
    """Deterministic Report Agent that reacts to persisted tool results."""

    def __init__(self) -> None:
        self.step = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.step += 1
        if self.step == 1:
            return _tool_response("workspace", "inspect_workspace", {})
        if self.step == 2:
            workspace_result = _last_tool_output(request)
            dataset_id = next(
                item["id"]
                for item in workspace_result["artifacts"]
                if item["type"] == "source_dataset"
            )
            return _tool_response(
                "inspect",
                "inspect_dataset",
                {"artifact_id": dataset_id, "sample_rows": 5},
            )
        if self.step == 3:
            return _tool_response(
                "python",
                "run_python",
                {
                    "code": ANALYSIS_CODE,
                    "declared_outputs": [
                        "derived/east-profit-diagnosis.csv",
                        "charts/east-logistics-cost-trend.html",
                    ],
                },
            )
        if self.step == 4:
            result = _last_tool_result(request)
            assert result["success"], result
            artifact_ids = result["output"]["artifact_ids"]
            derived_id, chart_id = artifact_ids[1], artifact_ids[2]
            return _tool_response(
                "report",
                "write_report",
                {
                    "markdown": (
                        "# East Region Operating Diagnosis\n\n"
                        "## Key Findings\n\n"
                        "East profit weakened while logistics cost increased over the "
                        "observed months. The reproducible regional extract is "
                        f"artifact://{derived_id}, and the trend view is "
                        f"artifact://{chart_id}.\n\n"
                        "## Next Action\n\n"
                        "Review carrier pricing and shipment mix before changing the "
                        "profit target."
                    )
                },
            )
        report_id = _last_tool_output(request)["artifact_id"]
        return ModelResponse(
            text=json.dumps(
                {
                    "summary": "East profit weakened while logistics cost increased.",
                    "report_artifact_id": report_id,
                }
            ),
            tool_calls=(),
        )


class LeadDiagnosisProvider:
    """Deterministic Lead that delegates all artifact production."""

    def __init__(self) -> None:
        self.step = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.step += 1
        if self.step == 1:
            return _tool_response(
                "plan",
                "update_plan",
                {
                    "steps": [
                        "Delegate evidence-linked diagnosis and report",
                        "Review the Report Agent handoff",
                    ],
                    "reason": "Artifact production belongs to the Report Agent.",
                },
            )
        if self.step == 2:
            return _tool_response(
                "spawn-report",
                "spawn_teammate",
                {
                    "role": "report",
                    "objective": (
                        "Inspect the operating dataset, diagnose the East profit "
                        "decline, create a chart, and publish an evidence-linked report."
                    ),
                },
            )
        if self.step == 3:
            agent_id = _last_tool_output(request)["agent_id"]
            return _tool_response(
                "await-report",
                "await_teammates",
                {"agent_ids": [agent_id], "timeout_seconds": 5},
            )
        handoff = _last_tool_output(request)["agents"][0]
        assert handoff["status"] == "completed"
        return ModelResponse(
            text="Diagnosis complete. The report and its evidence are in the workspace.",
            tool_calls=(),
        )


ANALYSIS_CODE = '''from pathlib import Path
import csv

source_path = next(Path("source").glob("*.csv"))
with source_path.open(encoding="utf-8", newline="") as source:
    rows = [row for row in csv.DictReader(source) if row["region"] == "East"]

output_path = Path("derived/east-profit-diagnosis.csv")
with output_path.open("w", encoding="utf-8", newline="") as output:
    writer = csv.DictWriter(
        output,
        fieldnames=["month", "revenue", "logistics_cost", "profit"],
    )
    writer.writeheader()
    writer.writerows(
        {
            "month": row["month"],
            "revenue": row["revenue"],
            "logistics_cost": row["logistics_cost"],
            "profit": row["profit"],
        }
        for row in rows
    )

points = "".join(
    "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
        row["month"], row["logistics_cost"], row["profit"]
    )
    for row in rows
)
Path("charts/east-logistics-cost-trend.html").write_text(
    "<html><body><h1>East logistics cost and profit</h1>"
    "<table><tr><th>Month</th><th>Logistics cost</th><th>Profit</th></tr>"
    + points
    + "</table></body></html>",
    encoding="utf-8",
)
'''


def _tool_response(call_id: str, name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        text="",
        tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
    )


def _last_tool_output(request: ModelRequest) -> dict:
    return _last_tool_result(request)["output"]


def _last_tool_result(request: ModelRequest) -> dict:
    message = next(message for message in reversed(request.messages) if message.role == "tool")
    return json.loads(message.content)


def test_operating_diagnosis_creates_traceable_report(settings, operations_csv: Path):
    provider = LeadDiagnosisProvider()
    services = build_services(
        settings,
        provider,
        secret_protector=TestProtector(),
        teammate_provider_factory=OperatingDiagnosisProvider,
    )
    project = services.projects.create("Operating review")
    services.workspace.create_project(project.id)
    dataset = services.datasets.add_file(project.id, operations_csv)
    task = services.tasks.create(project.id, "Why did East profit decline?")

    try:
        completed = services.agent().run(task.id)

        assert completed.status is TaskStatus.COMPLETED
        artifacts = services.artifacts.list_for_task(task.id)
        assert {artifact.artifact_type for artifact in artifacts} >= {
            ArtifactType.PYTHON_CODE,
            ArtifactType.DERIVED_DATASET,
            ArtifactType.CHART,
            ArtifactType.MARKDOWN_REPORT,
        }
        report = next(
            artifact
            for artifact in artifacts
            if artifact.artifact_type is ArtifactType.MARKDOWN_REPORT
        )
        markdown = report.path.read_text(encoding="utf-8")
        assert "East" in markdown
        assert "artifact://" in markdown
        assert services.artifacts.evidence_for(report.id)
        assert dataset.path.read_bytes() == operations_csv.read_bytes()
        runs = services.team_repository.list_runs(task.id)
        assert len(runs) == 1 and runs[0].role.value == "report"
        assert runs[0].status.value == "completed"
    finally:
        services.team_manager.shutdown()  # type: ignore[union-attr]
        services.connection.close()
