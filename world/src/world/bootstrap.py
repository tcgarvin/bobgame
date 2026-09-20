"""Building a running world: from a config's entities and objects, or a save.

`ServerSettings` is everything one run needs; `run_server` turns it into a
`WorldServer` and runs it until it is stopped. The command line that fills a
`ServerSettings` in lives in `cli.py`.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import structlog

from .recording import RunRecorder, file_sha256, generate_run_id
from .save_coordinator import SaveSettings
from .server import DEFAULT_PORT, DEFAULT_WS_PORT, PROJECT_ROOT, WorldServer
from .settlement import find_settlement_site, nearest_free_walkable
from .snapshot import (
    WorldSnapshot,
    check_map,
    load_world_snapshot,
    restore_wolf_simulator,
    restore_world,
)
from .state import DEFAULT_DAY_LENGTH_TICKS, Entity, World, WorldObject
from .tick import TickConfig
from .wolves import WolfSettings

logger = structlog.get_logger()

__all__ = [
    "ServerSettings",
    "load_resume",
    "run_server",
]

# The tick a run that is not a resume was "resumed from": no tick at all.
NO_TICK = -1


@dataclass(frozen=True)
class ServerSettings:
    """Everything one world run needs, whether it is fresh or a resume.

    A fresh run brings `entities` and `objects` to place; a resume brings a
    `snapshot` whose contents are already in `world`, and then places nothing.
    """

    # --- the world ---
    width: int = 100
    height: int = 100
    # A world built by the terrain loader; a bare one is made when it is None.
    world: World | None = None
    entities: Sequence[Entity] = ()
    objects: Sequence[WorldObject] = ()
    # "positions" (each entity's own coordinates) or "settlement" (around the
    # computed settlement centre).
    spawn_mode: str = "positions"
    day_length_ticks: int = DEFAULT_DAY_LENGTH_TICKS
    new_moon_every_days: int = 0

    # --- timing and ports ---
    port: int = DEFAULT_PORT
    ws_port: int = DEFAULT_WS_PORT
    tick_duration_ms: int = 1000
    # 0 means "half the tick duration"; see `deadline_ms`.
    intent_deadline_ms: int = 0

    # --- wolves ---
    wolves: bool = False
    wolf_settings: WolfSettings = WolfSettings()

    # --- recording and saves ---
    # None disables recording entirely.
    run_dir: Path | None = None
    run_id: str = ""
    config_name: str = "default"
    config_path: str = ""
    map_path: str = ""
    save_on_new_moon: bool = True
    save_wait_seconds: int = 180
    # The whole config this run started with, written beside a snapshot.
    run_config: dict[str, Any] = field(default_factory=dict)

    # --- resume ---
    # A loaded snapshot whose entities and objects are already in `world`.
    snapshot: WorldSnapshot | None = None
    parent_run_id: str = ""

    def deadline_ms(self) -> int:
        """The intent deadline within a tick, defaulting to half of it."""
        return self.intent_deadline_ms or self.tick_duration_ms // 2

    def resumed_from_tick(self) -> int:
        """The tick a resume continues from, `NO_TICK` for a fresh run."""
        return self.snapshot.tick if self.snapshot is not None else NO_TICK


def _spawn_at_settlement(server: WorldServer, entities: Sequence[Entity]) -> None:
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


def _register_restored_world(server: WorldServer) -> None:
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


def load_resume(save_dir: Path) -> tuple[WorldSnapshot, World]:
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


def _build_recorder(
    settings: ServerSettings, world: World, tick_config: TickConfig
) -> RunRecorder | None:
    """The run recorder for this run, or None when it records nothing."""
    if settings.run_dir is None:
        return None
    recorder = RunRecorder(
        run_dir=settings.run_dir,
        run_id=settings.run_id or generate_run_id(settings.config_name),
        config_name=settings.config_name,
        config_path=settings.config_path,
        world=world,
        tick_config=tick_config,
        wolves=settings.wolves,
        map_path=settings.map_path,
        project_root=PROJECT_ROOT,
        parent_run_id=settings.parent_run_id,
        resumed_from_tick=settings.resumed_from_tick(),
    )
    logger.info("run_dir", path=str(settings.run_dir), run_id=recorder.run_id)
    return recorder


def _populate(server: WorldServer, settings: ServerSettings) -> None:
    """Put a fresh run's objects and entities into the world."""
    # Objects go in first: settlement spawning needs to know which tiles are
    # occupied by trees and rocks.
    for obj in settings.objects:
        server.add_object(obj)
    if not settings.entities:
        return
    if settings.spawn_mode == "settlement":
        _spawn_at_settlement(server, settings.entities)
    else:
        for entity in settings.entities:
            server.add_entity(entity)


async def run_server(settings: ServerSettings) -> None:
    """Build the world `settings` describes and run it until it is stopped.

    Raises:
        ResumeStartupError: If a resumed run's settlers do not all connect.
    """
    snapshot = settings.snapshot
    world = settings.world
    if world is None:
        world = World(width=settings.width, height=settings.height)
    if snapshot is None:
        # A resume keeps the clock the save was written with.
        world.day_length_ticks = settings.day_length_ticks
        world.new_moon_every_days = settings.new_moon_every_days

    tick_config = TickConfig(
        tick_duration_ms=settings.tick_duration_ms,
        intent_deadline_ms=settings.deadline_ms(),
    )
    recorder = _build_recorder(settings, world, tick_config)

    save_settings = SaveSettings(
        run_dir=settings.run_dir,
        run_id=recorder.run_id if recorder is not None else settings.run_id,
        config=settings.run_config,
        config_name=settings.config_name,
        config_path=settings.config_path,
        map_path=settings.map_path,
        map_sha256=_map_sha256(settings.map_path),
        enabled=settings.save_on_new_moon,
        wait_seconds=settings.save_wait_seconds,
        # The tick a resume starts on already has a save; taking a second one
        # would ask the settlers for files they have already written.
        suppress_tick=settings.resumed_from_tick(),
    )

    server = WorldServer(
        world,
        port=settings.port,
        ws_port=settings.ws_port,
        tick_config=tick_config,
        recorder=recorder,
        wolf_settings=settings.wolf_settings,
        save_settings=save_settings,
    )
    server.tick_loop.wolves_enabled = settings.wolves

    if snapshot is not None:
        restore_wolf_simulator(snapshot, server.tick_loop.wolf_simulator)
        # Everything is already in the world; the chunk manager and the
        # discovery list still have to learn about it.
        _register_restored_world(server)
    else:
        _populate(server, settings)

    logger.info(
        "starting_world_server",
        width=world.width,
        height=world.height,
        port=settings.port,
        ws_port=settings.ws_port,
        tick_duration_ms=settings.tick_duration_ms,
        intent_deadline_ms=tick_config.intent_deadline_ms,
        spawn_mode=settings.spawn_mode,
        wolves=settings.wolves,
        max_wolves=settings.wolf_settings.max_wolves,
        wolf_spawn_min_distance=settings.wolf_settings.spawn_min_distance,
        wolf_spawn_max_distance=settings.wolf_settings.spawn_max_distance,
        wolf_spawn_interval_ticks=settings.wolf_settings.spawn_interval_ticks,
        entities=len(settings.entities),
        objects=len(settings.objects),
        new_moon_every_days=world.new_moon_every_days,
        resumed_from_tick=snapshot.tick if snapshot is not None else None,
    )

    if snapshot is not None:
        await server.run_forever(
            snapshot.settler_ids(), float(settings.save_wait_seconds)
        )
    else:
        await server.run_forever()


def run(settings: ServerSettings) -> None:
    """Run one world server to completion on a fresh event loop."""
    asyncio.run(run_server(settings))
