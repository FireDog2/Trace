# Architecture

The React workbench calls FastAPI. The lead agent owns task state and dispatches only registered tools. SQLite stores projects, task events, approvals, teammate records, context checkpoints, traces, and skill candidates. Artifacts live in the project workspace and reports cite artifact IDs.

The lead can create Knowledge, SQL, and Report teammates. Each has its own durable run record and role allowlist. The SQL gateway uses protected source credentials, policy snapshots, SQL AST validation, EXPLAIN risk classification, and a read-only PostgreSQL driver. The UI is an observability surface; it does not receive secrets.

Prompt text is assembled deterministically from versioned stable and dynamic sections for each role. Lead and teammate transcripts remain immutable audit records. Before each model call, the context manager creates a bounded projection, budgets oversized tool results, reuses the latest checkpoint, and creates a new checkpoint when the input estimate crosses the configured limit. Prompt hashes, section versions, context estimates, compact reasons, and checkpoint IDs are trace events; replay exports checkpoint metadata but not checkpoint text.

Business Knowledge is the project-scoped long-term semantic memory for metrics, terms, and rules. Session checkpoints are short-term continuity state. Keeping these separate avoids hidden global memory and preserves project isolation and provenance.
