# Data Analysis Agent MVP Design

**Date:** 2026-08-05  
**Status:** Approved for implementation planning

## 1. Product Definition

The product is a local-first analysis project workspace for business analysts and data analysts. Conversation is the primary way users express intent, while analysis plans, executable code, charts, intermediate datasets, evidence, and reports are durable task artifacts.

The MVP focuses on one complete workflow:

1. Create an analysis project.
2. Add one or more CSV or Excel files.
3. Ask an open-ended business question, initially demonstrated through operating-performance diagnosis.
4. Let the agent inspect the available data and propose an analysis plan.
5. Execute low-risk SQL and Python analysis steps automatically.
6. Persist code, intermediate results, charts, execution records, and evidence.
7. Produce a report whose important claims link back to supporting artifacts.
8. Allow the user to continue the analysis through follow-up conversation.

Database connections, cloud collaboration, scheduled work, multi-agent execution, Word or PowerPoint export, and a full notebook editor are outside the MVP.

## 2. Product Principles

### 2.1 The project is larger than the conversation

An analysis project is the durable container. It owns datasets, business knowledge, analysis tasks, and published artifacts. A project may contain multiple tasks, and a task may contain multiple conversations. Deleting or starting a conversation must not discard the project's validated knowledge or task artifacts.

### 2.2 Conversation controls intent; the workbench records work

Conversation is used to ask questions, refine goals, correct metric definitions, approve risky operations, and interpret results. The task workbench displays the current objective, plan, execution timeline, code, data outputs, charts, report, and errors.

### 2.3 Evidence is more important than prose

Every important report claim must reference at least one persisted supporting artifact. The system records which input datasets and executable steps produced each derived artifact.

### 2.4 The model plans dynamically

The analysis is not a hard-coded sales-analysis pipeline. The model may update its plan after observing data or tool results. The harness provides tools, context, persistence, and permission boundaries without replacing the model's judgment with a rigid workflow graph.

## 3. Core Domain Model

### 3.1 Project

A project owns:

- source datasets and derived datasets;
- business knowledge, including metric definitions and user corrections;
- analysis tasks;
- project-level artifacts and metadata.

### 3.2 Task

A task represents one analysis objective. It owns:

- the current objective and status;
- one active, revisable analysis plan;
- messages associated with the task;
- tool executions and their outcomes;
- generated artifacts;
- the final report and its evidence references.

Task states are:

`understanding -> planning -> running -> synthesizing -> completed`

From an active state, a task may also enter `needs_input`, `failed`, or `cancelled`. A recoverable failed task may return to `running` after an explicit retry.

### 3.3 Plan and step

A plan is an ordered list of steps maintained by the agent. A step records its purpose, status, tool executions, input artifacts, output artifacts, and error summary. Plan revisions are persisted rather than overwriting all history.

### 3.4 Artifact

An artifact is a durable task or project asset with:

- stable identifier;
- artifact type;
- filesystem location;
- content type;
- creation timestamp;
- producing tool execution, when applicable;
- input artifact identifiers;
- preview metadata;
- human-readable title and description.

Initial artifact types are source dataset, derived dataset, SQL, Python code, chart, table preview, execution log, and Markdown report.

### 3.5 Evidence reference

A report claim links to supporting artifacts using stable artifact identifiers rather than raw filesystem paths. The API resolves those identifiers for the workbench.

## 4. User Experience

The local Web interface has three conceptual areas:

1. **Project navigation:** datasets, business knowledge, tasks, and published artifacts.
2. **Task workbench:** objective, plan, execution timeline, errors, code, tables, charts, and report.
3. **Conversation:** new instructions, follow-up questions, business corrections, explanations, and approval requests.

The layout may change during UI design, but these responsibilities must remain separate.

The first-run path is:

1. User creates a project and adds data files.
2. The system registers source artifacts and performs a lightweight structural inspection.
3. User asks a business question.
4. The agent determines whether the available data can answer it.
5. The agent records a plan and begins low-risk execution.
6. Workbench events appear while analysis proceeds.
7. If a business definition is missing, the task moves to `needs_input` and asks one focused question.
8. The agent synthesizes a report with evidence links.
9. Follow-up instructions create new steps or a persisted plan revision in the same task.

## 5. System Architecture

The MVP is a local Web application with the following layers:

```text
React task workbench
        |
     HTTP/SSE
        |
FastAPI local service
        |
Agent runtime
  - agent loop
  - context builder
  - tool registry
  - permission policy
        |
Analysis workspace
  - project filesystem
  - SQLite metadata
  - DuckDB query engine
  - controlled Python execution
```

### 5.1 Web client

The client is built with React, TypeScript, and Vite. It renders project navigation, task state, the plan, tool-event summaries, artifact previews, the report, and conversation. It does not contain analysis logic.

### 5.2 API service

The FastAPI service provides project, dataset, task, message, artifact, and approval endpoints. Server-Sent Events deliver ordered task events to the client. SSE is one-way because user actions can use ordinary HTTP requests.

### 5.3 Agent runtime

The runtime implements one model-controlled tool-use loop. It assembles project and task context, calls a model provider, validates requested tool calls, evaluates permissions, executes allowed tools, appends tool results, and continues until the model completes or the task pauses.

The model interface is provider-neutral. The first adapter supports OpenAI-compatible chat and tool-calling APIs. Provider-specific response formats must not leak into domain or tool modules.

### 5.4 Workspace service

The workspace service creates and resolves project directories, enforces path containment, registers artifacts, and prevents mutation of source datasets. Filesystem paths are internal implementation details; other modules use project and artifact identifiers.

### 5.5 Metadata store

SQLite stores projects, tasks, messages, plan revisions, steps, tool executions, artifacts, evidence links, approvals, and ordered task events. Schema migrations are required from the first release.

### 5.6 Data execution

DuckDB is the default SQL engine for CSV, Parquet, and tabular intermediate results. Excel files are imported through a dedicated adapter and registered as queryable tables or views. Polars is preferred for programmatic tabular work; Pandas may be available for library compatibility.

Python analysis runs in a subprocess with:

- the project workspace as its allowed working area;
- a fixed execution timeout;
- captured standard output and standard error;
- an explicit environment and dependency set;
- output files restricted to a task-specific working directory.

Operating-system-grade isolation is not claimed by the MVP. The UI and documentation must describe Python execution as locally trusted analysis execution, not a secure hostile-code sandbox.

## 6. Initial Tool Set

The first vertical slice exposes these model tools:

- `inspect_workspace`: list project datasets, knowledge, tasks, and relevant artifacts.
- `inspect_dataset`: return schema, samples, row counts when affordable, and basic quality observations.
- `query_data`: execute read-only DuckDB SQL and persist substantial results as derived artifacts.
- `run_python`: execute generated analysis code in the task working directory and capture outputs.
- `save_artifact`: register a generated file with provenance and preview metadata.
- `write_report`: create or revise the task's Markdown report with validated artifact references.
- `update_plan`: create or revise the active analysis plan and step statuses.
- `request_user_input`: pause the task with one focused business or data question.

Chart creation in the MVP is performed through `run_python`, using Plotly, followed by `save_artifact`. A specialized chart tool may be introduced later if repeated failures demonstrate the need.

Arbitrary shell execution, unrestricted file editing, external network access, and database administration are not model tools in the MVP.

## 7. Permission Model

Actions are classified before execution:

### Automatically allowed

- read project metadata and registered datasets;
- run read-only queries;
- create files inside the current task's derived-output area;
- run Python within configured time and path limits;
- register new artifacts;
- update the task plan and report.

### Requires explicit approval

- overwrite or delete an existing artifact;
- modify or replace source data;
- access a path outside the project workspace;
- use network access or an external system;
- install dependencies;
- execute an action not covered by an allow policy.

### Always denied in the MVP

- writing outside approved workspace roots;
- silently changing source datasets;
- executing destructive database operations;
- bypassing the approval mechanism through generated code.

Approval records include the requested action, reason, scope, user decision, and time.

## 8. Data Flow and Provenance

For each tool execution the system persists:

- validated arguments with secrets redacted;
- start and completion time;
- status and error summary;
- input artifact identifiers;
- generated artifact identifiers;
- captured execution output within configured size limits;
- associated plan step.

Source files are immutable. Data cleaning produces new versioned derived datasets. A report references artifacts with stable identifiers such as `artifact://<artifact-id>`. Before publishing a report, the report service verifies that every artifact reference exists and belongs to the same project.

## 9. Error Handling and Recovery

Tool errors are returned to the model as structured results containing an error category, safe message, retryability, and relevant diagnostics. The model may revise code, revise a query, or change its plan.

The runtime enforces per-tool retry limits and an overall task action limit. When a limit is reached, the task pauses or fails with:

- completed plan steps;
- the failed step;
- a concise error explanation;
- retained intermediate artifacts;
- permitted recovery choices.

Application restart recovery uses persisted task and event state. The MVP does not automatically resume an interrupted model call; it marks the in-flight execution interrupted and offers a controlled retry.

## 10. Testing Strategy

Tests are layered:

- **Unit tests:** domain transitions, path containment, permission classification, artifact validation, evidence validation, and provider-response normalization.
- **Tool tests:** fixture CSV and Excel inspection, read-only DuckDB enforcement, Python timeout and output containment, and artifact registration.
- **Runtime tests:** deterministic fake-model scripts validate tool-call loops, plan updates, user-input pauses, retries, and completion without real API calls.
- **API integration tests:** create a project, add a dataset, start a task, consume ordered events, and inspect resulting artifacts.
- **End-to-end acceptance test:** a small operating dataset produces a plan, executable analysis, at least one chart, and a Markdown report containing valid evidence references.

Real-model evaluations are separate from the deterministic test suite. They measure analytical quality but do not gate every local test run.

## 11. MVP Acceptance Criteria

The MVP is complete when a user can:

1. run the application locally;
2. create and reopen an analysis project;
3. add CSV and Excel source data without modifying the originals;
4. ask an operating-performance question through conversation;
5. observe a persisted, revisable analysis plan;
6. observe ordered execution events in the workbench;
7. allow the agent to execute read-only SQL and controlled Python analysis;
8. inspect persisted code, tables, charts, logs, and their provenance;
9. receive a Markdown report whose important claims contain valid artifact references;
10. continue the task with a follow-up question after restarting the application;
11. see approval requests for actions outside the automatic permission policy;
12. run deterministic automated tests without a real model API key.

## 12. Delivery Strategy

Implementation should proceed as vertical slices rather than completing all backend infrastructure before a usable flow exists:

1. project and task persistence;
2. dataset registration and inspection;
3. fake-model agent loop with task events;
4. DuckDB and controlled Python tools;
5. artifact provenance and evidence-linked Markdown report;
6. minimal workbench UI;
7. OpenAI-compatible model adapter;
8. end-to-end operating-diagnosis acceptance flow.

The existing `Data-Analysis-Agent` repository is treated only as a product and feature reference because its license restricts commercial use without separate authorization. The MIT-licensed `lean-cc` repository may inform harness patterns, but production code should remain focused on this product's domain model and boundaries.
