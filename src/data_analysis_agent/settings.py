from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    python_timeout_seconds: int = 30
    max_tool_actions: int = 40

    @classmethod
    def local(cls, data_dir: Path | None = None) -> "Settings":
        root = (data_dir or Path.cwd() / ".data-analysis-agent").resolve()
        return cls(data_dir=root, database_path=root / "metadata.sqlite3")
