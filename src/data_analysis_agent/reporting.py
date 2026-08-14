from __future__ import annotations

import re
from uuid import uuid4

from data_analysis_agent.domain import Artifact, ArtifactType
from data_analysis_agent.repositories import (
    ArtifactRepository,
    RecordNotFoundError,
    TaskRepository,
)
from data_analysis_agent.workspace import WorkspaceService

ARTIFACT_REFERENCE = re.compile(r"artifact://([A-Za-z0-9_-]+)")


class ReportingError(RuntimeError):
    """Base error for report publication."""


class InvalidEvidenceReference(ReportingError):
    """Raised when a report cites missing or unrelated evidence."""


class ReportService:
    def __init__(
        self,
        workspace: WorkspaceService,
        artifacts: ArtifactRepository,
        tasks: TaskRepository,
    ) -> None:
        self._workspace = workspace
        self._artifacts = artifacts
        self._tasks = tasks

    def publish(self, task_id: str, markdown: str) -> Artifact:
        if not markdown.strip():
            raise ValueError("Report must not be empty")
        task = self._tasks.get(task_id)
        evidence_ids = tuple(dict.fromkeys(ARTIFACT_REFERENCE.findall(markdown)))
        if "## Key Findings" in markdown and not evidence_ids:
            raise InvalidEvidenceReference("Key findings require artifact evidence")
        for evidence_id in evidence_ids:
            try:
                evidence = self._artifacts.get(evidence_id)
            except RecordNotFoundError as exc:
                raise InvalidEvidenceReference(
                    f"Evidence artifact does not exist: {evidence_id}"
                ) from exc
            if evidence.project_id != task.project_id:
                raise InvalidEvidenceReference("Evidence artifact belongs to another project")

        path = self._workspace.resolve_inside(
            task.project_id,
            f"reports/report-{uuid4().hex}.md",
        )
        path.write_text(markdown, encoding="utf-8")
        report = self._artifacts.register(
            project_id=task.project_id,
            task_id=task.id,
            artifact_type=ArtifactType.MARKDOWN_REPORT,
            path=path,
            title=f"Report: {task.objective}",
            description="Evidence-linked Markdown analysis report",
            producer_execution_id=None,
            input_artifact_ids=evidence_ids,
        )
        self._artifacts.record_evidence(report.id, evidence_ids)
        return report
