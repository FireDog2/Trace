# Task 3 report: lead teammate coordination

Implemented durable, bounded teammate coordination.

- `TeamRepository.create_run_if_capacity()` performs the active-run count and insert in one `BEGIN IMMEDIATE` transaction.
- `TeamManager` uses a bounded executor, durable pre-submit creation, lock-protected futures/cancellation events, safe failure conversion, cancellation, idempotent shutdown, and durable-record preservation.
- Lead-only registry wiring supplies `spawn_teammate`, `list_teammates`, `await_teammates`, and `cancel_teammate`; restricted teammate registries omit them. Handlers enforce current-task ownership and bounded input.
- Application startup marks interrupted teammate work before traffic; shutdown stops the manager before closing the shared connection. Worker runners build fresh services, provider, registry, and SQLite connection, then close them after their run.
- `await_teammates` rich handoffs are explicitly protected in the lead transcript. Persistent safe protocol messages contain only handoff summaries and safe errors; the active provider receives the protected payload.

Verification: `G:\data-analysis-agent\.venv\Scripts\python.exe -m pytest tests/test_team_manager.py tests/test_team_repository.py tests/test_teammate_runner.py tests/test_tools.py tests/test_agent.py tests/test_api.py -q` completed with 68 passing tests.

## Fix round 1

- Lead coordination tools are automatic policy actions; role-restricted registries still omit them.
- Startup now terminalizes every stale active row, honoring queued cancellation first. Shutdown explicitly terminalizes futures cancelled before execution and rejects new spawns without creating durable rows.
- Production worker construction no longer creates a nested manager. Each worker path uses a fresh connection and closes it; pre-run failure uses a separate fresh connection to terminalize the durable run.
- Role input is the shared `AgentRole` enum, and teammate identifiers have bounded list and item lengths.

Round verification: focused suite 84 passed; full suite 230 passed, 1 PostgreSQL integration test skipped because `TEST_POSTGRES_DSN` is unset.

## Fix round 2

`spawn()` now holds the manager lock from closed-state check through durable capacity admission, submit, and future registration. `shutdown()` therefore either observes and cancels that registered future, or wins first and causes `TeamManagerClosed` before repository access. Done callbacks are registered after releasing the lock to avoid synchronous-completion lock re-entry.

Round verification: focused manager suite 3 passed; full suite 232 passed, 1 PostgreSQL integration test skipped because `TEST_POSTGRES_DSN` is unset.

## Fix round 3

Service construction now owns the SQLite connection before constructing dependent services and closes it on every construction failure. The worker wrapper already closes its independently constructed runtime after provider resolution, runner construction, success, or failure. The spawn/shutdown race test now explicitly verifies that shutdown cannot return while a spawn has passed its initial closed check but remains inside capacity admission; this is a RED regression against the pre-linearization implementation.

Round verification: focused manager/API suite 23 passed; full suite 232 passed, 1 PostgreSQL integration test skipped because `TEST_POSTGRES_DSN` is unset.
