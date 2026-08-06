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
