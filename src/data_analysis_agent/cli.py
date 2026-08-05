from __future__ import annotations

import argparse

import uvicorn

from data_analysis_agent.api import create_app
from data_analysis_agent.providers import ProviderError, provider_from_settings
from data_analysis_agent.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="data-analysis-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Run the local analysis API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    arguments = parser.parse_args()

    if arguments.command == "serve":
        settings = Settings.local()
        try:
            provider = provider_from_settings(settings)
        except ProviderError as exc:
            if exc.category != "not_configured":
                raise
            provider = None
        uvicorn.run(
            create_app(settings, provider),
            host=arguments.host,
            port=arguments.port,
        )


if __name__ == "__main__":
    main()
