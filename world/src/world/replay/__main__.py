"""CLI entry point: `python -m world.replay --runs-dir ../runs --port 8766`."""

import argparse
import asyncio
from pathlib import Path

import structlog

from .service import DEFAULT_REPLAY_PORT, ReplayWebSocketService

logger = structlog.get_logger()

# Project root: the parent of the world/ package directory.
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent


def main() -> None:
    """Parse arguments and serve recorded runs until interrupted."""
    parser = argparse.ArgumentParser(description="Bob's World replay server")
    parser.add_argument(
        "--runs-dir",
        type=str,
        default=str(PROJECT_ROOT / "runs"),
        help="Directory holding run directories (default: <project_root>/runs)",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_REPLAY_PORT, help="WebSocket port"
    )
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Bind address")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    )

    runs_dir = Path(args.runs_dir).resolve()
    if not runs_dir.is_dir():
        parser.error(f"Runs directory not found: {runs_dir}")

    service = ReplayWebSocketService(
        runs_dir=runs_dir,
        project_root=PROJECT_ROOT,
        host=args.host,
        port=args.port,
    )
    logger.info("replay_server_starting", runs_dir=str(runs_dir), port=args.port)
    try:
        asyncio.run(service.serve_forever())
    except KeyboardInterrupt:
        logger.info("replay_server_stopped")


if __name__ == "__main__":
    main()
