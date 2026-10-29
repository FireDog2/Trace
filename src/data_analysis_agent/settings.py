from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    python_timeout_seconds: int = 30
    python_backend: str = "docker"
    python_image: str = "trace-python:1"
    python_memory_mb: int = 1024
    python_cpus: float = 1.0
    python_pids_limit: int = 64
    python_workspace_mb: int = 256
    python_output_limit_bytes: int = 65_536
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
    local_model_path: Path | None = None
    local_model_device: str = "auto"
    local_model_dtype: str = "auto"
    local_model_max_new_tokens: int = 2_048

    def __post_init__(self) -> None:
        if self.python_backend not in {"docker", "trusted_local"}:
            raise ValueError("python_backend must be docker or trusted_local")
        if not 1 <= self.python_timeout_seconds <= 600:
            raise ValueError("python_timeout_seconds must be between 1 and 600")
        if not 128 <= self.python_memory_mb <= 16_384:
            raise ValueError("python_memory_mb must be between 128 and 16384")
        if not 0.1 <= self.python_cpus <= 16:
            raise ValueError("python_cpus must be between 0.1 and 16")
        if not 16 <= self.python_pids_limit <= 512:
            raise ValueError("python_pids_limit must be between 16 and 512")
        if not 16 <= self.python_workspace_mb <= 1024:
            raise ValueError("python_workspace_mb must be between 16 and 1024")
        if not 1024 <= self.python_output_limit_bytes <= 1_048_576:
            raise ValueError("python_output_limit_bytes must be between 1024 and 1048576")
        if not self.python_image or self.python_image.startswith("-"):
            raise ValueError("python_image must be a nonempty image reference")
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
        if not 1 <= self.local_model_max_new_tokens <= 16_384:
            raise ValueError("local_model_max_new_tokens must be between 1 and 16384")

    @classmethod
    def local(cls, data_dir: Path | None = None) -> "Settings":
        root = (data_dir or Path.cwd() / ".data-analysis-agent").resolve()
        model_path = os.environ.get("TRACE_LOCAL_MODEL_PATH", "").strip()
        return cls(
            data_dir=root,
            database_path=root / "metadata.sqlite3",
            python_backend=os.environ.get("TRACE_PYTHON_BACKEND", "docker").strip(),
            python_image=os.environ.get("TRACE_PYTHON_IMAGE", "trace-python:1").strip(),
            python_timeout_seconds=int(os.environ.get("TRACE_PYTHON_TIMEOUT_SECONDS", "30")),
            python_memory_mb=int(os.environ.get("TRACE_PYTHON_MEMORY_MB", "1024")),
            python_cpus=float(os.environ.get("TRACE_PYTHON_CPUS", "1")),
            python_pids_limit=int(os.environ.get("TRACE_PYTHON_PIDS_LIMIT", "64")),
            python_workspace_mb=int(os.environ.get("TRACE_PYTHON_WORKSPACE_MB", "256")),
            python_output_limit_bytes=int(
                os.environ.get("TRACE_PYTHON_OUTPUT_LIMIT_BYTES", "65536")
            ),
            local_model_path=Path(model_path).expanduser().resolve() if model_path else None,
            local_model_device=os.environ.get("TRACE_LOCAL_MODEL_DEVICE", "auto").strip() or "auto",
            local_model_dtype=os.environ.get("TRACE_LOCAL_MODEL_DTYPE", "auto").strip() or "auto",
            local_model_max_new_tokens=int(
                os.environ.get("TRACE_LOCAL_MODEL_MAX_NEW_TOKENS", "2048")
            ),
        )
