from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import uvicorn

from data_analysis_agent.api import create_app
from data_analysis_agent.demo import run_portfolio_demo
from data_analysis_agent.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="data-analysis-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Run the local analysis API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data-dir", type=Path)
    demo = subparsers.add_parser(
        "demo",
        help="Create a deterministic, API-key-free portfolio workspace",
    )
    demo.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / ".data-analysis-agent-demo",
    )
    demo.add_argument(
        "--reset",
        action="store_true",
        help="Replace the dedicated demo directory before running",
    )
    arguments = parser.parse_args()

    if arguments.command == "serve":
        settings = Settings.local(arguments.data_dir)
        uvicorn.run(
            create_app(settings),
            host=arguments.host,
            port=arguments.port,
        )
    elif arguments.command == "demo":
        data_dir = arguments.data_dir.resolve()
        if arguments.reset and data_dir.exists():
            _validate_demo_reset_target(data_dir)
            shutil.rmtree(data_dir)
        print(run_portfolio_demo(Settings.local(data_dir)).to_json())


def _validate_demo_reset_target(path: Path) -> None:
    cwd = Path.cwd().resolve()
    try:
        path.relative_to(cwd)
    except ValueError as exc:
        raise SystemExit("Demo reset target must be inside the current workspace") from exc
    if not path.name.startswith(".data-analysis-agent-demo"):
        raise SystemExit("Demo reset target directory must start with '.data-analysis-agent-demo'")


if __name__ == "__main__":
    main()
