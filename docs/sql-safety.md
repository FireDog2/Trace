# SQL safety

Only supported read-only SQL passes the SQL AST validator. Identifiers are checked against inspected schema and source policy rules. Denied fields never reach PostgreSQL; restricted fields and high-cost plans create an approval bound to the query hash and policy version. Approval is consumed once.

The gateway runs `EXPLAIN` before execution and uses a heuristic based on estimated cost, rows, scans, and joins. Execution configures PostgreSQL read-only behavior, statement/lock timeouts and result limits. Errors are normalized to safe categories such as `query_timeout`; DSNs and credentials are redacted from trace/API payloads. This is defense in depth, not a query-cost guarantee.

Restricted-field approval grants are scoped to one task, data source, and policy version. A grant may be reused by later SQL that accesses the same approved fields or a subset; adding a restricted field or changing the active policy requires another approval. High-risk query plans remain bound to an exact SQL hash and consume a separate one-time approval.
