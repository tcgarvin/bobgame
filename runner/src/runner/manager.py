"""Process manager for coordinating multiple agents."""

import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from .config import Config
from .discovery import DiscoveredEntity, discover_entities, wait_for_server
from .process import AgentProcess, ProcessState

logger = structlog.get_logger()


@dataclass
class RestartState:
    """Tracks restart state for an entity."""

    attempts: int = 0
    last_restart_time: float = 0.0
    next_backoff_ms: int = 1000


@dataclass
class ProcessManager:
    """Manages multiple agent processes with health monitoring."""

    config: Config
    working_dir: Path  # Directory containing agents/ package

    # Internal state
    _processes: dict[str, AgentProcess] = field(default_factory=dict, init=False)
    _restart_states: dict[str, RestartState] = field(default_factory=dict, init=False)
    _running: bool = field(default=False, init=False)
    _shutdown_requested: bool = field(default=False, init=False)

    def discover_and_spawn(self) -> int:
        """Discover entities and spawn agent processes.

        Returns:
            Number of agents spawned.

        Raises:
            RuntimeError: If cannot connect to world server.
        """
        server = self.config.runner.server
        timeout = self.config.runner.connection_timeout_ms / 1000.0

        logger.info("waiting_for_server", server=server)
        if not wait_for_server(server, timeout_seconds=timeout):
            raise RuntimeError(f"Could not connect to world server at {server}")

        logger.info("discovering_entities", server=server)
        entities = discover_entities(server, timeout_seconds=timeout)

        if not entities:
            logger.warning("no_entities_found")
            return 0

        logger.info("entities_discovered", count=len(entities))

        spawned = 0
        for entity in entities:
            if self._spawn_agent(entity):
                spawned += 1

        return spawned

    def _spawn_agent(self, entity: DiscoveredEntity) -> bool:
        """Spawn an agent for the given entity.

        Args:
            entity: The discovered entity to spawn an agent for.

        Returns:
            True if agent was spawned, False if skipped.
        """
        entity_id = entity.entity_id

        # Skip if already leased (unless we own it)
        if entity.has_active_lease and entity_id not in self._processes:
            logger.info(
                "skipping_leased_entity",
                entity_id=entity_id,
            )
            return False

        # Get agent config
        try:
            agent_config = self.config.get_agent_config(entity_id)
        except KeyError:
            if not self.config.runner.auto_discover:
                logger.debug("no_config_for_entity", entity_id=entity_id)
                return False
            raise RuntimeError(
                f"No agent config for '{entity_id}' and no default defined"
            )

        # Create and start process
        log_dir = Path(self.config.runner.log_dir)

        process = AgentProcess(
            entity_id=entity_id,
            module=agent_config.module,
            server_address=self.config.runner.server,
            args=list(agent_config.args),
            env=dict(agent_config.env),
            log_dir=log_dir,
            working_dir=self.working_dir,
        )

        process.start()
        self._processes[entity_id] = process
        self._restart_states[entity_id] = RestartState(
            next_backoff_ms=self.config.runner.initial_backoff_ms,
        )

        return True

    def run(self) -> None:
        """Run the process manager loop until shutdown."""
        self._running = True

        logger.info("process_manager_running", process_count=len(self._processes))

        try:
            while self._running and not self._shutdown_requested:
                self._check_processes()
                time.sleep(0.5)  # Health check interval
        except KeyboardInterrupt:
            logger.info("keyboard_interrupt")
        finally:
            self.shutdown()

    def _check_processes(self) -> None:
        """Check all processes and handle crashes."""
        for entity_id, process in list(self._processes.items()):
            state = process.poll()

            if state == ProcessState.CRASHED:
                self._handle_crash(entity_id, process)

            elif state == ProcessState.RESTARTING:
                self._maybe_restart(entity_id, process)

            elif state == ProcessState.STOPPED:
                # Agent exited cleanly - don't restart
                logger.info("agent_stopped_cleanly", entity_id=entity_id)
                del self._processes[entity_id]

    def _handle_crash(self, entity_id: str, process: AgentProcess) -> None:
        """Handle a crashed process."""
        restart_state = self._restart_states[entity_id]
        max_attempts = self.config.runner.max_restart_attempts

        if restart_state.attempts >= max_attempts:
            logger.error(
                "max_restarts_exceeded",
                entity_id=entity_id,
                attempts=restart_state.attempts,
            )
            del self._processes[entity_id]
            return

        # Mark for restart with backoff
        process.mark_restarting()
        restart_state.last_restart_time = time.time()

        logger.warning(
            "scheduling_restart",
            entity_id=entity_id,
            backoff_ms=restart_state.next_backoff_ms,
            attempt=restart_state.attempts + 1,
            max_attempts=max_attempts,
        )

    def _maybe_restart(self, entity_id: str, process: AgentProcess) -> None:
        """Restart a process if backoff period has elapsed."""
        restart_state = self._restart_states[entity_id]

        elapsed_ms = (time.time() - restart_state.last_restart_time) * 1000
        if elapsed_ms < restart_state.next_backoff_ms:
            return

        # Restart
        restart_state.attempts += 1
        restart_state.next_backoff_ms = min(
            int(restart_state.next_backoff_ms * self.config.runner.backoff_multiplier),
            self.config.runner.max_backoff_ms,
        )

        logger.info(
            "restarting_agent",
            entity_id=entity_id,
            attempt=restart_state.attempts,
        )

        process.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop all agent processes gracefully.

        Args:
            timeout: Seconds to wait for each process to stop gracefully.
        """
        if not self._processes:
            return

        logger.info("shutting_down", process_count=len(self._processes))
        self._shutdown_requested = True

        for entity_id, process in self._processes.items():
            if process.state == ProcessState.RUNNING:
                process.stop(timeout=timeout)

        self._processes.clear()
        logger.info("shutdown_complete")

    def request_shutdown(self) -> None:
        """Request a graceful shutdown (called from signal handler)."""
        logger.info("shutdown_requested")
        self._shutdown_requested = True
        self._running = False
