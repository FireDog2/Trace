from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from data_analysis_agent.settings import Settings

PROJECT_DIRECTORIES = (
    "source",
    "derived",
    "code",
    "charts",
    "reports",
    "logs",
    "knowledge",
)


class WorkspaceError(RuntimeError):
    """Base error for invalid workspace operations."""


class WorkspaceBoundaryError(WorkspaceError):
    """Raised when a path escapes its project workspace."""


class SourceFileError(WorkspaceError):
    """Raised when a source file cannot be imported."""


class WorkspaceService:
    def __init__(self, settings: Settings) -> None:
        self._projects_root = settings.data_dir.resolve() / "projects"

    def project_root(self, project_id: str) -> Path:
        return (self._projects_root / project_id).resolve()

    def create_project(self, project_id: str) -> Path:
        root = self.project_root(project_id)
        root.mkdir(parents=True, exist_ok=True)
        for directory in PROJECT_DIRECTORIES:
            (root / directory).mkdir(exist_ok=True)
        return root

    def resolve_inside(self, project_id: str, relative_path: str | Path) -> Path:
        root = self.project_root(project_id)
        candidate_path = Path(relative_path)
        candidate = (
            candidate_path.resolve()
            if candidate_path.is_absolute()
            else (root / candidate_path).resolve()
        )
        if candidate != root and root not in candidate.parents:
            raise WorkspaceBoundaryError(str(relative_path))
        return candidate

    def relative_path(self, project_id: str, path: Path) -> str:
        candidate = self.resolve_inside(project_id, path)
        return candidate.relative_to(self.project_root(project_id)).as_posix()

    def import_source(self, project_id: str, source: Path) -> Path:
        resolved_source = source.resolve()
        if not resolved_source.is_file():
            raise SourceFileError(f"Source file does not exist: {source.name}")
        destination = self.resolve_inside(
            project_id,
            Path("source") / f"{uuid4().hex[:12]}-{resolved_source.name}",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved_source, destination)
        return destination
