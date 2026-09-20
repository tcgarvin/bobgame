"""Main gRPC server for the world simulation."""

import asyncio
import signal
from concurrent import futures
from pathlib import Path
from typing import Any

import grpc
import structlog

from . import world_pb2 as pb
from . import world_pb2_grpc
from .lease import LeaseManager
from .recording import RunRecorder, default_run_dir, generate_run_id
from .services import (
    ActionServiceServicer,
    AgentStatusServiceServicer,
    EntityDiscoveryServiceServicer,
    LeaseServiceServicer,
    ObservationServiceServicer,
    TickServiceServicer,
    ViewerWebSocketService,
)
from .settlement import find_settlement_site, nearest_free_walkable
from .state import DEFAULT_DAY_LENGTH_TICKS, Entity, World, WorldObject
from .tick import TickConfig, TickContext, TickLoop, TickResult
from .types import Position
from .wolves import WolfSettings

logger = structlog.get_logger()

# Project root: the parent of the world/ package directory.
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

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
        recorder: RunRecorder | None = None,
        wolf_settings: WolfSettings = WolfSettings(),
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

    async def start(self) -> None:
        """Start the gRPC server and tick loop."""
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

    async def run_forever(self) -> None:
        """Start and run until interrupted.

        SIGINT and SIGTERM stop the tick loop so `stop()` runs and the run
        recorder can close its files and finish `meta.json`.
        """
        await self.start()
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


def _relative_to_root(path: Path) -> str:
    """Path as written relative to the project root, or its absolute form."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def _spawn_at_settlement(server: "WorldServer", entities: list[Entity]) -> None:
    """Place every entity on free walkable tiles around the settlement centre."""
    world = server.world
    site = find_settlement_site(world, world.all_objects().values())
    world.settlement = site
    logger.info("settlement_centre", x=site.x, y=site.y)

    for entity in entities:
        position = nearest_free_walkable(world, site)
        server.add_entity(entity.with_position(position))
        logger.info(
            "entity_spawned_at_settlement",
            entity_id=entity.entity_id,
            x=position.x,
            y=position.y,
        )


async def run_server(
    width: int = 100,
    height: int = 100,
    port: int = DEFAULT_PORT,
    ws_port: int = DEFAULT_WS_PORT,
    tick_duration_ms: int = 1000,
    entities: list[Entity] | None = None,
    objects: list[WorldObject] | None = None,
    world: World | None = None,
    spawn_mode: str = "positions",
    intent_deadline_ms: int | None = None,
    wolves: bool = False,
    wolf_settings: WolfSettings = WolfSettings(),
    run_dir: Path | None = None,
    run_id: str = "",
    config_name: str = "default",
    config_path: str = "",
    map_path: str = "",
    day_length_ticks: int = DEFAULT_DAY_LENGTH_TICKS,
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
        spawn_mode: "positions" (use each entity's coordinates) or "settlement"
            (place every entity near the computed settlement centre)
        intent_deadline_ms: Intent deadline within a tick; defaults to half the tick
        wolves: Whether the world simulates wolves
        wolf_settings: How many wolves the world keeps and how far out they spawn
        run_dir: Directory to record the run into; None disables recording
        run_id: Run id for the recording (generated when empty)
        config_name: Config name, recorded in meta.json
        config_path: Config path relative to the project root, recorded in meta.json
        map_path: Map file path relative to the project root, "" when there is none
        day_length_ticks: Ticks in one day/night cycle (docs/10)
    """
    if world is None:
        world = World(width=width, height=height)
    world.day_length_ticks = day_length_ticks
    config = TickConfig(
        tick_duration_ms=tick_duration_ms,
        intent_deadline_ms=(
            intent_deadline_ms
            if intent_deadline_ms is not None
            else tick_duration_ms // 2
        ),
    )

    recorder: RunRecorder | None = None
    if run_dir is not None:
        recorder = RunRecorder(
            run_dir=run_dir,
            run_id=run_id or generate_run_id(config_name),
            config_name=config_name,
            config_path=config_path,
            world=world,
            tick_config=config,
            wolves=wolves,
            map_path=map_path,
            project_root=PROJECT_ROOT,
        )
        logger.info("run_dir", path=str(run_dir), run_id=recorder.run_id)

    server = WorldServer(
        world,
        port=port,
        ws_port=ws_port,
        tick_config=config,
        recorder=recorder,
        wolf_settings=wolf_settings,
    )

    # The mechanics track owns the wolf simulation; it reads this flag off the
    # tick loop. Set defensively so the two tracks can land independently.
    # See docs/05_jev_agents_design.md "Deviations".
    setattr(server.tick_loop, "wolves_enabled", wolves)

    # Objects go in first: settlement spawning needs to know which tiles are
    # occupied by trees and rocks.
    if objects:
        for obj in objects:
            server.add_object(obj)

    if entities:
        if spawn_mode == "settlement":
            _spawn_at_settlement(server, entities)
        else:
            for entity in entities:
                server.add_entity(entity)

    logger.info(
        "starting_world_server",
        width=width,
        height=height,
        port=port,
        ws_port=ws_port,
        tick_duration_ms=tick_duration_ms,
        intent_deadline_ms=config.intent_deadline_ms,
        spawn_mode=spawn_mode,
        wolves=wolves,
        max_wolves=wolf_settings.max_wolves,
        wolf_spawn_min_distance=wolf_settings.spawn_min_distance,
        wolf_spawn_max_distance=wolf_settings.spawn_max_distance,
        wolf_spawn_interval_ticks=wolf_settings.spawn_interval_ticks,
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
        "--ws-port",
        type=int,
        default=DEFAULT_WS_PORT,
        help="WebSocket port for viewers",
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
        "--run-dir",
        type=str,
        default="",
        help="Directory to record this run into "
        "(default: $BOBGAME_RUN_DIR or <project_root>/runs/<run id>)",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="Do not record this run",
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
    config_name = "default"
    config_rel_path = ""
    if args.config:
        try:
            config_path = find_config(args.config)
            config = load_config(config_path)
            logger.info("config_loaded", path=str(config_path))
            config_name = config_path.stem
            config_rel_path = _relative_to_root(config_path)
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
            save_path = PROJECT_ROOT / map_save_path

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

        save_path = PROJECT_ROOT / map_save_path

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

    # Recording destination
    run_id = generate_run_id(config_name)
    run_dir: Path | None = None
    if not args.no_record:
        run_dir = (
            Path(args.run_dir)
            if args.run_dir
            else default_run_dir(PROJECT_ROOT, run_id)
        )
        logger.info("recording_run", run_id=run_id, run_dir=str(run_dir))
    else:
        logger.info("recording_disabled")

    # The map path recorded in meta is relative to the project root, as the
    # config writes it; only a map that actually exists is recorded.
    recorded_map_path = ""
    if map_save_path and (PROJECT_ROOT / map_save_path).exists():
        recorded_map_path = map_save_path

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
            spawn_mode=config.world.spawn_mode,
            intent_deadline_ms=config.world.intent_deadline_ms,
            wolves=config.world.wolves,
            wolf_settings=config.world.wolf_settings(),
            run_dir=run_dir,
            run_id=run_id,
            config_name=config_name,
            config_path=config_rel_path,
            map_path=recorded_map_path,
            day_length_ticks=config.world.day_length_ticks,
        )
    )


if __name__ == "__main__":
    main()
