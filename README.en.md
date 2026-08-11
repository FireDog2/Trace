# Governed Data Analysis Agent

[简体中文](README.md) | **English**

A local-first, evidence-first agent workbench for governed data analysis. A lead
agent can coordinate role-scoped teammates, query local files and read-only
PostgreSQL sources, publish evidence-linked reports, and leave a durable replay of
every important decision.

This repository is an Agent Engineer portfolio project. Its focus is not a chat UI
wrapped around a model. The focus is the **agent harness**: the deterministic runtime
that decides what a model may see, which actions it may request, whether an action is
allowed, how failures are bounded, what state is durable, and how an execution can be
audited later.

> Add a real screenshot or short demo recording here after capturing the workbench.

## What the project demonstrates

| Capability | Design choice | Result |
| --- | --- | --- |
| Governed data access | SQL AST validation, source-policy snapshots, bounded `EXPLAIN`, one-time approvals, read-only execution | The model never receives a raw database connection and cannot bypass query policy |
| Governed context | Versioned prompt sections, provider-neutral budgeting, non-destructive compaction, owner-scoped checkpoints | Long sessions remain bounded without rewriting the durable transcript |
| Bounded delegation | One lead plus Knowledge, SQL, and Report teammates with separate messages, leases, deadlines, turn limits, and tool allowlists | Delegation is observable and restart-safe rather than an unrestricted agent swarm |
| Evidence-first output | Immutable source inputs, registered artifacts, evidence links, Markdown rendering, Markdown/PDF export | Reports can be reviewed against their actual analytical inputs |
| Recovery policy | Categorized provider/tool failures, capped retries, repeated-call detection, explicit fallback metadata | Failures cannot silently create infinite tool or model loops |
| Replay and curation | Correlated traces, redacted replay bundles, human acceptance, zero-tool Curator, disabled Skill Candidates | Successful behavior can be reviewed for reuse without autonomous self-modification |

## Harness architecture

The language model is an untrusted planner inside the system. It proposes messages
and typed tool calls. The harness remains authoritative for state transitions,
permissions, credentials, context projection, tool execution, retries, evidence,
and traces.

![Governed Data Analysis Agent harness architecture](docs/assets/harness-architecture.en.png)

<p align="center"><sub>The model proposes; the harness governs context, permissions, execution, recovery, evidence, and durable state.</sub></p>

### Harness responsibilities

1. **Assemble a role-specific request.** `PromptContext` selects ordered and
   versioned identity, safety, role, recovery, output, and runtime sections. Tool
   schemas stay structured rather than being copied into prose.
2. **Project bounded context.** The complete task or teammate transcript remains
   durable. `ContextManager` creates only the model-visible projection, budgets
   oversized tool results, reuses the latest checkpoint, and compacts an older
   protocol-complete prefix when required.
3. **Ask the provider for a proposal.** Provider credentials are protected locally.
   Timeout, network, rate-limit, and upstream failures receive capped exponential
   retries. A provider context-length rejection receives exactly one forced
   compaction retry.
4. **Validate every requested action.** Pydantic validates tool arguments. The
   registry verifies task/project ownership and the permission policy classifies the
   call as allowed, approval-required, or denied. Unknown tools are denied.
5. **Execute through a narrow adapter.** File analysis, Python, PostgreSQL,
   knowledge lookup, artifact publication, and teammate coordination each pass
   through harness-owned services. The model never invokes infrastructure directly.
6. **Persist evidence and control state.** Messages, task events, plans, tool
   attempts, approval decisions, agent runs, artifacts, evidence links, context
   checkpoints, and trace events are recorded independently of the model context.
7. **Stop predictably.** Total tool actions, identical failed calls, retries,
   teammate count, teammate turns, deadlines, SQL queries, result rows, result bytes,
   and execution time all have explicit bounds.

## End-to-end analysis flow

```mermaid
sequenceDiagram
  actor User
  participant UI as React workbench
  participant API as FastAPI
  participant Lead as Lead harness
  participant KA as Knowledge Agent
  participant SA as SQL Agent
  participant Gate as SQL safety gateway
  participant RA as Report Agent
  participant Store as SQLite + workspace

  User->>UI: Ask a business question
  UI->>API: Create task / send message
  API->>Store: Persist task and immutable message
  API->>Lead: Start bounded run with TraceContext
  Lead->>KA: Spawn role-scoped metric/term lookup
  KA->>Store: Search project Business Knowledge
  KA-->>Lead: Durable structured handoff
  Lead->>SA: Spawn governed evidence query
  SA->>Gate: Typed read-only SQL request
  Gate->>Gate: AST + schema policy + EXPLAIN + budget
  alt restricted field or high plan cost
    Gate-->>UI: Persist one-time approval request
    User->>UI: Approve or reject
  end
  Gate->>Store: Register bounded result artifact and trace
  SA-->>Lead: Handoff with artifact identifiers
  Lead->>RA: Spawn evidence-linked report work
  RA->>Store: Publish Markdown report and evidence links
  RA-->>Lead: Report handoff
  Lead->>Store: Complete task and correlated run
  API-->>UI: Messages, artifacts, team state, approvals, trace
```

The lead is required by its prompt contract to delegate business-definition lookup
to the Knowledge Agent, database evidence to the SQL Agent, and final report/chart
work to the Report Agent. Enforcement is completed by role-specific tool allowlists:
for example, the Knowledge Agent cannot execute SQL and the Curator has no tools at
all.

## Design details

### 1. State machine and durable execution

Tasks move through explicit states such as understanding, planning, executing,
synthesizing, needs-input, completed, failed, and cancelled. Transitions are owned by
repositories and services, not inferred from model prose. Teammates have independent
durable run/message records, worker leases, cancellation state, turn limits, and
deadlines. On service startup, stale active teammate work is marked interrupted so a
process restart does not pretend that abandoned work is still running.

SQLite is the control-plane database. Project workspaces hold uploaded datasets,
derived artifacts, and reports. PostgreSQL remains an external read-only analytical
source. This separation makes lifecycle state transactional while keeping analytical
files inspectable.

### 2. Prompt, context, and memory boundaries

The runtime separates four concepts that are often collapsed into “agent memory”:

| State | Purpose | Mutation rule |
| --- | --- | --- |
| Transcript | Complete task/teammate protocol history | Append-only from the runtime's perspective |
| Context checkpoint | Bounded short-term continuation across compaction | New checkpoint; never replaces source messages |
| Business Knowledge | Accepted project semantics: metrics, terms, rules | Explicitly created/versioned with source metadata |
| Artifact | Durable analytical evidence or report | Registered in the project with provenance/evidence links |

Token counts use a deterministic four-characters-per-token estimate. They are a
portable budgeting signal, not provider billing counts. Replay exports checkpoint
metadata and a summary hash, not the checkpoint text. Full messages remain available
for audit even when the model sees a compacted projection.

### 3. Governed PostgreSQL path

`query_database` is a policy gateway rather than a thin database tool:

```text
typed call
  -> SELECT-only AST allowlist
  -> inspected schema and versioned source policy
  -> per-task query budget
  -> bounded EXPLAIN heuristic
  -> one-time query-hash/policy-version approval when required
  -> read-only transaction + statement/lock timeout
  -> row/byte limits
  -> registered artifact + redacted trace
```

Denied columns never reach PostgreSQL. Restricted fields and high-cost plans pause
for approval; approvals are consumed once. DSNs and credentials are never returned
to the UI or retained in trace payloads. The `EXPLAIN` classifier is intentionally a
risk heuristic, not a guarantee of database performance.

### 4. Tools, permissions, and recovery

Every tool has a typed input model and a harness-owned handler. Project-local,
non-destructive actions can run automatically. Absolute paths, overwrites, network
access, dependency installation, and other boundary-crossing actions require explicit
approval when supported. Source mutation and unknown tools are denied by default.

Tool failures carry safe normalized categories and a `retryable` decision. Retry
state is keyed by the normalized tool-call signature. The model receives remaining
attempts and then an explicit fallback instruction; repeating an identical call does
not reset the budget. Provider retries and tool retries are separate policies.

### 5. Evidence, reporting, traces, and skills

Important report claims cite registered artifact IDs. The workbench renders sanitized
Markdown, and published Markdown reports can be downloaded as source or rendered to
an A4 PDF with Chinese font support, tables, code blocks, links, hierarchy, and page
numbers.

A single `TraceContext` correlates lead, teammate, model, tool, SQL, approval, and
artifact events. Replay is built from persisted redacted events and does not
re-execute the model or a query. After a human accepts a successful run, one
idempotent zero-tool Curator may transform bounded redacted evidence into a workflow
candidate. Static validation runs before storage, and even an approved candidate
remains disabled. This demonstrates reviewable learning without autonomous
self-improvement.

## Source map

| Area | Main implementation |
| --- | --- |
| API composition and lifecycle | `src/data_analysis_agent/api.py` |
| Lead model/tool loop | `src/data_analysis_agent/agent.py` |
| Versioned prompts | `src/data_analysis_agent/prompts.py` |
| Context budgeting and checkpoints | `src/data_analysis_agent/context.py` |
| Typed tools and scoped execution | `src/data_analysis_agent/tools.py` |
| Permission and approval policy | `src/data_analysis_agent/permissions.py` |
| Teammate repository, runner, leases | `src/data_analysis_agent/team.py` |
| SQL AST and PostgreSQL gateway | `src/data_analysis_agent/sql_safety.py`, `postgres.py` |
| Provider adapters and error taxonomy | `src/data_analysis_agent/providers.py` |
| Trace/replay and Curator flow | `src/data_analysis_agent/tracing.py`, `skills.py` |
| Artifact/report/PDF pipeline | `src/data_analysis_agent/reporting.py`, `pdf_export.py` |
| Metadata schema and migrations | `src/data_analysis_agent/db.py` |
| React workbench | `web/src/App.tsx`, `web/src/components/` |

## 30-second demo path

1. Start PostgreSQL, the API, and the web app using the commands below.
2. Create a project and PostgreSQL source; mark `manager_email` as
   approval-required and register a business metric.
3. Ask the question in
   [`examples/postgres-business-review/question.md`](examples/postgres-business-review/question.md).
4. Inspect the approval, a safe `query_timeout` recovery, the three teammate records,
   evidence-linked report, and correlated trace.
5. Export the replay JSON, accept the completed run, and inspect the durable Curator
   plus its disabled Skill Candidate.
6. Compare with the deterministic checked-in
   [`golden-run.json`](examples/traces/golden-run.json).

## Quick start

Requires Python 3.12, Node 20, and Docker Desktop. Only PostgreSQL runs in
Docker; the API and web app stay on the host for a fast edit/run loop.

```powershell
# Start the disposable demo database. Its fixture loads on first volume creation.
docker compose up -d postgres
$env:PORTFOLIO_POSTGRES_DSN = "postgresql://portfolio_reader:portfolio_reader@localhost:5432/portfolio"

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
npm --prefix web ci

# Terminal 1: API
data-analysis-agent serve --host 127.0.0.1 --port 8765

# Terminal 2: web workbench
npm --prefix web run dev
```

Create the PostgreSQL source in the workbench using the reader DSN above. The
checked-in password is only for the local disposable fixture. Never commit a real
DSN or API key.

Code changes do not require rebuilding the database container. Stop it with
`docker compose stop postgres`. To discard demo data and reload the fixture, use
`docker compose down -v`, then `docker compose up -d postgres`.

The deterministic test suite requires neither a model API key nor PostgreSQL. Live
integration tests are opt-in through explicit test DSNs.

## Verification

```powershell
# Deterministic portfolio acceptance contract
python -m pytest tests/test_portfolio_acceptance.py -v

# Complete deterministic backend suite
python -m pytest -v

# Frontend unit tests and production build
npm --prefix web test -- --run
npm --prefix web run build

# Optional live PostgreSQL integration
$env:TEST_POSTGRES_ADMIN_DSN = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
$env:TEST_POSTGRES_DSN = "postgresql://portfolio_reader:portfolio_reader@localhost:5432/portfolio"
python -m pytest tests/test_postgres_integration.py -v

# Regenerate the deterministic replay fixture
python scripts/export_golden_trace.py
```

## Security boundaries and known limits

- Python execution is trusted local execution, not a hostile-code sandbox.
- PostgreSQL access is designed for a dedicated read-only account; only PostgreSQL
  is supported by the governed database path.
- The local workbench is single-user software and is not an internet-facing
  multi-tenant service.
- `EXPLAIN` cost classification reduces risk but cannot guarantee query performance.
- Teammates are bounded workers, not autonomous long-running services.
- Trace writes are best-effort and never replace the original runtime failure.
- Context estimation is provider-neutral and approximate.
- Checkpoint summaries preserve bounded continuity but never replace the durable
  transcript or governed Business Knowledge.
- Skill Candidate approval deliberately does not enable or execute the candidate.

## Further documentation

- [`docs/architecture.md`](docs/architecture.md) — concise component architecture
- [`docs/context-lifecycle.md`](docs/context-lifecycle.md) — prompt, projection,
  checkpoint, and memory design
- [`docs/sql-safety.md`](docs/sql-safety.md) — SQL validation and approval boundary
- [`docs/multi-agent-runtime.md`](docs/multi-agent-runtime.md) — durable bounded team
- [`docs/trace-and-skill.md`](docs/trace-and-skill.md) — trace, replay, acceptance,
  and Curator flow
- [`SECURITY.md`](SECURITY.md) — supported trust boundary
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — local development workflow
- [`docs/portfolio-checklist.zh-CN.md`](docs/portfolio-checklist.zh-CN.md) — Chinese
  acceptance checklist mapped to implementation and tests
