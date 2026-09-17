"""CLI entry point: `uv run python -m agents.jev_agent --entity ada`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

import structlog

from .agent import run_agent
from .tracelog import RUN_DIR_ENV, resolve_log_root


def configure_logging(level: str) -> None:
    """Send structlog output to stderr so stdout stays clean for the runner."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the agent's command line."""
    parser = argparse.ArgumentParser(description="Planner + Jev settler agent")
    parser.add_argument("--entity", required=True, help="entity id to control")
    parser.add_argument(
        "--server", default="localhost:50051", help="world server host:port"
    )
    parser.add_argument(
        "--log-root",
        default="",
        help=(
            "directory holding agent-<id>/ trace files; defaults to "
            f"${RUN_DIR_ENV}/agents when that variable is set, else ./logs"
        ),
    )
    parser.add_argument("--planner-model", default="", help="override PLANNER_MODEL")
    parser.add_argument("--jev-model", default="", help="override JEV_MODEL")
    parser.add_argument("--log-level", default="info", help="debug, info, warning")
    return parser.parse_args(argv)


def check_environment() -> None:
    """Fail fast and loudly when an API key is missing."""
    missing = [
        name
        for name in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(
            f"missing required environment variables: {', '.join(missing)}. "
            "Source the repository .env first."
        )


async def _main_async(args: argparse.Namespace) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(
        run_agent(
            args.server,
            args.entity,
            log_root=resolve_log_root(args.log_root),
            planner_model=args.planner_model,
            jev_model=args.jev_model,
        )
    )
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        structlog.get_logger(__name__).info("shutdown", entity=args.entity)


def main(argv: list[str] | None = None) -> None:
    """Run one agent process."""
    args = parse_args(argv)
    configure_logging(args.log_level)
    check_environment()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
