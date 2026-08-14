from __future__ import annotations

import pytest

from data_analysis_agent.reporting import InvalidEvidenceReference


def test_publish_rejects_missing_artifact_reference(report_service, task) -> None:
    markdown = "## Key Findings\nRevenue declined. [evidence](artifact://missing)"

    with pytest.raises(InvalidEvidenceReference):
        report_service.publish(task.id, markdown)


def test_publish_records_valid_evidence_link(
    report_service,
    task,
    chart_artifact,
    artifact_repository,
) -> None:
    markdown = f"## Key Findings\nCosts increased. [evidence](artifact://{chart_artifact.id})"

    report = report_service.publish(task.id, markdown)

    assert artifact_repository.evidence_for(report.id) == (chart_artifact.id,)
    assert report.path.read_text(encoding="utf-8") == markdown


def test_key_findings_require_evidence(report_service, task) -> None:
    with pytest.raises(InvalidEvidenceReference):
        report_service.publish(task.id, "## Key Findings\nProfit declined.")
