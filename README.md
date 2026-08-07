# Governed Data Analysis Agent

An interview portfolio for Agent Engineer roles: a local workbench where a lead agent delegates bounded analysis work, queries governed PostgreSQL sources, produces evidence-linked reports, and leaves a replayable trace.

> Screenshot/GIF slot: add a real recording here after the UI demo is captured.

| Governed PostgreSQL | Bounded agent team | Replayable trace | Reviewed skill candidate |
| --- | --- | --- | --- |
| AST validation, policy checks, EXPLAIN heuristics and one-time approval | Lead may coordinate at most three role-scoped teammates | One correlated redacted timeline spans lead, teammates, SQL, approvals, retries and artifacts | An accepted run spawns a zero-tool Curator; candidates remain disabled |

```mermaid
flowchart LR
  UI[React workbench] --> API[FastAPI lead agent]
  API --> Team[Knowledge / SQL / Report teammates]
  API --> Curator[Zero-tool Curator agent]
  API --> Tools[Permissioned tools]
  Tools --> SQL[PostgreSQL safety gateway]
  SQL --> PG[(Read-only PostgreSQL)]
  API --> Meta[(SQLite metadata + traces)]
  Tools --> Artifacts[Evidence-linked artifacts]
```

## 30-second demo

1. Start the API and web app below.
2. Create a PostgreSQL source and mark `manager_email` as approval-required.
3. Ask the question in [examples/postgres-business-review/question.md](examples/postgres-business-review/question.md).
4. Inspect the approval, a safe `query_timeout` retry, the three teammate records, report evidence, and the correlated trace.
5. Accept the completed run and inspect the durable Curator agent plus its disabled Skill Candidate.
6. Open the checked-in [golden replay](examples/traces/golden-run.json). It is deterministic and redacted.

The SQL path is: parse/allow-list -> schema and policy evaluation -> bounded `EXPLAIN` heuristic -> one-time approval where required -> read-only execution with timeout/result limits -> evidence artifact and trace. DuckDB also owns a watchdog deadline; model/provider and retryable tool failures use bounded retry state and explicit fallback metadata.

## Quick start

Requires Python 3.12, Node 20, and Docker Desktop. Only PostgreSQL runs in Docker;
the API and web app stay on the host for fast edit/run development.

```powershell
# Start the database once. The demo fixture is loaded on first volume creation.
docker compose up -d postgres
$env:PORTFOLIO_POSTGRES_DSN = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
# Use this DSN when creating the PostgreSQL source in the workbench.

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
npm --prefix web ci
data-analysis-agent serve --host 127.0.0.1 --port 8765
# in another terminal
npm --prefix web run dev
```

Code changes do not require rebuilding or restarting the database container. Use
`docker compose stop postgres` to stop it. To discard all local database data and
reload the fixture, run `docker compose down -v` followed by
`docker compose up -d postgres`.

The checked-in `portfolio` password is for local disposable development only. The
deterministic tests require no model API key or PostgreSQL. For live PostgreSQL
checks, point `TEST_POSTGRES_DSN` at the local container; never commit a real DSN.

## Verification

```powershell
python -m pytest tests/test_portfolio_acceptance.py -v
$env:TEST_POSTGRES_DSN = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
python -m pytest tests/test_postgres_integration.py -v
python -m pytest -v
npm --prefix web test -- --run
npm --prefix web run build
python scripts/export_golden_trace.py
```

## Security boundaries and known limits

- Python execution is trusted local execution, not a hostile-code sandbox.
- PostgreSQL access is designed for a read-only account; only PostgreSQL is supported.
- EXPLAIN cost is a heuristic, not a performance guarantee.
- Teammates are bounded to three and have role-specific tool allowlists; they are not autonomous long-running workers.
- Trace writes are best-effort and never replace the original runtime failure; true PostgreSQL integration still needs an explicit DSN.
- Curator reads redacted trace summaries only, has no tools, and is created idempotently only after human acceptance. Candidate approval still leaves it disabled; this is not autonomous self-improvement.

Read [architecture](docs/architecture.md), [SQL safety](docs/sql-safety.md), [multi-agent runtime](docs/multi-agent-runtime.md), [trace and skill](docs/trace-and-skill.md), [security](SECURITY.md), and [contributing](CONTRIBUTING.md) for implementation detail.
