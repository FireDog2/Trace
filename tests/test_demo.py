from data_analysis_agent.api import build_services
from data_analysis_agent.demo import run_portfolio_demo
from data_analysis_agent.domain import ArtifactType, StepStatus, TaskStatus
from data_analysis_agent.settings import Settings


def test_portfolio_demo_creates_a_clean_completed_workspace(tmp_path) -> None:
    settings = Settings.local(tmp_path / "demo")

    manifest = run_portfolio_demo(settings)

    services = build_services(settings, with_team_manager=False)
    try:
        task = services.tasks.get(manifest.task_id)
        plan = services.tasks.latest_plan(task.id)
        artifacts = services.artifacts.list_for_project(task.project_id)
        agents = services.team_repository.list_runs(task.id)
        trace = services.traces.events_for_run(manifest.run_id)

        assert task.status is TaskStatus.COMPLETED
        assert plan is not None
        assert {step.status for step in plan.steps} == {StepStatus.COMPLETED}
        assert {artifact.artifact_type for artifact in artifacts} >= {
            ArtifactType.SOURCE_DATASET,
            ArtifactType.DERIVED_DATASET,
            ArtifactType.CHART,
            ArtifactType.MARKDOWN_REPORT,
        }
        chart = services.artifacts.get(manifest.chart_artifact_id)
        report = services.artifacts.get(manifest.report_artifact_id)
        chart_html = chart.path.read_text(encoding="utf-8")
        report_markdown = report.path.read_text(encoding="utf-8")
        assert chart.artifact_type is ArtifactType.CHART
        assert "<svg" in chart_html
        assert "Profit and logistics cost by month" in chart_html
        assert "February → March profit bridge" in chart_html
        assert "Flat revenue. A logistics shock." in chart_html
        assert "33.8% drop" in report_markdown
        assert "What management should investigate first" in report_markdown
        assert len(agents) == 1 and agents[0].status.value == "completed"
        assert trace and manifest.trace_event_count == len(trace)
        assert (settings.data_dir / "demo-manifest.json").is_file()
    finally:
        services.connection.close()
