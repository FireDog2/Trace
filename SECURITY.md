# Security policy

This portfolio is supported for trusted local development only. Use a dedicated read-only PostgreSQL account and never expose the service publicly. Credentials are write-only in the browser and protected at rest where the local platform supports it; logs/traces must not include DSNs, API keys, passwords, authorization headers, or raw sensitive query results.

Python execution is not a hostile-code sandbox. PostgreSQL safety controls reduce risk but do not replace database permissions or operational review. Report vulnerabilities privately to the repository owner with reproduction steps, affected version, and impact; do not publish credentials or customer data in an issue.
