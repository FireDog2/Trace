# PostgreSQL business-review demo

This disposable fixture contains 24 synthetic rows and no credentials.

```powershell
# Run only PostgreSQL in Docker. The fixture is loaded automatically the first time.
docker compose up -d postgres
$env:PORTFOLIO_POSTGRES_DSN = "postgresql://portfolio:portfolio@localhost:5432/portfolio"
data-analysis-agent serve --host 127.0.0.1 --port 8765
```

Use [question.md](question.md). Code continues to run locally, so backend and
frontend edits do not require a Compose rebuild. The normal acceptance test is
deterministic and uses a fake PostgreSQL driver; opt-in live checks need a supplied
DSN:

```powershell
$env:TEST_POSTGRES_DSN = $env:PORTFOLIO_POSTGRES_DSN
python -m pytest tests/test_postgres_integration.py -v
```
