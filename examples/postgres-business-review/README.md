# PostgreSQL business-review demo

This disposable fixture contains 24 synthetic rows and no credentials.

```powershell
$env:PORTFOLIO_POSTGRES_DSN = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
psql $env:PORTFOLIO_POSTGRES_DSN -f tests/fixtures/postgres_business_review.sql
data-analysis-agent serve --host 127.0.0.1 --port 8765
```

Use [question.md](question.md). The normal acceptance test is deterministic and uses a fake PostgreSQL driver; opt-in live checks need a supplied DSN.
