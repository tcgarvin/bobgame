"""Main gRPC server for the world simulation."""

import asyncio
from concurrent import futures

import grpc
import structlog

from . import world_pb2 as pb
from . import world_pb2_grpc
from .lease import LeaseManager
from .services import (
    ActionServiceServicer,
    EntityDiscoveryServiceServicer,
    LeaseServiceServicer,
    ObservationServiceServicer,
    TickServiceServicer,
)
from .state import Entity, World
from .tick import TickConfig, TickContext, TickLoop, TickResult
from .types import Position

logger = structlog.get_logger()

# Default port for world server
DEFAULT_PORT = 50051


class WorldServer:
    """Main server coordinating the world simulation and gRPC services."""

    def __init__(
        self,
        world: World,
        port: int = DEFAULT_PORT,
        tick_config: TickConfig | None = None,
    ):
        self.world = world
        self.port = port
        self.tick_config = tick_config or TickConfig()

        # Core components
        self.lease_manager = LeaseManager()
        self.tick_loop = TickLoop(
            world,
            config=self.tick_config,
            on_tick_complete=self._on_tick_complete,
            on_tick_start=self._on_tick_start,
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

        # gRPC server
        self._server: grpc.Server | None = None
        self._tick_task: asyncio.Task | None = None

    async def _on_tick_start(self, context: TickContext) -> None:
        """Called at the start of each tick, before deadline."""
        # Broadcast tick event to tick subscribers
        tick_event = self.tick_service.create_tick_event()
        self.tick_service.broadcast_tick(tick_event)

        # Broadcast observations to observation subscribers
        # Agents can now submit intents for this tick
        self.observation_service.broadcast_observations(context)

        logger.debug(
            "tick_start_broadcast",
            tick_id=context.tick_id,
            deadline_ms=context.deadline_ms,
        )

    async def _on_tick_complete(self, result: TickResult) -> None:
        """Called after each tick completes."""
        # Cleanup expired leases periodically
        self.lease_manager.cleanup_expired()

        logger.debug(
            "tick_complete",
            tick_id=result.tick_id,
            moves=len(result.move_results),
            duration_ms=result.duration_ms,
        )

    def add_entity(self, entity: Entity) -> None:
        """Add an entity to the world and register its spawn tick."""
        self.world.add_entity(entity)
        self.discovery_service.register_entity_spawn(entity.entity_id, self.world.tick)

    async def start(self) -> None:
        """Start the gRPC server and tick loop."""
        # Create gRPC server with thread pool for handling requests
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

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

        # Bind to port
        self._server.add_insecure_port(f"[::]:{self.port}")

        # Start server
        self._server.start()
        logger.info("grpc_server_started", port=self.port)

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

        # Stop gRPC server
        if self._server:
            self._server.stop(grace_period)
            logger.info("grpc_server_stopped")

    async def run_forever(self) -> None:
        """Start and run until interrupted."""
        await self.start()
        try:
            # Wait for tick loop to complete (runs until stopped)
            if self._tick_task:
                await self._tick_task
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()


async def run_server(
    width: int = 100,
    height: int = 100,
    port: int = DEFAULT_PORT,
    tick_duration_ms: int = 1000,
    entities: list[Entity] | None = None,
) -> None:
    """Run a world server with the given configuration.

    Args:
        width: World width in tiles
        height: World height in tiles
        port: gRPC port to listen on
        tick_duration_ms: Duration of each tick in milliseconds
        entities: Initial entities to add to the world
    """
    world = World(width=width, height=height)
    config = TickConfig(
        tick_duration_ms=tick_duration_ms,
        intent_deadline_ms=tick_duration_ms // 2,
    )

    server = WorldServer(world, port=port, tick_config=config)

    # Add initial entities
    if entities:
        for entity in entities:
            server.add_entity(entity)

    logger.info(
        "starting_world_server",
        width=width,
        height=height,
        port=port,
        tick_duration_ms=tick_duration_ms,
        entities=len(entities) if entities else 0,
    )

    await server.run_forever()


def main() -> None:
    """CLI entry point for the world server."""
    import argparse

    parser = argparse.ArgumentParser(description="Bob's World Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="gRPC port")
    parser.add_argument("--width", type=int, default=100, help="World width")
    parser.add_argument("--height", type=int, default=100, help="World height")
    parser.add_argument(
        "--tick-duration", type=int, default=1000, help="Tick duration in ms"
    )
    parser.add_argument(
        "--spawn-entity",
        type=str,
        nargs="*",
        default=[],
        help="Spawn entity at x,y (e.g., 'bob:5,5')",
    )

    args = parser.parse_args()

    # Parse entity spawns
    entities = []
    for spawn in args.spawn_entity:
        if ":" not in spawn:
            parser.error(f"Invalid spawn format: {spawn} (expected 'id:x,y')")
        entity_id, coords = spawn.split(":", 1)
        if "," not in coords:
            parser.error(f"Invalid coords format: {coords} (expected 'x,y')")
        x, y = coords.split(",", 1)
        entities.append(
            Entity(
                entity_id=entity_id,
                position=Position(x=int(x), y=int(y)),
                entity_type="player",
            )
        )

    # Configure structlog for CLI
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    )

    asyncio.run(
        run_server(
            width=args.width,
            height=args.height,
            port=args.port,
            tick_duration_ms=args.tick_duration,
            entities=entities,
        )
    )


if __name__ == "__main__":
    main()
