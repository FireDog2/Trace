# Contributing

Use Python 3.12 and Node 20. Install with `python -m pip install -e ".[dev]"` and `npm --prefix web ci`.

Run the same quality gates as CI before proposing changes:

```text
ruff check src tests scripts
ruff format --check src tests scripts
mypy src
pytest --cov=data_analysis_agent --cov-report=term-missing
npm --prefix web run format:check
npm --prefix web test -- --run
npm --prefix web run build
```

Migrations are append-only and must work for a fresh SQLite database and an upgraded one. A new tool must declare its permission behavior, safe failure category, and trace behavior. Never add secrets, DSNs, or production data to fixtures, examples, tests, or logs.
