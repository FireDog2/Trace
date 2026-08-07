# Multi-agent runtime

The lead coordinates at most three active teammates: Knowledge, SQL, and Report. Role allowlists prevent a Knowledge teammate from calling SQL tools and constrain every teammate to its task. Runs are durable (`created`, `working`, `waiting`, terminal), independently message-scoped, cancellable, and have bounded turns/deadlines. Each row persists the parent analysis trace and spawn event so a fresh worker can resume correlation after restart.

The manager claims worker leases to avoid duplicate execution and marks stale active work interrupted at startup. Shutdown requests cancellation and does not promise immediate process termination. A fourth role, `CURATOR`, is not available to the lead: only the accepted-run path may create it, one per trace, and its tool allowlist is empty. The design is bounded delegation, not an unrestricted swarm.
