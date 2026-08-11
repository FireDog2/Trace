from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    python_timeout_seconds: int = 30
    duckdb_timeout_seconds: int = 30
    max_tool_actions: int = 40
    max_tool_attempts: int = 2
    max_teammate_turns: int = 12
    teammate_timeout_seconds: int = 120
    max_active_teammates: int = 3
    model_timeout_seconds: int = 60
    model_max_retries: int = 2
    context_max_tokens: int = 32_000
    context_target_tokens: int = 24_000
    context_recent_messages: int = 12
    context_max_tool_result_chars: int = 24_000
    postgres_connect_timeout_seconds: int = 5
    postgres_statement_timeout_ms: int = 8_000
    postgres_lock_timeout_ms: int = 1_000
    postgres_max_rows: int = 1_000
    postgres_max_result_bytes: int = 2_000_000
    postgres_max_queries_per_task: int = 8
    postgres_high_cost: float = 100_000.0
    postgres_extreme_cost: float = 1_000_000.0

    def __post_init__(self) -> None:
        if not 1 <= self.duckdb_timeout_seconds <= 600:
            raise ValueError("duckdb_timeout_seconds must be between 1 and 600")
        if not 1 <= self.max_tool_attempts <= 10:
            raise ValueError("max_tool_attempts must be between 1 and 10")
        if not 1 <= self.postgres_max_rows <= 10_000:
            raise ValueError("postgres_max_rows must be between 1 and 10000")
        if not 1 <= self.postgres_connect_timeout_seconds <= 60:
            raise ValueError("postgres_connect_timeout_seconds must be between 1 and 60")
        if not 1 <= self.max_teammate_turns <= 100:
            raise ValueError("max_teammate_turns must be between 1 and 100")
        if not 1 <= self.teammate_timeout_seconds <= 3_600:
            raise ValueError("teammate_timeout_seconds must be between 1 and 3600")
        if not 1 <= self.max_active_teammates <= 3:
            raise ValueError("max_active_teammates must be between 1 and 3")
        if not 0 <= self.model_max_retries <= 5:
            raise ValueError("model_max_retries must be between 0 and 5")
        if not 1_024 <= self.context_target_tokens <= self.context_max_tokens:
            raise ValueError("context_target_tokens must be between 1024 and context_max_tokens")
        if not 2 <= self.context_recent_messages <= 100:
            raise ValueError("context_recent_messages must be between 2 and 100")
        if not 1_000 <= self.context_max_tool_result_chars <= 1_000_000:
            raise ValueError("context_max_tool_result_chars must be between 1000 and 1000000")

    @classmethod
    def local(cls, data_dir: Path | None = None) -> "Settings":
        root = (data_dir or Path.cwd() / ".data-analysis-agent").resolve()
        return cls(
            data_dir=root,
            database_path=root / "metadata.sqlite3",
        )
