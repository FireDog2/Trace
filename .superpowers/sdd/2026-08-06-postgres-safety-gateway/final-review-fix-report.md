# PostgreSQL final-review fix report

## Final-review items

- **A — durable attempts:** RED migration test showed no attempt table; GREEN adds replay-safe migration 4 and `in_flight` / `completed` / `failed` records. The serialized pre-dispatch transaction validates policy, consumes an approved action, increments budget, creates the attempt, then commits. Timeout, result, serialization, and artifact paths finish the attempt without restoring accounting. Policy replacement rejects a non-stale attempt and converts attempts older than twice the configured statement timeout to `failed/stale_attempt`.
- **B — object identity:** inspection now uses `pg_catalog` with `pg_table_is_visible`, while inspection, EXPLAIN, and query all set the parameterized fixed `pg_catalog, public` local search path. Schema-qualified user SQL remains validator-denied.
- **C — locks:** RED tests for `FOR UPDATE`, `FOR SHARE`, and CTE locks passed after adding an `exp.Lock` denial.
- **D — previews:** RED JSON serialization test passed after recursive bounded normalization: Decimal strings, ISO temporal values, UUID strings, base64-prefixed bytes, and recursive containers.
- **E — messages:** database tool calls/results are persisted only as hashes, counts, risk, artifact id and safe status. The current AgentRunner loop overlays the rich SQL/result into the next provider request without storing it.
- **F — risk and limits:** explain exposes missing-filter/join-constraint signals. Medium/high execution derives smaller timeout, row, and byte limits; query fetches `row_limit + 1`.
- **G — credentials:** existing `CredentialError` boundary remains fixed-message / fixed-category and API storage failure remains a fixed 503 response.
- **H — integration:** `tests/test_postgres_integration.py` is opt-in only through `TEST_POSTGRES_DSN`, skips without it, and never prints it. README documents the command.
- **I — pytest compatibility:** removed the obsolete Starlette warning filter; collection succeeds under the declared dependency range without environment changes.
- **J — result boundaries:** overflow is detected from the extra row; duplicate columns are deterministically disambiguated with `_2`, `_3`, etc.

## RED/GREEN evidence

- RED then GREEN: migration durable-attempt table test.
- RED then GREEN: all locking-clause validator variants.
- RED then GREEN: JSON-safe nested PostgreSQL preview values.
- GREEN regression: stale recovery and current-run-only AgentRunner rich database result.

## Verification

- Focused: `G:\data-analysis-agent\.venv\Scripts\python.exe -m pytest tests/test_postgres_gateway.py -q` — 37 passed.
- Default suite: `G:\data-analysis-agent\.venv\Scripts\python.exe -m pytest -q` — 193 passed, 1 skipped (the opt-in PostgreSQL test), 1 third-party deprecation warning.
- Live entry point: `G:\data-analysis-agent\.venv\Scripts\python.exe -m pytest tests/test_postgres_integration.py -v` — skips unless `TEST_POSTGRES_DSN` is explicitly supplied; no DSN was present for this run.
- `git diff --check` — clean.

## Migration and risk notes

Migration 4 is `CREATE TABLE IF NOT EXISTS` plus a recorded migration version, so rerunning after a missing version record is safe. The in-flight policy fence is intentionally source-scoped and has bounded stale recovery; a live process that exceeds twice its configured statement timeout can be treated as stale, so deployments should retain a timeout appropriate for their database/network envelope. The integration coverage deliberately does not discover a DSN or connect to any default/local database.

Commit: `e9c7685` (the final amended hash is reported with delivery).
