from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from data_analysis_agent.api import build_services
from data_analysis_agent.domain import ArtifactType, TaskStatus
from data_analysis_agent.providers import ModelRequest, ModelResponse
from data_analysis_agent.settings import Settings
from data_analysis_agent.tools import ToolCall


@dataclass(frozen=True, slots=True)
class DemoManifest:
    data_dir: str
    project_id: str
    task_id: str
    run_id: str
    task_status: str
    artifact_count: int
    report_artifact_id: str
    trace_event_count: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


class DemoSecretProtector:
    """Portable protection used only for synthetic, credential-free demo data."""

    def protect(self, value: str) -> bytes:
        return b"demo-protected:" + value.encode("utf-8")

    def unprotect(self, value: bytes) -> str:
        return value.removeprefix(b"demo-protected:").decode("utf-8")


class DemoReportProvider:
    """Deterministic Report Agent that reacts to persisted tool results."""

    def __init__(self) -> None:
        self._step = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self._step += 1
        if self._step == 1:
            return _tool_response("workspace", "inspect_workspace", {})
        if self._step == 2:
            workspace = _last_tool_output(request)
            dataset_id = next(
                item["id"] for item in workspace["artifacts"] if item["type"] == "source_dataset"
            )
            return _tool_response(
                "inspect",
                "inspect_dataset",
                {"artifact_id": dataset_id, "sample_rows": 5},
            )
        if self._step == 3:
            return _tool_response(
                "python",
                "run_python",
                {
                    "code": _ANALYSIS_CODE,
                    "declared_outputs": [
                        "derived/east-profit-diagnosis.csv",
                        "charts/east-logistics-cost-trend.html",
                    ],
                },
            )
        if self._step == 4:
            result = _last_tool_result(request)
            artifact_ids = result["output"]["artifact_ids"]
            derived_id, chart_id = artifact_ids[1], artifact_ids[2]
            return _tool_response(
                "report",
                "write_report",
                {
                    "markdown": (
                        "# 华东区域利润诊断\n\n"
                        "## 核心结论\n\n"
                        "华东区域收入基本稳定，但物流成本在三月明显上升，"
                        "导致营业利润下滑。可复现的区域明细为 "
                        f"artifact://{derived_id}，趋势证据为 artifact://{chart_id}。\n\n"
                        "## 建议\n\n"
                        "在调整利润目标前，优先核查承运商价格和发货结构。"
                    )
                },
            )
        report_id = _last_tool_output(request)["artifact_id"]
        return ModelResponse(
            text=json.dumps(
                {
                    "summary": "华东区域利润下滑主要伴随物流成本上升。",
                    "report_artifact_id": report_id,
                },
                ensure_ascii=False,
            ),
            tool_calls=(),
        )


class DemoLeadProvider:
    """Deterministic Lead that delegates artifact production to a teammate."""

    def __init__(self) -> None:
        self._step = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self._step += 1
        if self._step == 1:
            return _tool_response(
                "plan",
                "update_plan",
                {
                    "steps": [
                        "委派证据化经营诊断",
                        "检查 Report Agent 的结构化交接",
                    ],
                    "reason": "报告和分析工件由 Report Agent 生成。",
                },
            )
        if self._step == 2:
            return _tool_response(
                "spawn-report",
                "spawn_teammate",
                {
                    "role": "report",
                    "objective": ("检查经营数据，诊断华东利润下滑，生成趋势证据并发布报告。"),
                },
            )
        if self._step == 3:
            agent_id = _last_tool_output(request)["agent_id"]
            return _tool_response(
                "await-report",
                "await_teammates",
                {"agent_ids": [agent_id], "timeout_seconds": 5},
            )
        handoff = _last_tool_output(request)["agents"][0]
        if handoff["status"] != "completed":
            raise RuntimeError("deterministic Report Agent did not complete")
        return ModelResponse(
            text="诊断已完成，正式报告和支持证据已经注册到项目工作区。",
            tool_calls=(),
        )


def run_portfolio_demo(settings: Settings) -> DemoManifest:
    """Run the credential-free golden path and leave a browsable workspace."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    source_path = settings.data_dir / "demo-operations.csv"
    _write_demo_dataset(source_path)
    services = build_services(
        settings,
        DemoLeadProvider(),
        secret_protector=DemoSecretProtector(),
        teammate_provider_factory=DemoReportProvider,
    )
    try:
        if services.projects.list():
            raise ValueError(
                "Demo data directory is not empty; choose another directory or reset it"
            )
        project = services.projects.create("Golden Demo · 华东利润诊断")
        services.workspace.create_project(project.id)
        services.datasets.add_file(
            project.id,
            source_path,
            display_name="region_operations_demo.csv",
        )
        task = services.tasks.create(project.id, "为什么华东区域利润出现下滑？")
        completed = services.agent().run(task.id)
        if completed.status is not TaskStatus.COMPLETED:
            raise RuntimeError(f"demo task ended as {completed.status.value}")
        artifacts = services.artifacts.list_for_task(task.id)
        report = next(
            artifact
            for artifact in artifacts
            if artifact.artifact_type is ArtifactType.MARKDOWN_REPORT
        )
        run = services.traces.list_runs(task.id)[-1]
        events = services.traces.events_for_run(run.id)
        manifest = DemoManifest(
            data_dir=str(settings.data_dir),
            project_id=project.id,
            task_id=task.id,
            run_id=run.id,
            task_status=completed.status.value,
            artifact_count=len(artifacts),
            report_artifact_id=report.id,
            trace_event_count=len(events),
        )
        (settings.data_dir / "demo-manifest.json").write_text(
            manifest.to_json() + "\n",
            encoding="utf-8",
        )
        return manifest
    finally:
        if services.team_manager is not None:
            services.team_manager.shutdown()
        services.connection.close()


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


def _write_demo_dataset(path: Path) -> None:
    rows = (
        ("2026-01", "East", "Electronics", 120, 60, 10, 50),
        ("2026-02", "East", "Electronics", 118, 59, 12, 47),
        ("2026-03", "East", "Electronics", 119, 59, 28, 32),
        ("2026-01", "East", "Home", 80, 42, 8, 30),
        ("2026-02", "East", "Home", 82, 43, 9, 30),
        ("2026-03", "East", "Home", 81, 42, 20, 19),
        ("2026-01", "West", "Electronics", 110, 55, 10, 45),
        ("2026-02", "West", "Electronics", 112, 56, 10, 46),
        ("2026-03", "West", "Electronics", 114, 57, 10, 47),
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "month",
                "region",
                "category",
                "revenue",
                "product_cost",
                "logistics_cost",
                "profit",
            )
        )
        writer.writerows(rows)


_ANALYSIS_CODE = """from pathlib import Path
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
"""
