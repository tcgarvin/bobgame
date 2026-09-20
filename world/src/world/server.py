"""`WorldServer`: the gRPC and WebSocket services wired to one tick loop.

Building a world from a config or a save lives in `bootstrap.py`, and the
command line in `cli.py`; `python -m world.server` still runs the latter.
"""

import asyncio
import signal
from concurrent import futures
from pathlib import Path
from typing import Any, Sequence

import grpc
import structlog

from . import world_pb2_grpc
from .exceptions import ResumeStartupError
from .lease import LeaseManager
from .recording import RunRecorder
from .save_coordinator import SaveCoordinator, SaveSettings
from .services import (
    ActionServiceServicer,
    AgentStatusServiceServicer,
    EntityDiscoveryServiceServicer,
    LeaseServiceServicer,
    ObservationServiceServicer,
    TickServiceServicer,
    ViewerWebSocketService,
)
from .state import Entity, World, WorldObject
from .tick import TickConfig, TickContext, TickLoop, TickResult
from .wolves import WolfSettings

logger = structlog.get_logger()

# How often a resuming server looks for the settlers' observation streams.
OBSERVER_POLL_INTERVAL_S = 0.25

# Project root: the parent of the world/ package directory.
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# Default ports
# The project ships one scenario (world/configs/hamlet.toml).
DEFAULT_CONFIG = "hamlet"
DEFAULT_PORT = 50051
DEFAULT_WS_PORT = 8765


class WorldServer:
    """Main server coordinating the world simulation and gRPC services."""

    def __init__(
        self,
        world: World,
        port: int = DEFAULT_PORT,
        ws_port: int = DEFAULT_WS_PORT,
        tick_config: TickConfig | None = None,
        recorder: RunRecorder | None = None,
        wolf_settings: WolfSettings = WolfSettings(),
        save_settings: SaveSettings | None = None,
    ):
        self.world = world
        self.port = port
        self.ws_port = ws_port
        self.tick_config = tick_config or TickConfig()
        self.recorder = recorder

        # Core components
        self.lease_manager = LeaseManager()
        self.tick_loop = TickLoop(
            world,
            config=self.tick_config,
            on_tick_complete=self._on_tick_complete,
            on_tick_start=self._on_tick_start,
            wolf_settings=wolf_settings,
        )
        # The save coordinator needs the tick loop's wolf simulator, so it is
        # built here rather than handed in (docs/14).
        if save_settings is not None:
            self.tick_loop.save_coordinator = SaveCoordinator(
                world,
                self.tick_loop.wolf_simulator,
                save_settings,
            )

        # gRPC services
        self.tick_service = TickServiceServicer(self.tick_loop)
        self.lease_service = LeaseServiceServicer(world, self.lease_manager)
        self.action_service = ActionServiceServicer(self.tick_loop, self.lease_manager)
        self.observation_service = ObservationServiceServicer(
            world, self.tick_loop, self.lease_manager
        )
        self.discovery_service = EntityDiscoveryServiceServicer(
            world, self.lease_manager
        )

        # Viewer WebSocket service
        self.viewer_ws_service = ViewerWebSocketService(
            world,
            self.tick_config,
            port=ws_port,
            run_id=recorder.run_id if recorder is not None else "",
        )

        # Agent status reports go to viewer clients and, when recording, to
        # the run's tick stream.
        self.status_service = AgentStatusServiceServicer(
            self.lease_manager,
            self.viewer_ws_service.broadcast_event,
            self._record_agent_status,
        )

        # gRPC server
        self._server: grpc.Server | None = None
        self._tick_task: asyncio.Task | None = None

    def _record_agent_status(self, status: dict[str, Any]) -> None:
        """Append an accepted agent status report to the run recording."""
        if self.recorder is not None:
            self.recorder.record_agent_status(status)

    async def _on_tick_start(self, context: TickContext) -> None:
        """Called at the start of each tick, before deadline."""
        # Broadcast tick event to tick subscribers
        tick_event = self.tick_service.create_tick_event()
        self.tick_service.broadcast_tick(tick_event)

        # Broadcast observations to observation subscribers
        # Agents can now submit intents for this tick
        self.observation_service.broadcast_observations(context)

        # Broadcast to viewer WebSocket clients
        self.viewer_ws_service.on_tick_start(context)

        logger.debug(
            "tick_start_broadcast",
            tick_id=context.tick_id,
            deadline_ms=context.deadline_ms,
        )

    async def _on_tick_complete(self, result: TickResult) -> None:
        """Called after each tick completes."""
        # Cleanup expired leases periodically
        self.lease_manager.cleanup_expired()

        # Hand the finished tick's events to the observation service so the
        # next tick's observations can replay them.
        self.observation_service.on_tick_complete(result)

        # Broadcast to viewer WebSocket clients
        self.viewer_ws_service.on_tick_complete(result)

        # Append the tick to the run recording
        if self.recorder is not None:
            self.recorder.record_tick(result)

        logger.debug(
            "tick_complete",
            tick_id=result.tick_id,
            moves=len(result.move_results),
            duration_ms=result.duration_ms,
        )

    def add_entity(self, entity: Entity) -> None:
        """Add an entity to the world, the chunk index and the discovery list."""
        self.world.add_entity(entity)
        self.discovery_service.register_entity_spawn(entity.entity_id, self.world.tick)
        self.viewer_ws_service.chunk_manager.add_entity(
            entity.entity_id, entity.position
        )

    def add_object(self, obj: WorldObject) -> None:
        """Add an object to the world and register with chunk manager."""
        self.world.add_object(obj)
        # Register with chunk manager so it gets sent to viewers
        self.viewer_ws_service.chunk_manager.add_object(obj.object_id, obj.position)

    def restore_entity(self, entity: Entity) -> None:
        """Put a snapshot's entity back, placed if alive and detached if dead.

        Like `add_entity`, but it never raises on two dead settlers that share
        the tile they died on: dead entities hold no tile (docs/14).
        """
        if entity.alive:
            self.world.add_entity(entity)
        else:
            self.world.add_entity_unplaced(entity)
        self.discovery_service.register_entity_spawn(entity.entity_id, self.world.tick)
        self.viewer_ws_service.chunk_manager.add_entity(
            entity.entity_id, entity.position
        )

    async def wait_for_observers(
        self, entity_ids: Sequence[str], timeout_s: float
    ) -> None:
        """Block until every named entity has an observation stream open.

        A resumed run holds its first tick until the settlers are back, so
        none of them misses the tick they were saved on (docs/14, section 4).

        Raises:
            ResumeStartupError: If some of them never connected in time.
        """
        wanted = set(entity_ids)
        if not wanted:
            return
        loop = asyncio.get_running_loop()
        give_up_at = loop.time() + timeout_s
        while True:
            missing = sorted(wanted - self.observation_service.subscriber_ids())
            if not missing:
                logger.info("resume_observers_ready", entities=len(wanted))
                return
            if loop.time() >= give_up_at:
                raise ResumeStartupError(
                    "These settlers never opened an observation stream within "
                    f"{timeout_s:.0f}s: {', '.join(missing)}"
                )
            await asyncio.sleep(OBSERVER_POLL_INTERVAL_S)

    async def start(
        self,
        wait_for_entities: Sequence[str] = (),
        wait_timeout_s: float = 0.0,
    ) -> None:
        """Start the gRPC server and tick loop.

        Args:
            wait_for_entities: Entity ids whose agents must be connected before
                the first tick runs (a resume; empty for a fresh run).
            wait_timeout_s: How long to wait for them.

        Raises:
            ResumeStartupError: If `wait_for_entities` do not all connect.
        """
        if self.recorder is not None:
            self.recorder.start()

        # Create gRPC server with thread pool for handling requests
        # Every observation stream holds a worker thread for its lifetime, so
        # the pool must exceed the number of agents or unary RPCs starve.
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=64))

        # Register all services
        world_pb2_grpc.add_TickServiceServicer_to_server(
            self.tick_service, self._server
        )
        world_pb2_grpc.add_LeaseServiceServicer_to_server(
            self.lease_service, self._server
        )
        world_pb2_grpc.add_ActionServiceServicer_to_server(
            self.action_service, self._server
        )
        world_pb2_grpc.add_ObservationServiceServicer_to_server(
            self.observation_service, self._server
        )
        world_pb2_grpc.add_EntityDiscoveryServiceServicer_to_server(
            self.discovery_service, self._server
        )
        world_pb2_grpc.add_AgentStatusServiceServicer_to_server(
            self.status_service, self._server
        )

        # Bind to port
        self._server.add_insecure_port(f"[::]:{self.port}")

        # Start server
        self._server.start()
        logger.info("grpc_server_started", port=self.port)

        # Start WebSocket server for viewers
        await self.viewer_ws_service.start()

        # A resumed run holds tick T until every settler is back.
        if wait_for_entities:
            logger.info(
                "resume_waiting_for_observers",
                entities=len(wait_for_entities),
                timeout_s=wait_timeout_s,
            )
            await self.wait_for_observers(wait_for_entities, wait_timeout_s)

        # Start tick loop
        self._tick_task = asyncio.create_task(self.tick_loop.run())
        logger.info("tick_loop_started")

    async def stop(self, grace_period: float = 5.0) -> None:
        """Stop the server and tick loop."""
        # Stop tick loop
        if self._tick_task:
            self.tick_loop.stop()
            try:
                await asyncio.wait_for(self._tick_task, timeout=grace_period)
            except asyncio.TimeoutError:
                self._tick_task.cancel()

        # Stop WebSocket server
        await self.viewer_ws_service.stop()

        # Stop gRPC server
        if self._server:
            self._server.stop(grace_period)
            logger.info("grpc_server_stopped")

        # Finish the recording (updates meta.json with finished_at/last_tick)
        if self.recorder is not None:
            self.recorder.close()

    async def run_forever(
        self,
        wait_for_entities: Sequence[str] = (),
        wait_timeout_s: float = 0.0,
    ) -> None:
        """Start and run until interrupted.

        SIGINT and SIGTERM stop the tick loop so `stop()` runs and the run
        recorder can close its files and finish `meta.json`.

        Raises:
            ResumeStartupError: If a resume's settlers do not all connect.
        """
        await self.start(wait_for_entities, wait_timeout_s)
        loop = asyncio.get_running_loop()
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signal_number, self.tick_loop.stop)
            except (NotImplementedError, RuntimeError):
                # Not on the main thread (tests) or not a Unix loop.
                pass
        try:
            # Wait for tick loop to complete (runs until stopped)
            if self._tick_task:
                await self._tick_task
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()


if __name__ == "__main__":  # pragma: no cover - `python -m world.server`
    from .cli import main

    main()
