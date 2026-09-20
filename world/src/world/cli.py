"""The `python -m world.server` command line.

It reads a config (or a save) and fills in a `bootstrap.ServerSettings`; every
decision about what the world contains is made here, and nothing else in the
package knows about argparse.
"""

import argparse
from pathlib import Path
from typing import Sequence

import structlog

from .bootstrap import ServerSettings, load_resume, run
from .config import Config, WorldConfig
from .exceptions import ResumeStartupError
from .recording import default_run_dir, run_id_for
from .server import DEFAULT_CONFIG, DEFAULT_PORT, DEFAULT_WS_PORT, PROJECT_ROOT
from .snapshot import SnapshotError
from .state import Entity, World, WorldObject
from .types import Position

logger = structlog.get_logger()

__all__ = ["main"]


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


def _relative_to_root(path: Path) -> str:
    """Path as written relative to the project root, or its absolute form."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def _build_parser() -> argparse.ArgumentParser:
    """The world server's arguments."""
    from .config import list_configs

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
    return parser


def _parse_spawn(parser: argparse.ArgumentParser, spawn: str) -> tuple[str, Position]:
    """Split an `id:x,y` spawn argument, exiting with a message when malformed."""
    if ":" not in spawn:
        parser.error(f"Invalid spawn format: {spawn} (expected 'id:x,y')")
    name, coords = spawn.split(":", 1)
    if "," not in coords:
        parser.error(f"Invalid coords format: {coords} (expected 'x,y')")
    x, y = coords.split(",", 1)
    return name, Position(x=int(x), y=int(y))


def _run_dir_for(run_id: str, requested: str) -> Path:
    """Where this run records: what was asked for, else the default."""
    return Path(requested) if requested else default_run_dir(PROJECT_ROOT, run_id)


def _load_terrain(
    parser: argparse.ArgumentParser,
    world_config: WorldConfig,
    width: int,
    height: int,
) -> tuple[World | None, list[WorldObject]]:
    """Build or load the map this config asks for, with its natural objects.

    Returns (None, []) for `generation_mode = "empty"`, which is a bare world.
    """
    mode = world_config.generation_mode
    map_save_path = world_config.map_save_path

    if mode == "load":
        from .terrain import load_world

        if not map_save_path:
            parser.error("generation_mode='load' requires map_save_path to be set")
        save_path = PROJECT_ROOT / map_save_path
        logger.info("loading_saved_map", path=str(save_path))
        world, terrain_objects = load_world(save_path)
        logger.info("map_loaded", tiles=width * height, objects=len(terrain_objects))
        return world, list(terrain_objects)

    if mode != "generate":
        return None, []

    from .terrain import (
        TerrainConfig,
        generate_and_save_world,
        generate_world,
        load_world,
    )

    seed = world_config.terrain_seed or 42
    if map_save_path:
        save_path = PROJECT_ROOT / map_save_path
        if save_path.exists():
            logger.info("loading_saved_map", path=str(save_path))
            world, terrain_objects = load_world(save_path)
            logger.info(
                "map_loaded", tiles=width * height, objects=len(terrain_objects)
            )
            return world, list(terrain_objects)

        logger.info(
            "generating_terrain",
            width=width,
            height=height,
            seed=seed,
            save_path=str(save_path),
        )
        world, terrain_objects = generate_and_save_world(
            TerrainConfig(seed=seed, width=width, height=height), save_path
        )
        logger.info(
            "terrain_generated_and_saved",
            tiles=width * height,
            objects=len(terrain_objects),
            save_path=str(save_path),
        )
        return world, list(terrain_objects)

    logger.info("generating_terrain", width=width, height=height, seed=seed)
    world, terrain_objects = generate_world(
        TerrainConfig(seed=seed, width=width, height=height)
    )
    logger.info("terrain_generated", tiles=width * height, objects=len(terrain_objects))
    return world, list(terrain_objects)


def _resume(args: argparse.Namespace) -> None:
    """Continue a saved run: everything comes from the save (docs/14, section 4).

    The config in the snapshot wins over the TOML on disk, which may have
    changed since the run started.

    Raises:
        SystemExit: With status 1 when the save cannot be used or the settlers
            never reconnect.
    """
    save_dir = Path(args.resume)
    try:
        snapshot, world = load_resume(save_dir)
    except (SnapshotError, FileNotFoundError, ValueError) as exc:
        logger.error("resume_failed", path=str(save_dir), error=str(exc))
        raise SystemExit(1)

    config = Config.model_validate(snapshot.config)
    config_name = snapshot.config_name or DEFAULT_CONFIG
    run_id = run_id_for(config_name)
    run_dir = _run_dir_for(run_id, args.run_dir)
    parent_run_id = args.parent_run_id or snapshot.run_id
    logger.info(
        "resuming_run",
        run_id=run_id,
        run_dir=str(run_dir),
        parent_run_id=parent_run_id,
        tick=snapshot.tick,
    )

    try:
        run(
            ServerSettings(
                width=world.width,
                height=world.height,
                world=world,
                port=args.port,
                ws_port=args.ws_port,
                tick_duration_ms=config.world.tick_duration_ms,
                intent_deadline_ms=config.world.intent_deadline_ms or 0,
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
                snapshot=snapshot,
                parent_run_id=parent_run_id,
            )
        )
    except ResumeStartupError as exc:
        logger.error("resume_startup_failed", error=str(exc))
        raise SystemExit(1)


def _settings_from_config(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> ServerSettings:
    """Turn the named config plus the CLI overrides into one `ServerSettings`."""
    from .config import config_to_entities, config_to_objects, find_config, load_config

    # There is no built-in fallback world: a config that cannot be found is an
    # error naming what was looked for.
    try:
        config_path = find_config(args.config)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    config = load_config(config_path)
    logger.info("config_loaded", path=str(config_path))
    config_name = config_path.stem

    width = args.width if args.width is not None else config.world.width
    height = args.height if args.height is not None else config.world.height
    tick_duration = (
        args.tick_duration
        if args.tick_duration is not None
        else config.world.tick_duration_ms
    )

    entities: list[Entity] = config_to_entities(config)
    objects: list[WorldObject] = config_to_objects(config)

    for spawn in args.spawn_entity:
        entity_id, position = _parse_spawn(parser, spawn)
        entities.append(
            Entity(entity_id=entity_id, position=position, entity_type="player")
        )
    for spawn in args.spawn_bush:
        object_id, position = _parse_spawn(parser, spawn)
        objects.append(
            WorldObject(
                object_id=object_id,
                position=position,
                object_type="bush",
                state=(("berry_count", "1"),),  # Binary state: has berry
            )
        )

    world, terrain_objects = _load_terrain(parser, config.world, width, height)
    objects.extend(terrain_objects)

    run_id = run_id_for(config_name)
    run_dir = _run_dir_for(run_id, args.run_dir)
    logger.info("recording_run", run_id=run_id, run_dir=str(run_dir))

    # The map path recorded in meta is relative to the project root, as the
    # config writes it; only a map that actually exists is recorded.
    map_save_path = config.world.map_save_path
    recorded_map_path = ""
    if map_save_path and (PROJECT_ROOT / map_save_path).exists():
        recorded_map_path = map_save_path

    return ServerSettings(
        width=width,
        height=height,
        world=world,
        entities=entities,
        objects=objects,
        spawn_mode=config.world.spawn_mode,
        day_length_ticks=config.world.day_length_ticks,
        new_moon_every_days=config.world.new_moon_every_days,
        port=args.port,
        ws_port=args.ws_port,
        tick_duration_ms=tick_duration,
        intent_deadline_ms=config.world.intent_deadline_ms or 0,
        wolves=config.world.wolves,
        wolf_settings=config.world.wolf_settings(),
        run_dir=run_dir,
        run_id=run_id,
        config_name=config_name,
        config_path=_relative_to_root(config_path),
        map_path=recorded_map_path,
        save_on_new_moon=config.world.save_on_new_moon,
        save_wait_seconds=config.world.save_wait_seconds,
        run_config=config.model_dump(),
    )


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point for the world server."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging()

    if args.resume:
        _resume(args)
        return

    run(_settings_from_config(parser, args))


if __name__ == "__main__":  # pragma: no cover
    main()
