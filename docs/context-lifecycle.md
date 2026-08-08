# Context Lifecycle

The runtime treats persisted messages as the authoritative transcript and the model request as a derived, bounded projection. Compaction never deletes or rewrites task or teammate messages.

## Prompt assembly

`PromptContext` selects ordered, versioned sections for identity, safety, role policy, recovery, output contract, and current runtime state. Stable sections precede the dynamic runtime section. The assembly exposes section IDs, versions, a content hash, and a provider-neutral token estimate. Lead and teammate traces record this metadata without storing full prompts.

Tool schemas remain in the provider's structured `tools` field. The runtime section lists enabled tool names so a role can reason about its current allowlist without duplicating schemas in prompt text.

## Context projection

Before every model call, `ContextManager`:

1. Loads the full durable transcript and the latest owner-scoped checkpoint.
2. Replaces an oversized model-visible tool result with valid protocol JSON containing bounded status, recovery, and artifact metadata. The persisted result is unchanged.
3. Estimates the complete request size, including system prompt, objective, messages, and tool schemas.
4. If over budget, summarizes the older prefix into a persisted checkpoint and retains recent protocol-complete messages.
5. On a provider `context_length_exceeded` rejection, performs one forced reactive compaction and retries once.

Checkpoints are independently scoped to a lead task or teammate run. A checkpoint records its transcript boundary, source-message count, before/after estimates, trigger reason, and bounded summary. Replay exposes only metadata and a summary hash.

The estimator intentionally uses a deterministic four-characters-per-token approximation. It is a portable budgeting signal, not a claim about provider billing.

## Memory boundaries

Session checkpoints preserve current-goal continuity across compaction. They are not long-term truth.

Project Business Knowledge is the governed long-term semantic memory. It stores versioned metrics, terms, and rules with project scope and source metadata; only the Knowledge Agent can search it through its role allowlist. The runtime does not silently extract global user memory from every conversation.

This separation keeps memory reviewable:

- transcript: complete execution history;
- checkpoint: bounded session continuation;
- Business Knowledge: project-scoped accepted semantics;
- artifact: durable analytical evidence;
- trace: why the harness assembled or compacted a particular model request.
