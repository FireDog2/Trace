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
    chart_artifact_id: str
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
        self._chart_id = ""
        self._derived_id = ""

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
            self._chart_id = chart_id
            self._derived_id = derived_id
            return _tool_response(
                "report",
                "write_report",
                {
                    "markdown": (
                        "# East Region · March Profit Review\n\n"
                        "> **Management decision:** investigate the East region logistics-cost spike first. "
                        "Revenue was flat, while logistics cost more than doubled.\n\n"
                        "## Executive summary\n\n"
                        "East profit declined from **77 in February to 51 in March**, a **26-point / 33.8% drop**. "
                        "Revenue stayed at 200 and product cost improved by 1, but logistics cost rose from 21 to 48. "
                        "The 27-point logistics increase fully explains the decline.\n\n"
                        "## Driver bridge · February → March\n\n"
                        "| Driver | Change | Profit effect |\n"
                        "| --- | ---: | ---: |\n"
                        "| Revenue | 0 | 0 |\n"
                        "| Product cost | -1 | +1 |\n"
                        "| Logistics cost | +27 | -27 |\n"
                        "| **Operating profit** | **-26** | **-26** |\n\n"
                        "## Category pattern\n\n"
                        "Electronics profit fell by 15 as logistics cost rose from 12 to 28. Home profit fell by 11 "
                        "as logistics cost rose from 9 to 20. The pattern is broad-based rather than isolated to one category.\n\n"
                        "## What management should investigate first\n\n"
                        "1. Carrier rate changes, fuel surcharges, and expedited-shipping fees posted in March.\n"
                        "2. Shipment mix by category, route, warehouse, and service level.\n"
                        "3. Invoice anomalies or timing shifts that moved February costs into March.\n\n"
                        "## Evidence package\n\n"
                        f"- Reproducible driver data: artifact://{derived_id}\n"
                        f"- Profit and logistics visual: artifact://{chart_id}\n"
                    )
                },
            )
        report_id = _last_tool_output(request)["artifact_id"]
        return ModelResponse(
            text=json.dumps(
                {
                    "protocol_version": "1",
                    "status": "ready",
                    "limitations": [],
                    "summary": "East profit fell 33.8% in March; the 27-point logistics-cost increase fully explains the decline.",
                    "report_artifact_id": report_id,
                    "chart_artifact_ids": [self._chart_id],
                    "evidence_artifact_ids": [self._derived_id, self._chart_id],
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
                        "Delegate evidence-based business diagnosis",
                        "Review the report agent structured handoff",
                    ],
                    "reason": "Reports and analysis artifacts are generated by the report agent.",
                },
            )
        if self._step == 2:
            return _tool_response(
                "spawn-report",
                "spawn_teammate",
                {
                    "role": "report",
                    "objective": (
                        "Quantify the East region March profit decline versus February, break down the drivers by category, "
                        "create a decision-relevant comparison chart, and publish an evidence-linked management report."
                    ),
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
            text=(
                "The full diagnosis is complete: East profit fell 33.8% in March, driven by a 27-point increase in "
                "logistics cost. The management report, driver data, and comparison chart are available in the evidence workspace."
            ),
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
        project = services.projects.create("Golden Demo · East March Profit Review")
        services.workspace.create_project(project.id)
        services.datasets.add_file(
            project.id,
            source_path,
            display_name="region_operations_demo.csv",
        )
        task = services.tasks.create(
            project.id,
            (
                "Why did profit in the East region decline in March versus February? Quantify the drivers by category, "
                "create a comparison chart, and publish an evidence-linked management report with the first issue to investigate."
            ),
        )
        completed = services.agent().run(task.id)
        if completed.status is not TaskStatus.COMPLETED:
            raise RuntimeError(f"demo task ended as {completed.status.value}")
        artifacts = services.artifacts.list_for_task(task.id)
        report = next(
            artifact
            for artifact in artifacts
            if artifact.artifact_type is ArtifactType.MARKDOWN_REPORT
        )
        chart = next(
            artifact for artifact in artifacts if artifact.artifact_type is ArtifactType.CHART
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
            chart_artifact_id=chart.id,
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
from collections import defaultdict
import csv

source_path = next(Path("source").glob("*.csv"))
with source_path.open(encoding="utf-8", newline="") as source:
    rows = [row for row in csv.DictReader(source) if row["region"] == "East"]

numeric_fields = ("revenue", "product_cost", "logistics_cost", "profit")
monthly = defaultdict(lambda: {field: 0 for field in numeric_fields})
category_month = {}
for row in rows:
    values = {field: int(row[field]) for field in numeric_fields}
    for field, value in values.items():
        monthly[row["month"]][field] += value
    category_month[(row["category"], row["month"])] = values

months = sorted(monthly)
february = monthly["2026-02"]
march = monthly["2026-03"]
profit_change = march["profit"] - february["profit"]
profit_change_pct = profit_change / february["profit"] * 100
logistics_change = march["logistics_cost"] - february["logistics_cost"]
logistics_change_pct = logistics_change / february["logistics_cost"] * 100
product_cost_effect = february["product_cost"] - march["product_cost"]

output_path = Path("derived/east-profit-diagnosis.csv")
with output_path.open("w", encoding="utf-8", newline="") as output:
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "month",
            "category",
            "revenue",
            "product_cost",
            "logistics_cost",
            "profit",
        ],
    )
    writer.writeheader()
    writer.writerows(
        {
            "month": row["month"],
            "category": row["category"],
            "revenue": row["revenue"],
            "product_cost": row["product_cost"],
            "logistics_cost": row["logistics_cost"],
            "profit": row["profit"],
        }
        for row in rows
    )

plot_left = 72
plot_baseline = 270
plot_height = 210
plot_max = 90
bar_width = 42
bar_gap = 8
group_gap = 72
bar_markup = []
for index, month in enumerate(months):
    group_x = plot_left + 72 + index * (bar_width * 2 + bar_gap + group_gap)
    profit = monthly[month]["profit"]
    logistics = monthly[month]["logistics_cost"]
    profit_height = profit / plot_max * plot_height
    logistics_height = logistics / plot_max * plot_height
    bar_markup.extend(
        [
            '<rect class="profit-bar" x="{}" y="{:.1f}" width="{}" height="{:.1f}" rx="5" />'.format(
                group_x, plot_baseline - profit_height, bar_width, profit_height
            ),
            '<rect class="logistics-bar" x="{}" y="{:.1f}" width="{}" height="{:.1f}" rx="5" />'.format(
                group_x + bar_width + bar_gap,
                plot_baseline - logistics_height,
                bar_width,
                logistics_height,
            ),
            '<text class="value-label" x="{}" y="{:.1f}">{}</text>'.format(
                group_x + bar_width / 2, plot_baseline - profit_height - 9, profit
            ),
            '<text class="value-label" x="{}" y="{:.1f}">{}</text>'.format(
                group_x + bar_width + bar_gap + bar_width / 2,
                plot_baseline - logistics_height - 9,
                logistics,
            ),
            '<text class="month-label" x="{}" y="302">{}</text>'.format(
                group_x + bar_width + bar_gap / 2, month
            ),
        ]
    )

grid_markup = []
for value in (0, 30, 60, 90):
    y = plot_baseline - value / plot_max * plot_height
    grid_markup.append('<line x1="58" x2="718" y1="{:.1f}" y2="{:.1f}" />'.format(y, y))
    grid_markup.append('<text x="44" y="{:.1f}">{}</text>'.format(y + 4, value))

category_rows = []
for category in sorted({row["category"] for row in rows}):
    feb = category_month[(category, "2026-02")]
    mar = category_month[(category, "2026-03")]
    category_rows.append(
        "<tr><td>{}</td><td>{} → {}</td><td>{:+d}</td><td>{} → {}</td></tr>".format(
            category,
            feb["profit"],
            mar["profit"],
            mar["profit"] - feb["profit"],
            feb["logistics_cost"],
            mar["logistics_cost"],
        )
    )

html = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>East region March profit review</title>
<style>
:root { color-scheme: light; font-family: "Segoe UI", Arial, sans-serif; color: #172238; background: #eef3fa; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; background: #eef3fa; }
main { max-width: 980px; margin: 0 auto; }
.hero { padding: 28px 30px; border-radius: 20px; background: #0c1732; color: #f7faff; box-shadow: 0 22px 54px rgba(20, 38, 70, .18); }
.eyebrow { color: #72d5ff; font: 700 11px Consolas, monospace; letter-spacing: .14em; }
h1 { max-width: 720px; margin: 12px 0 8px; font-size: clamp(28px, 5vw, 46px); line-height: 1.04; letter-spacing: -.04em; }
.hero p { max-width: 760px; margin: 0; color: #bdc9df; font-size: 15px; line-height: 1.65; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 12px 0; }
.kpi { min-height: 116px; padding: 18px; border: 1px solid #dfe7f3; border-radius: 16px; background: #fff; }
.kpi span { color: #748198; font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.kpi strong { display: block; margin-top: 12px; font-size: 27px; letter-spacing: -.035em; }
.kpi small { display: block; margin-top: 5px; color: #68758a; line-height: 1.45; }
.kpi.alert strong { color: #d65151; }
.kpi.cost strong { color: #d97a2b; }
.panel { margin-top: 12px; padding: 22px; border: 1px solid #dfe7f3; border-radius: 18px; background: #fff; }
.panel-header { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.panel-header h2 { margin: 0; font-size: 19px; letter-spacing: -.02em; }
.legend { display: flex; gap: 14px; color: #647187; font-size: 11px; }
.legend i { display: inline-block; width: 9px; height: 9px; margin-right: 5px; border-radius: 2px; }
.legend .profit { background: #416cef; }
.legend .logistics { background: #f0a34a; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
svg line { stroke: #e3e9f2; stroke-width: 1; }
svg text { fill: #8a96a8; font-size: 11px; text-anchor: end; }
svg .profit-bar { fill: #416cef; }
svg .logistics-bar { fill: #f0a34a; }
svg .value-label { fill: #3c4860; font-size: 12px; font-weight: 700; text-anchor: middle; }
svg .month-label { fill: #59667b; font-size: 11px; font-weight: 600; text-anchor: middle; }
.bridge { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.driver { padding: 14px; border-radius: 12px; background: #f5f8fd; }
.driver span { display: block; color: #68758a; font-size: 11px; }
.driver strong { display: block; margin-top: 8px; font-size: 21px; }
.driver.negative strong { color: #cf4f4f; }
.driver.positive strong { color: #269d65; }
.driver.total { background: #172238; color: white; }
.driver.total span { color: #aebbd0; }
.category-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.category-table th, .category-table td { padding: 12px 10px; border-bottom: 1px solid #e6ebf3; text-align: right; }
.category-table th:first-child, .category-table td:first-child { text-align: left; }
.priority { display: grid; grid-template-columns: 46px 1fr; gap: 12px; align-items: start; }
.priority b { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 12px; background: #fff0e2; color: #bd6822; font: 700 13px Consolas, monospace; }
.priority h2 { margin: 0 0 5px; font-size: 18px; }
.priority p { margin: 0; color: #667389; line-height: 1.55; }
@media (max-width: 720px) { body { padding: 12px; } .kpis, .bridge { grid-template-columns: 1fr 1fr; } .panel { padding: 16px; } .panel-header { display: block; } .legend { margin-top: 8px; } }
</style>
</head>
<body>
<main>
  <section class="hero">
    <div class="eyebrow">EAST REGION · MARCH REVIEW</div>
    <h1>Flat revenue. A logistics shock. Profit down 33.8%.</h1>
    <p>The evidence points to a cost problem, not a demand problem. Management should investigate carrier pricing, expedited shipping, and invoice timing first.</p>
  </section>
  <section class="kpis" aria-label="Key performance indicators">
    <article class="kpi alert"><span>Profit</span><strong>77 → 51</strong><small>26-point decline · __PROFIT_PCT__%</small></article>
    <article class="kpi"><span>Revenue</span><strong>200 → 200</strong><small>No month-over-month change</small></article>
    <article class="kpi cost"><span>Logistics cost</span><strong>21 → 48</strong><small>+27 · +__LOGISTICS_PCT__%</small></article>
    <article class="kpi"><span>First investigation</span><strong>Carrier cost</strong><small>Rates, surcharges, service mix, timing</small></article>
  </section>
  <section class="panel">
    <div class="panel-header"><h2>Profit and logistics cost by month</h2><div class="legend"><span><i class="profit"></i>Profit</span><span><i class="logistics"></i>Logistics cost</span></div></div>
    <svg viewBox="0 0 760 325" role="img" aria-label="Grouped bar chart showing East profit falling and logistics cost rising sharply in March">
      <g>__GRID__</g><g>__BARS__</g>
    </svg>
  </section>
  <section class="panel">
    <div class="panel-header"><h2>February → March profit bridge</h2></div>
    <div class="bridge">
      <div class="driver"><span>Revenue effect</span><strong>0</strong></div>
      <div class="driver positive"><span>Product cost effect</span><strong>+__PRODUCT_EFFECT__</strong></div>
      <div class="driver negative"><span>Logistics effect</span><strong>-__LOGISTICS_CHANGE__</strong></div>
      <div class="driver total"><span>Net profit change</span><strong>__PROFIT_CHANGE__</strong></div>
    </div>
  </section>
  <section class="panel">
    <div class="panel-header"><h2>The decline affects both categories</h2></div>
    <table class="category-table"><thead><tr><th>Category</th><th>Profit, Feb → Mar</th><th>Profit change</th><th>Logistics, Feb → Mar</th></tr></thead><tbody>__CATEGORY_ROWS__</tbody></table>
  </section>
  <section class="panel priority">
    <b>01</b><div><h2>Investigate the logistics spike first</h2><p>Check carrier rate cards, fuel and rush surcharges, shipment service levels, route and warehouse mix, and whether invoice timing moved costs into March.</p></div>
  </section>
</main>
</body>
</html>'''

html = (
    html.replace("__PROFIT_PCT__", "{:.1f}".format(profit_change_pct))
    .replace("__LOGISTICS_PCT__", "{:.1f}".format(logistics_change_pct))
    .replace("__PRODUCT_EFFECT__", str(product_cost_effect))
    .replace("__LOGISTICS_CHANGE__", str(logistics_change))
    .replace("__PROFIT_CHANGE__", str(profit_change))
    .replace("__GRID__", "".join(grid_markup))
    .replace("__BARS__", "".join(bar_markup))
    .replace("__CATEGORY_ROWS__", "".join(category_rows))
)
Path("charts/east-logistics-cost-trend.html").write_text(html, encoding="utf-8")
"""
