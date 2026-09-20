"""World snapshots: writing and loading a save (docs/14_new_moon_and_saves.md).

A save is a directory `runs/<run>/saves/tick-<T>/` holding

- `agents/<entity_id>.json.gz` - one file per settler, written by the agents
  before the world writes anything (`save_coordinator` waits for them),
- `world.json` - the state at the start of tick `T`,
- `objects.jsonl.gz` - every object in registry order,
- `complete.json` - written last. A directory without it is not a save.

The world's two files are written under a `.partial` name and renamed, so a
half-written save can never be mistaken for a whole one.

Loading refuses an unknown `format_version` and a map whose sha256 has moved.
Everything derived (position indexes, the blocking index, chunk caches) is
rebuilt as the objects and entities go back in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import structlog

from .exceptions import WorldError
from .recording import JsonlGzWriter, file_sha256, read_jsonl_gz
from .state import Entity, World, WorldObject
from .types import Position
from .viewer_payload import object_state
from .wolves import WolfSimulator

logger = structlog.get_logger()

SNAPSHOT_FORMAT_VERSION = 1

WORLD_FILE = "world.json"
OBJECTS_FILE = "objects.jsonl.gz"
COMPLETE_FILE = "complete.json"
AGENTS_DIR = "agents"
PARTIAL_SUFFIX = ".partial"
ABANDONED_SUFFIX = ".abandoned"


class SnapshotError(WorldError):
    """A snapshot directory is missing, incomplete or unusable."""


class SnapshotVersionError(SnapshotError):
    """A snapshot was written by an incompatible version of the world."""


class SnapshotMapMismatchError(SnapshotError):
    """The map file on disk is not the one the snapshot was taken against."""


# --- Paths -----------------------------------------------------------------


def saves_dir(run_dir: Path) -> Path:
    """The directory holding every save of a run."""
    return run_dir / "saves"


def save_dir_for(run_dir: Path, tick: int) -> Path:
    """The directory of the save taken at `tick`."""
    return saves_dir(run_dir) / f"tick-{tick}"


def agent_snapshot_path(save_dir: Path, entity_id: str) -> Path:
    """Where one settler's snapshot file belongs inside a save."""
    return save_dir / AGENTS_DIR / f"{entity_id}.json.gz"


def prepare_save_dir(run_dir: Path, tick: int) -> Path:
    """Create `saves/tick-<T>/agents/` so the settlers can write into it.

    The directory exists before observation `T` goes out, because the agents
    write their files as soon as they have folded it.

    Raises:
        OSError: If the directory cannot be created.
    """
    save_dir = save_dir_for(run_dir, tick)
    (save_dir / AGENTS_DIR).mkdir(parents=True, exist_ok=True)
    return save_dir


def is_complete(save_dir: Path) -> bool:
    """Whether this directory holds a finished save."""
    return (save_dir / COMPLETE_FILE).is_file()


def newest_complete_save(run_dir: Path) -> Path | None:
    """The complete save of `run_dir` with the highest tick, or None."""
    best: tuple[int, Path] | None = None
    directory = saves_dir(run_dir)
    if not directory.is_dir():
        return None
    for entry in directory.iterdir():
        if not entry.is_dir() or not entry.name.startswith("tick-"):
            continue
        if not is_complete(entry):
            continue
        try:
            tick = int(entry.name.removeprefix("tick-"))
        except ValueError:
            continue
        if best is None or tick > best[0]:
            best = (tick, entry)
    return best[1] if best is not None else None


# --- The RNG state ---------------------------------------------------------


def rng_state_to_json(state: Sequence[Any]) -> list[Any]:
    """`random.Random.getstate()` as JSON: [version, [ints...], gauss|null]."""
    version, internal, gauss = state
    return [int(version), [int(value) for value in internal], gauss]


def rng_state_from_json(data: Sequence[Any]) -> tuple[Any, ...]:
    """The inverse of `rng_state_to_json`.

    Raises:
        SnapshotError: If the encoded state does not have the expected shape.
    """
    if len(data) != 3:
        raise SnapshotError(f"Bad RNG state: expected 3 fields, got {len(data)}")
    version, internal, gauss = data
    if not isinstance(internal, list):
        raise SnapshotError("Bad RNG state: the internal state is not a list")
    return (int(version), tuple(int(value) for value in internal), gauss)


# --- The snapshot record ---------------------------------------------------


@dataclass(frozen=True)
class WorldSnapshot:
    """Everything `world.json` holds, parsed and validated."""

    format_version: int
    tick: int
    run_id: str
    day_length_ticks: int
    new_moon_every_days: int
    width: int
    height: int
    settlement: Position | None
    map_path: str
    map_sha256: str
    config_name: str
    config_path: str
    config: dict[str, Any]
    object_id_seq: int
    death_ticks: dict[str, int]
    wolf_rng_state: list[Any]
    wolf_id_counter: int
    entities: list[dict[str, Any]]

    @property
    def entity_ids(self) -> list[str]:
        """Every entity id in the snapshot, in registry order."""
        return [str(entity["entity_id"]) for entity in self.entities]

    def settler_ids(self) -> list[str]:
        """Non-wolf entity ids: the settlers that must have an agent."""
        from .state import WOLF_ENTITY_TYPE

        return [
            str(entity["entity_id"])
            for entity in self.entities
            if entity.get("entity_type") != WOLF_ENTITY_TYPE
        ]


# --- Writing ---------------------------------------------------------------


def write_world_snapshot(
    world: World,
    save_dir: Path,
    run_id: str,
    config: Mapping[str, Any],
    config_name: str,
    config_path: str,
    map_path: str,
    map_sha256: str,
    wolf_simulator: WolfSimulator,
) -> None:
    """Write the world's half of the save into an existing `save_dir`.

    `complete.json` is written last, after both data files have been renamed
    into place, so a reader that sees it sees a whole save.

    Raises:
        OSError: If any file cannot be written.
    """
    settlement = world.settlement
    data: dict[str, Any] = {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "tick": world.tick,
        "run_id": run_id,
        "day_length_ticks": world.day_length_ticks,
        "new_moon_every_days": world.new_moon_every_days,
        "world_size": {"width": world.width, "height": world.height},
        "settlement": (
            {"x": settlement.x, "y": settlement.y} if settlement is not None else None
        ),
        "map_path": map_path,
        "map_sha256": map_sha256,
        "config_name": config_name,
        "config_path": config_path,
        "config": dict(config),
        "object_id_seq": world.object_id_seq,
        "death_ticks": dict(sorted(world.pending_respawns().items())),
        "wolf_rng_state": rng_state_to_json(wolf_simulator.rng_state()),
        "wolf_id_counter": wolf_simulator.id_counter,
        "entities": [entity.model_dump() for entity in world.all_entities().values()],
    }

    _write_json_atomic(save_dir / WORLD_FILE, data)
    _write_objects_atomic(save_dir / OBJECTS_FILE, world)
    _write_json_atomic(
        save_dir / COMPLETE_FILE,
        {
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "tick": world.tick,
            "run_id": run_id,
            "entities": [entity["entity_id"] for entity in data["entities"]],
        },
    )
    logger.info(
        "world_snapshot_written",
        path=str(save_dir),
        tick=world.tick,
        entities=len(data["entities"]),
        objects=world.object_count(),
    )


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    """Write JSON to `path` via a `.partial` name and one rename."""
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
        handle.write("\n")
    partial.replace(path)


def _write_objects_atomic(path: Path, world: World) -> None:
    """Write every object in registry order, one JSON line each."""
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    partial.unlink(missing_ok=True)
    writer = JsonlGzWriter(partial)
    try:
        for obj in world.all_objects().values():
            writer.write(object_state(obj))
    finally:
        writer.close()
    partial.replace(path)


def abandon_save_dir(save_dir: Path) -> Path:
    """Rename a save directory aside after a timeout, keeping what is there.

    The agent files are useful for working out who did not report, so nothing
    is deleted.

    Raises:
        OSError: If the directory cannot be renamed.
    """
    target = save_dir.with_name(save_dir.name + ABANDONED_SUFFIX)
    if target.exists():
        target = save_dir.with_name(f"{save_dir.name}{ABANDONED_SUFFIX}-{id(save_dir)}")
    save_dir.rename(target)
    return target


# --- Loading ---------------------------------------------------------------


def load_world_snapshot(save_dir: Path) -> WorldSnapshot:
    """Read and validate `world.json`.

    Raises:
        SnapshotError: If the save is incomplete or `world.json` is unreadable.
        SnapshotVersionError: If `format_version` is not one we understand.
    """
    if not is_complete(save_dir):
        raise SnapshotError(f"No {COMPLETE_FILE} in {save_dir}: not a finished save")
    world_file = save_dir / WORLD_FILE
    try:
        with open(world_file, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"Unreadable {world_file}: {exc}") from exc
    if not isinstance(data, dict):
        raise SnapshotError(f"{world_file} is not a JSON object")

    version = int(data.get("format_version", -1))
    if version != SNAPSHOT_FORMAT_VERSION:
        raise SnapshotVersionError(
            f"{world_file} has format_version {version}, "
            f"this world writes and reads {SNAPSHOT_FORMAT_VERSION}"
        )

    settlement_data = data.get("settlement")
    settlement = (
        Position(x=int(settlement_data["x"]), y=int(settlement_data["y"]))
        if isinstance(settlement_data, dict)
        else None
    )
    size = data.get("world_size", {})
    return WorldSnapshot(
        format_version=version,
        tick=int(data["tick"]),
        run_id=str(data.get("run_id", "")),
        day_length_ticks=int(data["day_length_ticks"]),
        new_moon_every_days=int(data.get("new_moon_every_days", 0)),
        width=int(size.get("width", 0)),
        height=int(size.get("height", 0)),
        settlement=settlement,
        map_path=str(data.get("map_path", "")),
        map_sha256=str(data.get("map_sha256", "")),
        config_name=str(data.get("config_name", "")),
        config_path=str(data.get("config_path", "")),
        config=dict(data.get("config", {})),
        object_id_seq=int(data.get("object_id_seq", 0)),
        death_ticks={str(k): int(v) for k, v in data.get("death_ticks", {}).items()},
        wolf_rng_state=list(data.get("wolf_rng_state", [])),
        wolf_id_counter=int(data.get("wolf_id_counter", 0)),
        entities=list(data.get("entities", [])),
    )


def iter_snapshot_objects(save_dir: Path) -> Iterator[WorldObject]:
    """Stream the saved objects in registry order.

    Raises:
        SnapshotError: If the objects file is missing or unreadable.
    """
    path = save_dir / OBJECTS_FILE
    if not path.is_file():
        raise SnapshotError(f"No {OBJECTS_FILE} in {save_dir}")
    try:
        for record in read_jsonl_gz(path):
            yield _object_from_state(record)
    except OSError as exc:
        raise SnapshotError(f"Unreadable {path}: {exc}") from exc


def _object_from_state(record: Mapping[str, Any]) -> WorldObject:
    """Rebuild a WorldObject from the JSON shape `object_state` writes."""
    position = record["position"]
    state = record.get("state", {})
    return WorldObject(
        object_id=str(record["object_id"]),
        position=Position(x=int(position["x"]), y=int(position["y"])),
        object_type=str(record["object_type"]),
        state=tuple((str(k), str(v)) for k, v in state.items()),
    )


def check_map(snapshot: WorldSnapshot, project_root: Path) -> None:
    """Verify the map file still hashes to what the snapshot recorded.

    A snapshot with no map (a generated test world) passes.

    Raises:
        SnapshotMapMismatchError: If the file is missing or its hash differs.
    """
    if not snapshot.map_path or not snapshot.map_sha256:
        return
    map_file = project_root / snapshot.map_path
    try:
        digest = file_sha256(map_file)
    except OSError as exc:
        raise SnapshotMapMismatchError(
            f"Cannot read the snapshot's map {map_file}: {exc}"
        ) from exc
    if digest != snapshot.map_sha256:
        raise SnapshotMapMismatchError(
            f"{map_file} has sha256 {digest}, the snapshot was taken against "
            f"{snapshot.map_sha256}"
        )


def restore_world(snapshot: WorldSnapshot, save_dir: Path, world: World) -> None:
    """Put a snapshot's objects and entities into a world that has terrain.

    `world` must be empty of entities and objects; its floor array and size
    come from the map. Dead entities go back unplaced, exactly as the live
    world keeps them.

    Raises:
        SnapshotError: If the world already holds state, or the save is broken.
    """
    if world.entity_count() or world.object_count():
        raise SnapshotError("restore_world() needs an empty world")
    if snapshot.width and (world.width, world.height) != (
        snapshot.width,
        snapshot.height,
    ):
        raise SnapshotError(
            f"Map is {world.width}x{world.height}, the snapshot is "
            f"{snapshot.width}x{snapshot.height}"
        )

    world.tick = snapshot.tick
    world.day_length_ticks = snapshot.day_length_ticks
    world.new_moon_every_days = snapshot.new_moon_every_days
    world.settlement = snapshot.settlement
    world.set_object_id_seq(snapshot.object_id_seq)

    for obj in iter_snapshot_objects(save_dir):
        world.add_object(obj)

    for record in snapshot.entities:
        entity = Entity.model_validate(record)
        if entity.alive:
            world.add_entity(entity)
        else:
            world.add_entity_unplaced(entity)

    for entity_id, death_tick in snapshot.death_ticks.items():
        world.mark_death(entity_id, death_tick)


def restore_wolf_simulator(snapshot: WorldSnapshot, simulator: WolfSimulator) -> None:
    """Put the wolf simulator back where the snapshot left it.

    Raises:
        SnapshotError: If the saved RNG state cannot be restored.
    """
    try:
        simulator.restore_rng_state(rng_state_from_json(snapshot.wolf_rng_state))
    except (ValueError, TypeError) as exc:
        raise SnapshotError(f"Bad wolf RNG state in the snapshot: {exc}") from exc
    simulator.set_id_counter(snapshot.wolf_id_counter)
