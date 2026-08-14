"""Validate the checked-in, deliberately stable golden trace.

The normal demo is API-key-free and intentionally does not need a local PostgreSQL
server. A supplied PORTFOLIO_POSTGRES_DSN is used only by opt-in live checks.
"""

from __future__ import annotations

import json
from pathlib import Path

from data_analysis_agent.tracing import redact_payload

OUTPUT = Path(__file__).parents[1] / "examples" / "traces" / "golden-run.json"


def main() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    if redact_payload(payload) != payload:
        raise SystemExit("golden trace contains a value that would be redacted")
    if [item["sequence"] for item in payload] != list(range(1, len(payload) + 1)):
        raise SystemExit("golden trace sequence must be stable and contiguous")
    print(f"validated {OUTPUT}")


if __name__ == "__main__":
    main()
