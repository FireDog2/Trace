from __future__ import annotations

from pathlib import Path

import pytest

from data_analysis_agent.domain import ArtifactType
from data_analysis_agent.repositories import ArtifactOwnershipError
from data_analysis_agent.workspace import WorkspaceBoundaryError


def test_resolve_inside_rejects_parent_escape(workspace, project) -> None:
    with pytest.raises(WorkspaceBoundaryError):
        workspace.resolve_inside(project.id, "../outside.csv")


def test_import_source_copies_bytes_and_uses_project_source_directory(
    workspace, tmp_path: Path, project
) -> None:
    incoming = tmp_path / "sales.csv"
    incoming.write_text("region,revenue\nEast,10\n", encoding="utf-8")

    imported = workspace.import_source(project.id, incoming)
    incoming.write_text("changed", encoding="utf-8")

    assert imported.read_text(encoding="utf-8") == "region,revenue\nEast,10\n"
    assert imported.parent.name == "source"
    assert imported != incoming


def test_artifact_registration_rejects_input_from_another_project(
    artifact_repository,
    project_repository,
    workspace,
    project,
) -> None:
    first_path = workspace.resolve_inside(project.id, "derived/first.csv")
    first_path.write_text("value\n1\n", encoding="utf-8")
    first = artifact_repository.register(
        project_id=project.id,
        task_id=None,
        artifact_type=ArtifactType.DERIVED_DATASET,
        path=first_path,
        title="First result",
        description="A result from the first project",
        producer_execution_id=None,
        input_artifact_ids=(),
    )

    second_project = project_repository.create("Second project")
    workspace.create_project(second_project.id)
    second_path = workspace.resolve_inside(second_project.id, "derived/second.csv")
    second_path.write_text("value\n2\n", encoding="utf-8")

    with pytest.raises(ArtifactOwnershipError):
        artifact_repository.register(
            project_id=second_project.id,
            task_id=None,
            artifact_type=ArtifactType.DERIVED_DATASET,
            path=second_path,
            title="Second result",
            description="Must not depend on another project's artifact",
            producer_execution_id=None,
            input_artifact_ids=(first.id,),
        )
