"""Main gRPC server for the world simulation."""

import asyncio
import signal
from concurrent import futures
from pathlib import Path
from typing import Any, Sequence

import grpc
import structlog

from . import world_pb2 as pb
from . import world_pb2_grpc
from .exceptions import ResumeStartupError
from .lease import LeaseManager
from .recording import RunRecorder, default_run_dir, file_sha256, generate_run_id
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
from .settlement import find_settlement_site, nearest_free_walkable
from .snapshot import (
    SnapshotError,
    WorldSnapshot,
    check_map,
    load_world_snapshot,
    restore_wolf_simulator,
    restore_world,
)
from .state import DEFAULT_DAY_LENGTH_TICKS, Entity, World, WorldObject
from .tick import TickConfig, TickContext, TickLoop, TickResult
from .types import Position
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


def _map_sha256(map_path: str) -> str:
    """Hex sha256 of the run's map file, "" when there is none to hash."""
    if not map_path:
        return ""
    map_file = PROJECT_ROOT / map_path
    try:
        return file_sha256(map_file)
    except OSError as exc:
        logger.warning("map_sha256_failed", path=str(map_file), error=str(exc))
        return ""


def _register_restored_world(server: "WorldServer") -> None:
    """Tell the chunk manager and discovery list about a restored world."""
    chunks = server.viewer_ws_service.chunk_manager
    for obj in server.world.all_objects().values():
        chunks.add_object(obj.object_id, obj.position)
    for entity in server.world.all_entities().values():
        server.discovery_service.register_entity_spawn(
            entity.entity_id, server.world.tick
        )
        chunks.add_entity(entity.entity_id, entity.position)
    logger.info(
        "restored_world_registered",
        objects=server.world.object_count(),
        entities=server.world.entity_count(),
    )


def _load_resume(save_dir: Path) -> tuple[WorldSnapshot, World]:
    """Load a save: check the map, build the world from it, restore the state.

    Returns the snapshot and the world it was restored into.

    Raises:
        SnapshotError: If the save is incomplete, stale or unreadable.
        FileNotFoundError: If the snapshot's map file is gone.
    """
    from .terrain import load_world

    snapshot = load_world_snapshot(save_dir)
    check_map(snapshot, PROJECT_ROOT)
    if snapshot.map_path:
        world, _terrain_objects = load_world(PROJECT_ROOT / snapshot.map_path)
    else:
        world = World(width=snapshot.width, height=snapshot.height)
    restore_world(snapshot, save_dir, world)
    logger.info(
        "resumed_from_snapshot",
        path=str(save_dir),
        tick=snapshot.tick,
        entities=len(snapshot.entities),
        objects=world.object_count(),
    )
    return snapshot, world


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
    new_moon_every_days: int = 0,
    save_on_new_moon: bool = True,
    save_wait_seconds: int = 180,
    run_config: dict[str, Any] | None = None,
    resume: WorldSnapshot | None = None,
    parent_run_id: str = "",
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
        new_moon_every_days: How often a new-moon night falls; 0 for never (docs/14)
        save_on_new_moon: Whether a new-moon night also writes a save
        save_wait_seconds: How long a save waits for the settlers' files
        run_config: The full config this run was started with, saved with a snapshot
        resume: A loaded snapshot whose entities and objects are already in
            `world`; its wolf RNG is restored and its tick is held for the agents
        parent_run_id: The run a resume continues, recorded in meta.json

    Raises:
        ResumeStartupError: If a resumed run's settlers do not all connect.
    """
    if world is None:
        world = World(width=width, height=height)
    if resume is None:
        world.day_length_ticks = day_length_ticks
        world.new_moon_every_days = new_moon_every_days
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
            parent_run_id=parent_run_id,
            resumed_from_tick=resume.tick if resume is not None else -1,
        )
        logger.info("run_dir", path=str(run_dir), run_id=recorder.run_id)

    save_settings = SaveSettings(
        run_dir=run_dir,
        run_id=recorder.run_id if recorder is not None else run_id,
        config=run_config or {},
        config_name=config_name,
        config_path=config_path,
        map_path=map_path,
        map_sha256=_map_sha256(map_path),
        enabled=save_on_new_moon,
        wait_seconds=save_wait_seconds,
        # The tick a resume starts on already has a save; taking a second one
        # would ask the settlers for files they have already written.
        suppress_tick=resume.tick if resume is not None else -1,
    )

    server = WorldServer(
        world,
        port=port,
        ws_port=ws_port,
        tick_config=config,
        recorder=recorder,
        wolf_settings=wolf_settings,
        save_settings=save_settings,
    )

    if resume is not None:
        restore_wolf_simulator(resume, server.tick_loop.wolf_simulator)

    # The mechanics track owns the wolf simulation; it reads this flag off the
    # tick loop. Set defensively so the two tracks can land independently.
    # See docs/05_jev_agents_design.md "Deviations".
    setattr(server.tick_loop, "wolves_enabled", wolves)

    if resume is not None:
        # Everything is already in the world; the chunk manager and the
        # discovery list still have to learn about it.
        _register_restored_world(server)
    else:
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
        new_moon_every_days=world.new_moon_every_days,
        resumed_from_tick=resume.tick if resume is not None else None,
    )

    if resume is not None:
        await server.run_forever(resume.settler_ids(), float(save_wait_seconds))
    else:
        await server.run_forever()


def _configure_logging() -> None:
    """Console logging at INFO for the CLI."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    )


def _main_resume(args: Any) -> None:
    """Continue a saved run: everything comes from the save (docs/14, section 4).

    The config in the snapshot wins over the TOML on disk, which may have
    changed since the run started.

    Raises:
        SystemExit: With status 1 when the save cannot be used or the settlers
            never reconnect.
    """
    from .config import Config

    save_dir = Path(args.resume)
    try:
        snapshot, world = _load_resume(save_dir)
    except (SnapshotError, FileNotFoundError, ValueError) as exc:
        logger.error("resume_failed", path=str(save_dir), error=str(exc))
        raise SystemExit(1)

    config = Config.model_validate(snapshot.config)
    config_name = snapshot.config_name or DEFAULT_CONFIG
    run_id = generate_run_id(config_name)
    run_dir = (
        Path(args.run_dir) if args.run_dir else default_run_dir(PROJECT_ROOT, run_id)
    )
    logger.info(
        "resuming_run",
        run_id=run_id,
        run_dir=str(run_dir),
        parent_run_id=args.parent_run_id or snapshot.run_id,
        tick=snapshot.tick,
    )

    try:
        asyncio.run(
            run_server(
                width=world.width,
                height=world.height,
                port=args.port,
                ws_port=args.ws_port,
                tick_duration_ms=config.world.tick_duration_ms,
                world=world,
                intent_deadline_ms=config.world.intent_deadline_ms,
                wolves=config.world.wolves,
                wolf_settings=config.world.wolf_settings(),
                run_dir=run_dir,
                run_id=run_id,
                config_name=config_name,
                config_path=snapshot.config_path,
                map_path=snapshot.map_path,
                run_config=snapshot.config,
                save_on_new_moon=config.world.save_on_new_moon,
                save_wait_seconds=config.world.save_wait_seconds,
                resume=snapshot,
                parent_run_id=args.parent_run_id or snapshot.run_id,
            )
        )
    except ResumeStartupError as exc:
        logger.error("resume_startup_failed", error=str(exc))
        raise SystemExit(1)


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
        default=DEFAULT_CONFIG,
        help=f"Config name or path (default: {DEFAULT_CONFIG}, "
        f"available: {', '.join(list_configs())})",
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
        "--resume",
        type=str,
        default="",
        help="Resume from a save directory (runs/<run>/saves/tick-<T>); the "
        "world, its config and the map all come from the save (docs/14)",
    )
    parser.add_argument(
        "--parent-run-id",
        type=str,
        default="",
        help="Run id a --resume continues, recorded in the new meta.json",
    )
    parser.add_argument(
        "--spawn-bush",
        type=str,
        nargs="*",
        default=[],
        help="Spawn bush at x,y (e.g., 'bush1:3,3') - adds to config objects",
    )

    args = parser.parse_args()

    _configure_logging()

    if args.resume:
        _main_resume(args)
        return

    # Load the named config. There is no built-in fallback world: a config
    # that cannot be found is an error naming what was looked for.
    try:
        config_path = find_config(args.config)
    except FileNotFoundError as e:
        parser.error(str(e))
    config = load_config(config_path)
    logger.info("config_loaded", path=str(config_path))
    config_name = config_path.stem
    config_rel_path = _relative_to_root(config_path)

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
    run_dir = (
        Path(args.run_dir) if args.run_dir else default_run_dir(PROJECT_ROOT, run_id)
    )
    logger.info("recording_run", run_id=run_id, run_dir=str(run_dir))

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
            new_moon_every_days=config.world.new_moon_every_days,
            save_on_new_moon=config.world.save_on_new_moon,
            save_wait_seconds=config.world.save_wait_seconds,
            run_config=config.model_dump(),
        )
    )


if __name__ == "__main__":
    main()
