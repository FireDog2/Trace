# Contributing

Use Python 3.12 and Node 20. Install with `python -m pip install -e ".[dev]"` and `npm --prefix web ci`. Run `python -m pytest -v`, `npm --prefix web test -- --run`, and `npm --prefix web run build` before proposing changes.

Migrations are append-only and must work for a fresh SQLite database and an upgraded one. A new tool must declare its permission behavior, safe failure category, and trace behavior. Never add secrets, DSNs, or production data to fixtures, examples, tests, or logs.
