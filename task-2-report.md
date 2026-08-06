# Task 2: role-scoped teammate loop

Implemented a bounded `TeammateRunner` with independent `agent_runs` histories,
role-specific prompts/tool views, JSON-object handoffs, timeout/turn/cancellation
checks, and safe provider/tool failure categories.  A restricted registry shares
the original permission, approval, task, and artifact services while exposing only
the selected definitions.

SQL teammate `query_database` protocol messages now persist a redacted summary in
`agent_messages` and ciphertext in migration-8
`protected_agent_message_payloads`; requests are decrypted only when rebuilding
provider history.  Knowledge and report messages retain normal storage.

Verification (shared `G:\data-analysis-agent\.venv`):

- `pytest tests/test_teammate_runner.py tests/test_team_repository.py tests/test_tools.py tests/test_agent.py -v`: 31 passed.
- `pytest -q`: 210 passed, 1 skipped (`TEST_POSTGRES_DSN` unset), 1 existing dependency deprecation warning.
- `git diff --check`: no whitespace errors.

## Fix round 1/5

- Re-check cancellation (which wins ties) and deadline immediately after each
  model response, plus after each tool handler result, before any subsequent
  persistence, tool, or handoff completion.
- Encrypt database results only when successful and rich (`preview`, `rows`, or
  `raw_result`); error, approval, denied, and cancellation results remain safe
  summaries. Persistence failures now end terminally with
  `message_persistence_error`, and sibling tool calls always receive a paired
  cancelled result after a tool exception.
- Reject `NaN`, `Infinity`, and `-Infinity` in JSON handoffs and prohibit
  non-finite values in the canonical serialized result.

## Fix round 2/5

- A tool result that returns after cancellation or deadline is discarded before
  persistence; the current and sibling calls receive only paired cancellation
  summaries (`cancelled_due_to_cancel` or `cancelled_due_to_timeout`).
- Migration 9 adds replay-safe durable `turns_used` and absolute `deadline_at`
  fields. `reserve_turn()` atomically establishes the deadline once and claims
  each provider turn, so resumes preserve both limits.
- Verification: focused migration/teammate suite: 24 passed; full suite: 220
  passed, 1 Postgres integration test skipped because `TEST_POSTGRES_DSN` is
  unset.

## Fix round 3/5

- Pre-tool cancellation and timeout now use distinct paired-result categories.
- Re-entry continues an already `working` run without an invalid self-transition.
- Turn claims use an explicit conditional SQLite update; a post-claim boundary
  recheck may terminate without calling the provider, while retaining that
  claimed scheduling attempt for safety.
