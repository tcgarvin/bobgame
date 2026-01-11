"""Agent process lifecycle management."""

import os
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import IO

import structlog

logger = structlog.get_logger()


class ProcessState(Enum):
    """Agent process states."""

    PENDING = auto()  # Not yet started
    RUNNING = auto()  # Process is running
    STOPPED = auto()  # Gracefully stopped
    CRASHED = auto()  # Exited with non-zero code
    RESTARTING = auto()  # Waiting for backoff before restart


@dataclass
class AgentProcess:
    """Manages a single agent subprocess."""

    entity_id: str
    module: str  # Python module to run
    server_address: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    log_dir: Path | None = None
    working_dir: Path | None = None  # Directory containing agents/ package

    # Internal state
    _process: subprocess.Popen | None = field(default=None, init=False, repr=False)
    _log_file: IO | None = field(default=None, init=False, repr=False)
    _state: ProcessState = field(default=ProcessState.PENDING, init=False)
    _exit_code: int | None = field(default=None, init=False)
    _start_time: float | None = field(default=None, init=False)
    _restart_count: int = field(default=0, init=False)

    @property
    def state(self) -> ProcessState:
        """Current process state."""
        return self._state

    @property
    def pid(self) -> int | None:
        """Process ID if running."""
        return self._process.pid if self._process else None

    @property
    def exit_code(self) -> int | None:
        """Exit code if process has terminated."""
        return self._exit_code

    @property
    def restart_count(self) -> int:
        """Number of times this process has been restarted."""
        return self._restart_count

    def start(self) -> None:
        """Start the agent process.

        Raises:
            RuntimeError: If process is already running.
        """
        if self._state == ProcessState.RUNNING:
            raise RuntimeError(f"Agent {self.entity_id} is already running")

        # Build command: uv run python -m <module> --server <addr> --entity <id> [args...]
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            self.module,
            "--server",
            self.server_address,
            "--entity",
            self.entity_id,
            *self.args,
        ]

        # Set up environment
        env = os.environ.copy()
        env.update(self.env)

        # Open log file if log_dir specified
        stdout: IO | int | None = None
        stderr: IO | int | None = None

        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.log_dir / f"agent-{self.entity_id}.log"
            self._log_file = open(log_path, "a")
            stdout = self._log_file
            stderr = subprocess.STDOUT

        logger.info(
            "starting_agent",
            entity_id=self.entity_id,
            module=self.module,
            working_dir=str(self.working_dir),
        )

        self._process = subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            env=env,
            cwd=self.working_dir,
        )

        self._state = ProcessState.RUNNING
        self._start_time = time.time()
        self._exit_code = None

        logger.info(
            "agent_started",
            entity_id=self.entity_id,
            pid=self._process.pid,
        )

    def poll(self) -> ProcessState:
        """Check process status and update state.

        Returns:
            Current process state after polling.
        """
        if self._state != ProcessState.RUNNING or not self._process:
            return self._state

        exit_code = self._process.poll()
        if exit_code is not None:
            self._exit_code = exit_code
            self._state = ProcessState.STOPPED if exit_code == 0 else ProcessState.CRASHED

            logger.info(
                "agent_exited",
                entity_id=self.entity_id,
                pid=self._process.pid,
                exit_code=exit_code,
                state=self._state.name,
            )

            self._cleanup_log()

        return self._state

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the process gracefully, then forcefully if needed.

        Args:
            timeout: Seconds to wait for graceful shutdown before SIGKILL.
        """
        if not self._process or self._state != ProcessState.RUNNING:
            return

        logger.info("stopping_agent", entity_id=self.entity_id, pid=self._process.pid)

        # Send SIGTERM
        self._process.terminate()

        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("force_killing_agent", entity_id=self.entity_id)
            self._process.kill()
            self._process.wait(timeout=2.0)

        self._exit_code = self._process.returncode
        self._state = ProcessState.STOPPED
        self._cleanup_log()

    def mark_restarting(self) -> None:
        """Mark this process as pending restart."""
        self._state = ProcessState.RESTARTING
        self._restart_count += 1
        self._process = None

    def _cleanup_log(self) -> None:
        """Close log file if open."""
        if self._log_file:
            self._log_file.close()
            self._log_file = None
