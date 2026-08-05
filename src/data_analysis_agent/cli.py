from __future__ import annotations

import argparse

import uvicorn

from data_analysis_agent.api import create_app
from data_analysis_agent.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="data-analysis-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Run the local analysis API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()

    if arguments.command == "serve":
        uvicorn.run(
            create_app(Settings.local()),
            host=arguments.host,
            port=arguments.port,
        )


if __name__ == "__main__":
    main()
