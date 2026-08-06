# Trace and skill review

Each analysis run has ordered events with type, status, parent relationship, attempt, safe error category, duration and artifact references. Payloads are recursively redacted and bounded before storage. Replay reads persisted events; it does not re-execute a model or database query.

A workflow can become a candidate only after a completed, accepted trace with successful tool evidence. Static validation rejects incomplete or unsafe content. Human review may approve or reject a candidate, but approval deliberately leaves `enabled=false`.
