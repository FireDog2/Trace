# Architecture

The React workbench calls FastAPI. The lead agent owns task state and dispatches only registered tools. SQLite stores projects, task events, approvals, teammate records, traces, and skill candidates. Artifacts live in the project workspace and reports cite artifact IDs.

The lead can create Knowledge, SQL, and Report teammates. Each has its own durable run record and role allowlist. The SQL gateway uses protected source credentials, policy snapshots, SQL AST validation, EXPLAIN risk classification, and a read-only PostgreSQL driver. The UI is an observability surface; it does not receive secrets.
