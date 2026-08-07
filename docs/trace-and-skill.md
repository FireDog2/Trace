# Trace and skill review

Each analysis run has ordered events with type, status, parent relationship, attempt, safe error category, duration and artifact references. One `TraceContext` follows the lead into spawned durable teammates and their tools. PostgreSQL events retain only SQL hashes, policy/EXPLAIN summaries, approval IDs, bounded result counts and artifact IDs. Payloads are recursively redacted and bounded before storage. Replay reads persisted events; it does not re-execute a model or database query.

A workflow can become a candidate only after a completed trace with successful tool evidence is explicitly accepted by a human. Acceptance idempotently creates one durable `CURATOR` agent whose tool allowlist is empty. It receives redacted event summaries, runs static safety validation, and writes one disabled candidate. Provider, eligibility and validation failures remain durable agent terminal states. Human review may approve or reject a candidate, but approval deliberately leaves `enabled=false`.

Provider timeouts, network failures, rate limits and upstream errors retry at most twice with exponential backoff. Retryable tool failures are counted per normalized call signature; the model receives remaining-attempt metadata and then an explicit fallback instruction. Identical failed calls and total tool actions remain bounded.
