from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from data_analysis_agent.domain import ArtifactType


def test_add_and_inspect_csv(
    dataset_service,
    project,
    operations_csv: Path,
) -> None:
    artifact = dataset_service.add_file(project.id, operations_csv)

    inspection = dataset_service.inspect(artifact.id, sample_rows=3)

    assert artifact.artifact_type is ArtifactType.SOURCE_DATASET
    assert inspection.row_count == 12
    assert [column.name for column in inspection.columns][:2] == ["month", "region"]
    assert len(inspection.sample) == 3
    assert inspection.duplicate_row_count == 0


def test_add_and_inspect_excel(
    dataset_service,
    project,
    operations_csv: Path,
    tmp_path: Path,
) -> None:
    rows = [line.split(",") for line in operations_csv.read_text(encoding="utf-8").splitlines()]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "operations"
    for row in rows:
        sheet.append(row)
    workbook_path = tmp_path / "operations.xlsx"
    workbook.save(workbook_path)

    artifact = dataset_service.add_file(project.id, workbook_path)
    inspection = dataset_service.inspect(artifact.id)

    assert inspection.sheets == ("operations",)
    assert inspection.row_count == 12
    assert inspection.columns[0].name == "month"


def test_inspection_caps_sample_at_one_hundred_rows(
    dataset_service,
    project,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "large.csv"
    csv_path.write_text(
        "value\n" + "\n".join(str(value) for value in range(150)) + "\n",
        encoding="utf-8",
    )
    artifact = dataset_service.add_file(project.id, csv_path)

    inspection = dataset_service.inspect(artifact.id, sample_rows=1_000)

    assert len(inspection.sample) == 100
