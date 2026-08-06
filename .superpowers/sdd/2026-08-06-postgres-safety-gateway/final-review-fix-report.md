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

Implementation commit: `93a502a`.

## Round 2/5

- **E:** migration 5 adds `protected_tool_message_payloads`, keyed by message id. Database assistant tool calls and tool results retain only safe summaries in `messages`; encrypted raw provider content is replayed on every request, including after approval resume. A pausing call emits `cancelled_due_to_pause` result envelopes for its unexecuted siblings.
- **G:** source creation now converts any `protect()` failure to `CredentialError` without retaining the DSN or original exception; the POST route maps it to the fixed secure-storage 503.
- **F/J:** unbounded index scans and unconstrained joins raise plan risk to at least medium; derived limits reach the driver. Column disambiguation is globally collision-free and settings reject row limits outside 1..10000.
- **H:** the opt-in integration test now uses an explicit DSN only, creates uniquely named public/schema/sequence objects, invokes both driver and gateway behavior, and always drops the objects.
- **A:** migrations 5/6 are replay-safe; migration 6 repairs the source foreign keys for earlier version-3/4 databases and fresh migration definitions include those FKs.

Round-2 verification: focused agent/gateway tests passed (44); default suite passed (196) with the one explicit `TEST_POSTGRES_DSN` skip. No test DSN was present, so no live connection was made.

## Round 3/5

- Protected query tool-call payloads are encrypted before any SQLite write and inserted atomically with their safe message envelope. Successful rich database results use the same one-transaction path; safe error/pause results never require a second protected write.
- Migration 6 now uses `BEGIN IMMEDIATE` for the complete rebuild, index creation, and migration-version record. Interrupted legacy `*_repaired` tables have deterministic recovery, and foreign keys are restored on exit.
- The opt-in live test includes a restricted, approved cross-join statement with a short timeout and asserts the durable consumed approval, budget, and failed attempt category. It remains skipped unless `TEST_POSTGRES_DSN` is explicitly supplied.

## Round 4/5

The live test now materializes and verifies the single-row JSON-safe gateway result before inserting pressure rows. The timeout uses a separate task, so its durable budget assertion is exactly one dispatched approved attempt. The live module still collects and skips cleanly without `TEST_POSTGRES_DSN`; deterministic gateway tests continue to cover the same successful and derived-limit paths without a database.
