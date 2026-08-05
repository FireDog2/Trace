from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    python_timeout_seconds: int = 30
    max_tool_actions: int = 40
    model_base_url: str | None = None
    model_api_key: str | None = None
    model_name: str | None = None
    model_timeout_seconds: int = 60

    @classmethod
    def local(cls, data_dir: Path | None = None) -> "Settings":
        root = (data_dir or Path.cwd() / ".data-analysis-agent").resolve()
        return cls(
            data_dir=root,
            database_path=root / "metadata.sqlite3",
            model_base_url=os.getenv("DAA_MODEL_BASE_URL"),
            model_api_key=os.getenv("DAA_MODEL_API_KEY"),
            model_name=os.getenv("DAA_MODEL_NAME"),
        )
