# Agent Engineer Portfolio Upgrade Design

**Date:** 2026-08-06
**Status:** Approved for implementation planning

## 1. Objective

Upgrade the existing local-first data analysis MVP into a focused portfolio project for Agent Engineer recruiting. The project must demonstrate that the author can build an agent harness with controlled tools, multi-agent coordination, human approval, durable traces, failure recovery, and reusable workflow knowledge.

The two-week version is a high-quality vertical slice, not a production-complete analytics platform. Its primary reviewer is an interviewer browsing the public GitHub repository. The repository therefore treats architecture, tests, security boundaries, example traces, and a reproducible demo as first-class deliverables.

The product statement is:

> A local-first, auditable enterprise data analysis workbench where a lead agent coordinates specialized teammates and every database query passes through an explainable SQL safety gateway.

## 2. Golden Demonstration

The repository must present one coherent scenario:

1. The user configures a PostgreSQL data source.
2. The user marks tables and fields as allowed, approval-required, or denied.
3. The user asks the lead agent to diagnose a regional profit decline and produce a management report.
4. The lead creates focused work for a knowledge agent, SQL agent, and report agent.
5. Every proposed query passes through parsing, AST validation, policy evaluation, `EXPLAIN`, risk classification, and either approval or execution.
6. A restricted field or high-risk plan pauses the relevant work and creates a one-time approval request.
7. A timed-out query returns a structured retryable error. The SQL agent rewrites it within a fixed retry budget.
8. The workbench displays each agent's status, task, tool activity, and artifacts.
9. The lead publishes an evidence-linked report.
10. A workflow curator reads the successful structured trace and produces a disabled candidate skill for human review.

The deterministic end-to-end demonstration uses a fake model and fixture database so it can run in CI without an API key. Real models are an optional manual demonstration path.

## 3. Scope

### 3.1 Included

- One PostgreSQL connector.
- Locally encrypted database credentials.
- Read-only database sessions and a read-only database account assumption.
- Table- and field-level policies with one-time approvals.
- AST-based SQL validation and PostgreSQL `EXPLAIN (FORMAT JSON)` risk checks.
- At most three active teammate agents.
- A durable task board and typed agent message bus.
- Structured events, trace retrieval, and offline trace replay.
- A single candidate skill flow for safe SQL diagnostic analysis.
- Deterministic tests, an example trace, and interview-oriented repository documentation.

### 3.2 Excluded

- Additional database engines.
- Row-level security policy authoring.
- Database writes, arbitrary shell access, or model-controlled networking.
- Distributed workers, multi-node scheduling, or general-purpose agent swarms.
- Enterprise identity, multi-tenancy, and centralized secrets management.
- Automatically enabling model-generated skills.
- A general self-improving workflow platform.

## 4. Architecture

```text
React workbench
  - data source and policy configuration
  - agent team status
  - approval center
  - trace replay
          |
       HTTP/SSE
          |
FastAPI application
  - project/task APIs
  - data source/policy APIs
  - approval APIs
  - trace APIs
          |
Agent runtime
  - lead loop
  - teammate manager
  - task board
  - message bus
  - tool dispatcher
          |
  +-------+---------+
  |       |         |
knowledge SQL     report
agent     agent   agent
            |
      SQL safety gateway
  - parse and validate AST
  - resolve table/field access
  - explain and classify risk
  - request approval
  - execute with limits
            |
        PostgreSQL
```

The existing project, task, artifact, approval, and event concepts remain authoritative. New database, agent-run, and trace entities extend them rather than creating a second persistence model.

## 5. Agent Runtime

### 5.1 Lead agent

The lead is the only user-facing agent. It maintains the analysis objective and plan, creates bounded teammate tasks, handles dependencies and failures, interprets structured handoffs, and publishes the final result.

The lead cannot execute database statements directly. Database work must be delegated to the SQL agent and pass through the SQL safety gateway.

### 5.2 Teammates

The teammate model adapts the persistent teammate idea demonstrated by `lean-cc`:

- each teammate owns an independent model context;
- teammates share durable project metadata, the task board, artifact references, and typed messages rather than a mutable conversation array;
- each teammate receives a role-specific prompt and tool allowlist;
- teammates cannot spawn additional agents;
- each run has a maximum model-turn count, wall-clock timeout, retry budget, and cancellation signal;
- final results return through structured handoff records rather than copied transcripts.

Lifecycle states are `created`, `working`, `waiting`, `completed`, `failed`, and `cancelled`.

The initial roles are:

- **Knowledge agent:** reads project knowledge and schema descriptions, resolves metric definitions, and reports missing definitions.
- **SQL agent:** sees only policy-filtered schema, proposes SQL, consumes safety diagnostics, and registers query outputs.
- **Report agent:** reads registered artifacts and produces charts and evidence-linked reports. It cannot access database credentials.

The lead decides which roles are needed. The roles are capability boundaries, not a hard-coded business pipeline.

### 5.3 Task board and message bus

The task board persists objective, owner, dependencies, state, progress summary, inputs, outputs, retry count, and terminal error.

The message bus supports `task_assignment`, `progress_update`, `result`, `error`, `approval_required`, and `cancel`. Every message carries `run_id`, `agent_id`, `task_id`, `message_id`, and a timestamp.

## 6. SQL Safety Gateway

No agent or general tool receives a raw database connection. All queries use one gateway:

```text
SQL text
  -> single-statement check
  -> PostgreSQL AST parse
  -> statement and function validation
  -> table/field resolution
  -> policy evaluation
  -> defensive limit application
  -> EXPLAIN (FORMAT JSON)
  -> risk classification
  -> approval or execution
  -> timeout and output limits
  -> redaction and artifact registration
```

### 6.1 AST policy

Use `sqlglot` with the PostgreSQL dialect. Allow `SELECT`, CTEs, joins, aggregates, window functions, and safe subqueries. Reject multiple statements, writes, DDL, `COPY`, `CALL`, `SET`, unapproved external/file access, unsafe functions, and any statement that cannot be parsed or resolved confidently.

`SELECT *` must be expanded against the known schema before field policy evaluation. If expansion is not reliable, reject the query and ask the SQL agent to use explicit fields.

### 6.2 Access policy and approvals

Each table and field is classified as:

- `allowed`: automatic read access;
- `approval_required`: one-time human approval;
- `denied`: never exposed or executed.

An approval is bound to the normalized SQL hash, data source identifier, and policy version. It is consumed once. Any SQL or policy change invalidates it.

### 6.3 Explain risk

The gateway uses `EXPLAIN (FORMAT JSON)`, never automatic `EXPLAIN ANALYZE`. Risk signals include total cost, estimated rows, large sequential scans, join count, missing filters, and restricted resources.

Risk classes are:

- low: automatic execution;
- medium: shorter timeout and smaller result cap;
- high: human approval required;
- extreme: reject and request a rewritten query.

Thresholds are configurable and documented as heuristics. PostgreSQL cost is not presented as elapsed time.

### 6.4 Defense in depth

Execution additionally enforces a read-only database account, `default_transaction_read_only`, `statement_timeout`, `lock_timeout`, maximum returned rows and bytes, connection-pool limits, and a per-task query budget. AST validation is not the only protection.

## 7. Failure Recovery

All tools return a common structured result with status, category, safe message, retryability, attempt, maximum attempts, redacted diagnostics, and relevant artifact identifiers.

SQL recovery is bounded:

1. A syntax, plan, or timeout diagnostic is returned to the SQL agent.
2. The agent may rewrite once.
3. A second failure may trigger a smaller, staged query if the remaining budget allows it.
4. When the retry budget is exhausted, automatic execution stops.
5. The lead reports completed work, the terminal reason, retained artifacts, and permitted recovery choices.

Permission denial, approval rejection, parse failure, connection failure, query timeout, result overflow, model failure, and teammate timeout are distinct categories. The runtime must not raise limits automatically to bypass approval or policy.

## 8. Structured Traces

The trace hierarchy is `Project -> Task -> Run -> Agent turn / Model call / Tool call / Approval / Artifact`.

Each event contains:

- event, run, task, agent, and optional parent identifiers;
- event type and status;
- start and finish timestamps plus duration;
- attempt number;
- redacted input and output summaries;
- error category;
- related artifact identifiers.

Events cover agent lifecycle, model calls and token usage, tool requests and policy decisions, SQL validation and risk results, approvals, agent messages, plan revisions, artifacts, and skill candidate review.

Trace replay reads persisted events and never re-executes a model call or database query.

Credentials, API keys, full restricted results, unbounded model context, and sensitive exception details must not enter ordinary logs. Protected query results remain permission-controlled artifacts; trace events store hashes, metadata, counts, and redacted summaries.

## 9. Workflow Curator

The workflow curator consumes structured traces rather than raw chat transcripts. It accepts only runs that completed successfully, have valid evidence references, contain no unresolved policy or execution error, were explicitly marked acceptable, and have complete trace structure.

For the initial safe-SQL-diagnostic skill, the curator:

1. identifies stable steps, tool ordering, and recovery behavior;
2. removes database names, table names, business constants, credentials, and customer data;
3. generates a candidate `SKILL.md`;
4. performs static checks for placeholders, permission expansion, required inputs, and forbidden content;
5. presents a diff for human review;
6. saves an approved version with source run, model, reviewer, and status metadata.

Generated skills are disabled by default and versioned for audit and rollback.

## 10. Testing Strategy

Unit tests cover AST rejection and resolution, `SELECT *` expansion, policy precedence, approval binding and consumption, explain risk, timeout/retry transitions, teammate lifecycle and tool allowlists, trace correlation and redaction, and curator eligibility checks.

Integration tests use a disposable PostgreSQL database to verify read-only execution, restricted-field approval, high-risk plans, statement timeouts, approval invalidation after SQL changes, lead/teammate handoff, and trace persistence across application restarts.

The deterministic acceptance scenario uses a fake model to demonstrate delegation, approval, timeout, retry, evidence-linked reporting, offline replay, and candidate skill generation without an API key.

Real-model evaluations are optional and separate from CI.

## 11. Repository Presentation

The README must communicate the project in about 30 seconds through a concise product statement, a short demonstration, architecture and SQL-safety diagrams, multi-agent and trace screenshots, one representative trace, quick-start commands, test commands, and explicit limitations.

Supporting material should include:

- `docs/architecture.md`
- `docs/sql-safety.md`
- `docs/multi-agent-runtime.md`
- `docs/trace-and-skill.md`
- `examples/postgres-business-review/`
- `examples/traces/golden-run.json`
- `SECURITY.md`

Commit history should expose coherent vertical capabilities instead of one bulk implementation commit.

## 12. Delivery Budget and Priority

The available budget is approximately 42 hours over 14 days:

- SQL source, gateway, and approvals: 13 hours;
- multi-agent coordination: 9 hours;
- structured traces and replay: 6 hours;
- candidate skill generation: 3 hours;
- UI integration: 4 hours;
- tests, documentation, and demonstration material: 7 hours.

The implementation order is:

1. persistence changes and PostgreSQL test fixture;
2. SQL AST policy, explain risk, read-only execution, and approvals;
3. lead, task board, message bus, and teammate lifecycle;
4. events, trace API, and replay UI;
5. candidate skill generation and review;
6. deterministic golden-path acceptance test;
7. security tests, CI, README, screenshots, and demonstration recording.

If the schedule slips, remove the skill-review UI and nonessential animation first. Preserve the SQL safety gateway, visible multi-agent state, durable traces, deterministic acceptance test, and repository documentation because these provide the strongest Agent Engineer evidence.

## 13. Acceptance Criteria

The portfolio upgrade is complete when:

1. a reviewer can understand the architecture, safety boundaries, and demonstration from the repository without an API key;
2. the application can connect to the fixture PostgreSQL database with a read-only account;
3. table and field policies deterministically allow, pause, or deny queries;
4. all executable SQL passes AST validation and explain risk evaluation;
5. approvals are one-time and bound to the exact normalized query and policy version;
6. timeouts and retry exhaustion produce structured, replayable outcomes;
7. the lead can run bounded specialized teammates and display their state;
8. the final report references persisted query and visualization artifacts;
9. a complete run can be replayed offline from structured events;
10. one accepted run can produce a disabled, reviewable safe-SQL skill candidate;
11. deterministic tests cover the golden scenario and critical safety boundaries;
12. documentation states what is heuristic, trusted, unsupported, and intentionally out of scope.
