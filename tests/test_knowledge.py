from __future__ import annotations

import pytest

from data_analysis_agent.knowledge import BusinessKnowledgeRepository
from data_analysis_agent.repositories import RecordNotFoundError, RepositoryError


def test_business_knowledge_crud_versions_and_project_search(
    db_connection, project, project_repository
) -> None:
    repository = BusinessKnowledgeRepository(db_connection)
    metric = repository.create(
        project.id,
        kind="metric",
        name="Operating profit",
        definition="Revenue minus logistics and operating costs.",
        formula="revenue - logistics_cost - operating_cost",
        dimensions=("month", "region", "region"),
        synonyms=("Operating income", "EBIT", "ebit"),
        source="Finance metric catalog",
    )
    other = project_repository.create("Other project")
    repository.create(
        other.id,
        kind="metric",
        name="Operating profit",
        definition="A deliberately different definition.",
    )

    assert metric.version == 1
    assert metric.dimensions == ("month", "region")
    assert metric.synonyms == ("Operating income", "EBIT")
    assert repository.search(project.id, "EBIT") == (metric,)
    assert repository.search(project.id, "logistics region") == (metric,)

    updated = repository.update(
        metric.id,
        kind="metric",
        name="Operating profit",
        definition="Revenue minus all operating costs.",
        formula="revenue - operating_cost",
        dimensions=("month", "region"),
        synonyms=("Operating income",),
        source="Finance v2",
    )
    assert updated.version == 2
    assert updated.definition == "Revenue minus all operating costs."

    repository.delete(metric.id)
    with pytest.raises(RecordNotFoundError):
        repository.get(metric.id)


def test_business_knowledge_rejects_duplicate_names_case_insensitively(
    db_connection, project
) -> None:
    repository = BusinessKnowledgeRepository(db_connection)
    repository.create(
        project.id,
        kind="term",
        name="Active customer",
        definition="A customer with an order in the trailing 90 days.",
    )

    with pytest.raises(RepositoryError):
        repository.create(
            project.id,
            kind="term",
            name="active CUSTOMER",
            definition="Duplicate definition.",
        )


def test_business_knowledge_validates_kind_and_search_limits(db_connection, project) -> None:
    repository = BusinessKnowledgeRepository(db_connection)
    with pytest.raises(ValueError):
        repository.create(
            project.id,
            kind="secret",
            name="Invalid",
            definition="Invalid kind.",
        )
    with pytest.raises(ValueError):
        repository.search(project.id, "")
    with pytest.raises(ValueError):
        repository.search(project.id, "profit", limit=21)
