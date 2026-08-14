from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from data_analysis_agent.db import (
    sqlite_connection_guarded,
    sqlite_connection_lock,
    utc_now,
)
from data_analysis_agent.repositories import RecordNotFoundError, RepositoryError

KNOWLEDGE_KINDS = frozenset({"metric", "term", "rule"})


@dataclass(frozen=True, slots=True)
class BusinessKnowledgeEntry:
    id: str
    project_id: str
    kind: str
    name: str
    definition: str
    formula: str | None
    dimensions: tuple[str, ...]
    synonyms: tuple[str, ...]
    source: str | None
    version: int
    created_at: str
    updated_at: str


class BusinessKnowledgeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = sqlite_connection_lock(connection)

    @sqlite_connection_guarded
    def create(
        self,
        project_id: str,
        *,
        kind: str,
        name: str,
        definition: str,
        formula: str | None = None,
        dimensions: tuple[str, ...] = (),
        synonyms: tuple[str, ...] = (),
        source: str | None = None,
    ) -> BusinessKnowledgeEntry:
        values = _normalize(kind, name, definition, formula, dimensions, synonyms, source)
        now = utc_now()
        entry_id = str(uuid4())
        try:
            self._connection.execute(
                "INSERT INTO business_knowledge_entries("
                "id,project_id,kind,name,definition,formula,dimensions_json,"
                "synonyms_json,source,version,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                (
                    entry_id,
                    project_id,
                    *values[:4],
                    json.dumps(values[4], ensure_ascii=False),
                    json.dumps(values[5], ensure_ascii=False),
                    values[6],
                    now,
                    now,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise RepositoryError("Knowledge name already exists in this project") from exc
        return self.get(entry_id)

    @sqlite_connection_guarded
    def get(self, entry_id: str) -> BusinessKnowledgeEntry:
        row = self._connection.execute(
            "SELECT * FROM business_knowledge_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError("Business knowledge entry not found")
        return _entry(row)

    @sqlite_connection_guarded
    def list_for_project(self, project_id: str) -> tuple[BusinessKnowledgeEntry, ...]:
        rows = self._connection.execute(
            "SELECT * FROM business_knowledge_entries WHERE project_id=? "
            "ORDER BY kind,name COLLATE NOCASE",
            (project_id,),
        ).fetchall()
        return tuple(_entry(row) for row in rows)

    @sqlite_connection_guarded
    def list_all(self) -> tuple[BusinessKnowledgeEntry, ...]:
        rows = self._connection.execute(
            "SELECT * FROM business_knowledge_entries ORDER BY kind,name COLLATE NOCASE,updated_at"
        ).fetchall()
        return tuple(_entry(row) for row in rows)

    @sqlite_connection_guarded
    def update(
        self,
        entry_id: str,
        *,
        kind: str,
        name: str,
        definition: str,
        formula: str | None = None,
        dimensions: tuple[str, ...] = (),
        synonyms: tuple[str, ...] = (),
        source: str | None = None,
    ) -> BusinessKnowledgeEntry:
        self.get(entry_id)
        values = _normalize(kind, name, definition, formula, dimensions, synonyms, source)
        try:
            self._connection.execute(
                "UPDATE business_knowledge_entries SET kind=?,name=?,definition=?,"
                "formula=?,dimensions_json=?,synonyms_json=?,source=?,"
                "version=version+1,updated_at=? WHERE id=?",
                (
                    *values[:4],
                    json.dumps(values[4], ensure_ascii=False),
                    json.dumps(values[5], ensure_ascii=False),
                    values[6],
                    utc_now(),
                    entry_id,
                ),
            )
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise RepositoryError("Knowledge name already exists in this project") from exc
        return self.get(entry_id)

    @sqlite_connection_guarded
    def delete(self, entry_id: str) -> None:
        cursor = self._connection.execute(
            "DELETE FROM business_knowledge_entries WHERE id=?", (entry_id,)
        )
        self._connection.commit()
        if cursor.rowcount == 0:
            raise RecordNotFoundError("Business knowledge entry not found")

    def search(
        self, project_id: str, query: str, *, limit: int = 8
    ) -> tuple[BusinessKnowledgeEntry, ...]:
        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("Knowledge search query must not be empty")
        if not 1 <= limit <= 20:
            raise ValueError("Knowledge search limit must be between 1 and 20")
        terms = tuple(dict.fromkeys(re.findall(r"[\w\u4e00-\u9fff]+", normalized)))
        ranked: list[tuple[int, str, BusinessKnowledgeEntry]] = []
        for entry in self.list_for_project(project_id):
            name = entry.name.casefold()
            synonyms = tuple(value.casefold() for value in entry.synonyms)
            fields = " ".join(
                value
                for value in (
                    name,
                    entry.definition.casefold(),
                    (entry.formula or "").casefold(),
                    " ".join(value.casefold() for value in entry.dimensions),
                    " ".join(synonyms),
                    (entry.source or "").casefold(),
                )
                if value
            )
            score = 100 if normalized == name else 90 if normalized in synonyms else 0
            score += 40 if normalized in name else 0
            score += sum(8 for term in terms if term in fields)
            if score:
                ranked.append((score, entry.name.casefold(), entry))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in ranked[:limit])

    def search_global(self, query: str, *, limit: int = 8) -> tuple[BusinessKnowledgeEntry, ...]:
        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("Knowledge search query must not be empty")
        if not 1 <= limit <= 20:
            raise ValueError("Knowledge search limit must be between 1 and 20")
        terms = tuple(dict.fromkeys(re.findall(r"[\w\u4e00-\u9fff]+", normalized)))
        ranked: list[tuple[int, str, BusinessKnowledgeEntry]] = []
        for entry in self.list_all():
            name = entry.name.casefold()
            synonyms = tuple(value.casefold() for value in entry.synonyms)
            fields = " ".join(
                value
                for value in (
                    name,
                    entry.definition.casefold(),
                    (entry.formula or "").casefold(),
                    " ".join(value.casefold() for value in entry.dimensions),
                    " ".join(synonyms),
                    (entry.source or "").casefold(),
                )
                if value
            )
            score = 100 if normalized == name else 90 if normalized in synonyms else 0
            score += 40 if normalized in name else 0
            score += sum(8 for term in terms if term in fields)
            if score:
                ranked.append((score, entry.name.casefold(), entry))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in ranked[:limit])


def _normalize(
    kind: str,
    name: str,
    definition: str,
    formula: str | None,
    dimensions: tuple[str, ...],
    synonyms: tuple[str, ...],
    source: str | None,
) -> tuple[str, str, str, str | None, tuple[str, ...], tuple[str, ...], str | None]:
    normalized_kind = kind.strip().lower()
    if normalized_kind not in KNOWLEDGE_KINDS:
        raise ValueError("Knowledge kind must be metric, term, or rule")
    normalized_name = name.strip()
    normalized_definition = definition.strip()
    if not normalized_name or not normalized_definition:
        raise ValueError("Knowledge name and definition are required")
    normalized_formula = formula.strip() if formula and formula.strip() else None
    normalized_dimensions = _unique_values(dimensions)
    normalized_synonyms = _unique_values(synonyms)
    normalized_source = source.strip() if source and source.strip() else None
    return (
        normalized_kind,
        normalized_name,
        normalized_definition,
        normalized_formula,
        normalized_dimensions,
        normalized_synonyms,
        normalized_source,
    )


def _unique_values(values: tuple[str, ...]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            output.append(normalized)
            seen.add(key)
    return tuple(output)


def _entry(row: sqlite3.Row) -> BusinessKnowledgeEntry:
    return BusinessKnowledgeEntry(
        id=row["id"],
        project_id=row["project_id"],
        kind=row["kind"],
        name=row["name"],
        definition=row["definition"],
        formula=row["formula"],
        dimensions=tuple(json.loads(row["dimensions_json"])),
        synonyms=tuple(json.loads(row["synonyms_json"])),
        source=row["source"],
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
