"""Main gRPC server for the world simulation."""

import asyncio
from concurrent import futures
from pathlib import Path

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
    ViewerWebSocketService,
)
from .state import Entity, World, WorldObject
from .tick import TickConfig, TickContext, TickLoop, TickResult
from .types import Position

logger = structlog.get_logger()

# Default ports
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
    ):
        self.world = world
        self.port = port
        self.ws_port = ws_port
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

        # Viewer WebSocket service
        self.viewer_ws_service = ViewerWebSocketService(
            world, self.tick_config, port=ws_port
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

        # Broadcast to viewer WebSocket clients
        self.viewer_ws_service.on_tick_complete(result)

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

    def add_object(self, obj: WorldObject) -> None:
        """Add an object to the world."""
        self.world.add_object(obj)

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

        # Start WebSocket server for viewers
        await self.viewer_ws_service.start()

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
    ws_port: int = DEFAULT_WS_PORT,
    tick_duration_ms: int = 1000,
    entities: list[Entity] | None = None,
    objects: list[WorldObject] | None = None,
    world: World | None = None,
) -> None:
    """Run a world server with the given configuration.

    Args:
        width: World width in tiles
        height: World height in tiles
        port: gRPC port to listen on
        ws_port: WebSocket port for viewer connections
        tick_duration_ms: Duration of each tick in milliseconds
        entities: Initial entities to add to the world
        objects: Initial objects (bushes, etc.) to add to the world
        world: Pre-created world (overrides width/height if provided)
    """
    if world is None:
        world = World(width=width, height=height)
    config = TickConfig(
        tick_duration_ms=tick_duration_ms,
        intent_deadline_ms=tick_duration_ms // 2,
    )

    server = WorldServer(world, port=port, ws_port=ws_port, tick_config=config)

    # Add initial entities
    if entities:
        for entity in entities:
            server.add_entity(entity)

    # Add initial objects
    if objects:
        for obj in objects:
            server.add_object(obj)

    logger.info(
        "starting_world_server",
        width=width,
        height=height,
        port=port,
        ws_port=ws_port,
        tick_duration_ms=tick_duration_ms,
        entities=len(entities) if entities else 0,
        objects=len(objects) if objects else 0,
    )

    await server.run_forever()


def main() -> None:
    """CLI entry point for the world server."""
    import argparse

    from .config import (
        config_to_entities,
        config_to_objects,
        find_config,
        list_configs,
        load_config,
    )

    parser = argparse.ArgumentParser(description="Bob's World Server")
    parser.add_argument(
        "--config",
        type=str,
        help=f"Config name or path (available: {', '.join(list_configs())})",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="gRPC port")
    parser.add_argument(
        "--ws-port", type=int, default=DEFAULT_WS_PORT, help="WebSocket port for viewers"
    )
    parser.add_argument("--width", type=int, help="World width (overrides config)")
    parser.add_argument("--height", type=int, help="World height (overrides config)")
    parser.add_argument(
        "--tick-duration", type=int, help="Tick duration in ms (overrides config)"
    )
    parser.add_argument(
        "--spawn-entity",
        type=str,
        nargs="*",
        default=[],
        help="Spawn entity at x,y (e.g., 'bob:5,5') - adds to config entities",
    )
    parser.add_argument(
        "--spawn-bush",
        type=str,
        nargs="*",
        default=[],
        help="Spawn bush at x,y (e.g., 'bush1:3,3') - adds to config objects",
    )

    args = parser.parse_args()

    # Load config if specified
    if args.config:
        try:
            config_path = find_config(args.config)
            config = load_config(config_path)
            logger.info("config_loaded", path=str(config_path))
        except FileNotFoundError as e:
            parser.error(str(e))
    else:
        # Default config: small 10x10 world
        from .config import Config, WorldConfig

        config = Config(world=WorldConfig(width=10, height=10))

    # Apply CLI overrides
    width = args.width if args.width is not None else config.world.width
    height = args.height if args.height is not None else config.world.height
    tick_duration = (
        args.tick_duration
        if args.tick_duration is not None
        else config.world.tick_duration_ms
    )

    # Start with entities/objects from config
    entities = config_to_entities(config)
    objects = config_to_objects(config)

    # Add CLI-specified entities
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

    # Add CLI-specified bushes
    for spawn in args.spawn_bush:
        if ":" not in spawn:
            parser.error(f"Invalid spawn format: {spawn} (expected 'id:x,y')")
        object_id, coords = spawn.split(":", 1)
        if "," not in coords:
            parser.error(f"Invalid coords format: {coords} (expected 'x,y')")
        x, y = coords.split(",", 1)
        objects.append(
            WorldObject(
                object_id=object_id,
                position=Position(x=int(x), y=int(y)),
                object_type="bush",
                state=(("berry_count", "1"),),  # Binary state: has berry
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

    # Handle terrain generation mode
    world = None
    generation_mode = getattr(config.world, "generation_mode", "empty")
    map_save_path = getattr(config.world, "map_save_path", None)

    if generation_mode == "generate":
        from .terrain import (
            TerrainConfig,
            generate_and_save_world,
            generate_world,
            load_world,
        )

        terrain_seed = getattr(config.world, "terrain_seed", None) or 42

        # Resolve save path relative to project root
        if map_save_path:
            # Find project root (parent of world/ directory)
            project_root = Path(__file__).parent.parent.parent.parent
            save_path = project_root / map_save_path

            if save_path.exists():
                # Load existing map
                logger.info(
                    "loading_saved_map",
                    path=str(save_path),
                )
                world, terrain_objects = load_world(save_path)
                objects.extend(terrain_objects)

                logger.info(
                    "map_loaded",
                    tiles=width * height,
                    objects=len(terrain_objects),
                )
            else:
                # Generate and save new map
                terrain_config = TerrainConfig(
                    seed=terrain_seed, width=width, height=height
                )

                logger.info(
                    "generating_terrain",
                    width=width,
                    height=height,
                    seed=terrain_seed,
                    save_path=str(save_path),
                )

                world, terrain_objects = generate_and_save_world(
                    terrain_config, save_path
                )
                objects.extend(terrain_objects)

                logger.info(
                    "terrain_generated_and_saved",
                    tiles=width * height,
                    objects=len(terrain_objects),
                    save_path=str(save_path),
                )
        else:
            # No save path, just generate without saving
            terrain_config = TerrainConfig(
                seed=terrain_seed, width=width, height=height
            )

            logger.info(
                "generating_terrain",
                width=width,
                height=height,
                seed=terrain_seed,
            )

            world, terrain_objects = generate_world(terrain_config)
            objects.extend(terrain_objects)

            logger.info(
                "terrain_generated",
                tiles=width * height,
                objects=len(terrain_objects),
            )

    elif generation_mode == "load":
        from .terrain import load_world

        if not map_save_path:
            parser.error("generation_mode='load' requires map_save_path to be set")

        project_root = Path(__file__).parent.parent.parent.parent
        save_path = project_root / map_save_path

        logger.info(
            "loading_saved_map",
            path=str(save_path),
        )

        world, terrain_objects = load_world(save_path)
        objects.extend(terrain_objects)

        logger.info(
            "map_loaded",
            tiles=width * height,
            objects=len(terrain_objects),
        )

    asyncio.run(
        run_server(
            width=width,
            height=height,
            port=args.port,
            ws_port=args.ws_port,
            tick_duration_ms=tick_duration,
            entities=entities,
            objects=objects,
            world=world,
        )
    )


if __name__ == "__main__":
    main()
