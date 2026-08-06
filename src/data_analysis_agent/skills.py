from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from data_analysis_agent.db import sqlite_connection_guarded, sqlite_connection_lock, utc_now
from data_analysis_agent.providers import ModelMessage, ModelProvider, ModelRequest
from data_analysis_agent.tracing import TraceRepository

class SkillEligibilityError(ValueError): pass
class InvalidSkillCandidate(ValueError): pass

@dataclass(frozen=True, slots=True)
class SkillCandidate:
    id: str; source_run_id: str; name: str; version: int; markdown: str; status: str; enabled: bool; generated_by_model: str; reviewed_at: str | None; created_at: str

class SkillCandidateRepository:
    def __init__(self, connection: sqlite3.Connection) -> None: connection.row_factory = sqlite3.Row; self._connection = connection; self._lock = sqlite_connection_lock(connection)
    @sqlite_connection_guarded
    def get(self, candidate_id: str) -> SkillCandidate:
        row = self._connection.execute("SELECT * FROM skill_candidates WHERE id=?", (candidate_id,)).fetchone()
        if row is None: raise ValueError("Skill candidate not found")
        return _candidate(row)
    @sqlite_connection_guarded
    def create(self, run_id: str, name: str, markdown: str, model: str) -> SkillCandidate:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            version = int(self._connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM skill_candidates WHERE name=?", (name,)).fetchone()[0])
            candidate = SkillCandidate(str(uuid4()),run_id,name,version,markdown,"candidate",False,model,None,utc_now())
            self._connection.execute("INSERT INTO skill_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", (candidate.id,candidate.source_run_id,candidate.name,candidate.version,candidate.markdown,candidate.status,0,candidate.generated_by_model,None,candidate.created_at)); self._connection.commit(); return candidate
        except Exception: self._connection.rollback(); raise
    @sqlite_connection_guarded
    def decide(self, candidate_id: str, approved: bool) -> SkillCandidate:
        candidate = self.get(candidate_id)
        if candidate.status != "candidate": raise ValueError("Skill candidate already decided")
        self._connection.execute("UPDATE skill_candidates SET status=?, reviewed_at=?, enabled=0 WHERE id=?", ("approved" if approved else "rejected",utc_now(),candidate_id)); self._connection.commit(); return self.get(candidate_id)

class SkillStaticValidator:
    REQUIRED = ("When to use", "Inputs", "Workflow", "Safety gates", "Failure recovery")
    def validate(self, markdown: str) -> None:
        lower = markdown.lower()
        if not markdown.startswith("---\nname:") or any(section.lower() not in lower for section in self.REQUIRED): raise InvalidSkillCandidate("Candidate is incomplete")
        if any(word in lower for word in ("todo", "tbd", "password", "api_key", "postgresql://", "bearer ", "bypass approval", "disable approval")): raise InvalidSkillCandidate("Candidate contains unsafe content")

class WorkflowCurator:
    def __init__(self, traces: TraceRepository, candidates: SkillCandidateRepository, provider: ModelProvider) -> None: self.traces=traces; self.candidates=candidates; self.provider=provider; self.validator=SkillStaticValidator()
    def create_candidate(self, run_id: str, model_name: str) -> SkillCandidate:
        run = self.traces.get_run(run_id); events = self.traces.events_for_run(run_id)
        if run.status != "completed" or not run.accepted: raise SkillEligibilityError("Run must be completed and accepted")
        if any(event.status == "failed" for event in events) or not any(event.event_type == "tool.completed" and event.status == "completed" for event in events): raise SkillEligibilityError("Run lacks successful trace evidence")
        summaries = "\n".join(f"- {event.event_type}: {event.status}" for event in events[:50])
        request = ModelRequest(system="Create a safe reusable workflow skill from these redacted event summaries only.", objective="safe SQL diagnostic", messages=(ModelMessage("user", summaries),), tools=())
        markdown = self.provider.complete(request).text.strip(); self.validator.validate(markdown)
        return self.candidates.create(run_id, "safe-sql-diagnostic", markdown, model_name)

def _candidate(row: sqlite3.Row) -> SkillCandidate: return SkillCandidate(row["id"],row["source_run_id"],row["name"],row["version"],row["markdown"],row["status"],bool(row["enabled"]),row["generated_by_model"],row["reviewed_at"],row["created_at"])
