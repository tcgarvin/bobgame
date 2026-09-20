"""CLI entry point for the runner."""

import argparse
import os
import signal
from pathlib import Path

import structlog

from .config import load_config, find_config
from .manager import ProcessManager

# The project ships one scenario; its runner config is the default.
DEFAULT_CONFIG = "hamlet"

# Env fallback for --resume-from, exported by dev.sh --resume.
RESUME_FROM_ENV = "BOBGAME_RESUME_FROM"


def setup_signal_handlers(manager: ProcessManager) -> None:
    """Set up signal handlers for graceful shutdown.

    Args:
        manager: ProcessManager to notify on shutdown signals.
    """
    logger = structlog.get_logger()

    def handler(signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("signal_received", signal=sig_name)
        manager.request_shutdown()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def main() -> None:
    """Run the agent runner."""
    parser = argparse.ArgumentParser(
        description="Bob's World Agent Runner - spawns and manages agent processes"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=DEFAULT_CONFIG,
        help=f"Path or name of runner TOML config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="World server address (overrides config)",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=None,
        help="Log directory (overrides config)",
    )
    parser.add_argument(
        "--resume-from",
        type=str,
        default=None,
        help="Save directory this run resumes from; every agent command gets "
        "`--resume-from <dir>` appended (default: $BOBGAME_RESUME_FROM)",
    )
    parser.add_argument(
        "--agents-dir",
        type=str,
        default=None,
        help="Directory containing agents package (auto-detected if not specified)",
    )

    args = parser.parse_args()

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    logger = structlog.get_logger()

    # Load config. There is no built-in fallback: a config that cannot be
    # found is an error naming what was looked for.
    try:
        config_path = find_config(args.config)
    except FileNotFoundError as exc:
        config_path = Path(args.config)
        if not config_path.exists():
            logger.error("config_not_found", path=args.config, error=str(exc))
            raise SystemExit(1)

    config = load_config(config_path)
    logger.info("config_loaded", path=str(config_path))

    # Apply CLI overrides
    if args.server:
        config.runner.server = args.server
    if args.log_dir:
        config.runner.log_dir = args.log_dir
    resume_from = args.resume_from or os.environ.get(RESUME_FROM_ENV, "")
    if resume_from:
        config.runner.resume_from = resume_from

    # Determine working directory for agent processes
    if args.agents_dir:
        working_dir = Path(args.agents_dir)
    else:
        # Auto-detect: look for agents/ directory relative to runner
        # runner/ is a sibling of agents/ in the project
        runner_package = Path(__file__).parent
        project_root = runner_package.parent.parent.parent
        working_dir = project_root / "agents"
        if not working_dir.exists():
            working_dir = Path.cwd()

    logger.info(
        "runner_starting",
        server=config.runner.server,
        log_dir=config.runner.log_dir,
        working_dir=str(working_dir),
        auto_discover=config.runner.auto_discover,
        resume_from=config.runner.resume_from or None,
    )

    # Create manager
    manager = ProcessManager(
        config=config,
        working_dir=working_dir,
    )

    # Set up signal handlers
    setup_signal_handlers(manager)

    # Discover and spawn
    try:
        spawned = manager.discover_and_spawn()
        logger.info("agents_spawned", count=spawned)

        if spawned == 0:
            logger.warning("no_agents_spawned")
            return

        # Run until shutdown
        manager.run()

    except RuntimeError as e:
        logger.error("runner_error", error=str(e))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
