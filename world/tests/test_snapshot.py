"""Writing, guarding and loading a save, and the pause that takes it (docs/14)."""

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from world.save_coordinator import SaveCoordinator, SaveSettings
from world.snapshot import (
    SNAPSHOT_FORMAT_VERSION,
    SnapshotError,
    SnapshotMapMismatchError,
    SnapshotVersionError,
    agent_snapshot_path,
    check_map,
    is_complete,
    load_world_snapshot,
    newest_complete_save,
    prepare_save_dir,
    restore_wolf_simulator,
    restore_world,
    rng_state_from_json,
    rng_state_to_json,
    save_dir_for,
    write_world_snapshot,
)
from world.state import Entity, World, WorldObject, night_start_tick
from world.types import Position
from world.wolves import WolfSimulator

DAY = 60
NIGHT = night_start_tick(DAY)


def _world() -> World:
    world = World(width=12, height=12, day_length_ticks=DAY, new_moon_every_days=1)
    world.settlement = Position(x=6, y=6)
    world.add_entity(Entity(entity_id="ada", position=Position(x=2, y=2), food=41))
    dead = Entity(entity_id="bram", position=Position(x=3, y=3))
    world.add_entity(dead)
    world.set_entity(dead.as_dead())
    world.detach_entity("bram")
    world.mark_death("bram", 5)
    world.add_object(
        WorldObject(
            object_id="bush_1",
            position=Position(x=4, y=4),
            object_type="bush",
            state=(("berry_count", "1"),),
        )
    )
    world.add_object(
        WorldObject(
            object_id="wall_1",
            position=Position(x=5, y=5),
            object_type="wood_wall",
        )
    )
    world.set_object_id_seq(17)
    world.tick = 123
    return world


def _write(save_dir: Path, world: World, simulator: WolfSimulator) -> None:
    write_world_snapshot(
        world,
        save_dir=save_dir,
        run_id="20260920-000000-hamlet",
        config={"world": {"width": 12}},
        config_name="hamlet",
        config_path="world/configs/hamlet.toml",
        map_path="",
        map_sha256="",
        wolf_simulator=simulator,
    )


class TestRngState:
    def test_round_trips_through_json(self) -> None:
        simulator = WolfSimulator(seed=99)
        for _ in range(20):
            simulator.rng.random()
        state = simulator.rng_state()
        encoded = json.loads(json.dumps(rng_state_to_json(state)))
        assert rng_state_from_json(encoded) == state

    def test_a_short_state_is_refused(self) -> None:
        with pytest.raises(SnapshotError, match="expected 3 fields"):
            rng_state_from_json([1, 2])


class TestWriting:
    def test_complete_json_is_the_marker(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        assert (save_dir / "agents").is_dir()
        assert not is_complete(save_dir)

        _write(save_dir, _world(), WolfSimulator(seed=1))
        assert is_complete(save_dir)
        assert not list(save_dir.glob("*.partial"))

    def test_objects_are_written_in_registry_order(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        _write(save_dir, _world(), WolfSimulator(seed=1))
        with gzip.open(save_dir / "objects.jsonl.gz", "rt") as stream:
            ids = [json.loads(line)["object_id"] for line in stream]
        assert ids == ["bush_1", "wall_1"]

    def test_newest_complete_save_wins(self, tmp_path: Path) -> None:
        for tick in (100, 203, 50):
            save_dir = prepare_save_dir(tmp_path, tick)
            world = _world()
            world.tick = tick
            _write(save_dir, world, WolfSimulator(seed=1))
        prepare_save_dir(tmp_path, 999)  # no complete.json: not a save
        assert newest_complete_save(tmp_path) == save_dir_for(tmp_path, 203)

    def test_no_saves_at_all(self, tmp_path: Path) -> None:
        assert newest_complete_save(tmp_path) is None


class TestGuards:
    def test_an_unfinished_directory_is_not_a_save(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 1)
        with pytest.raises(SnapshotError, match="not a finished save"):
            load_world_snapshot(save_dir)

    def test_an_unknown_format_version_is_refused(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        _write(save_dir, _world(), WolfSimulator(seed=1))
        data = json.loads((save_dir / "world.json").read_text())
        data["format_version"] = SNAPSHOT_FORMAT_VERSION + 7
        (save_dir / "world.json").write_text(json.dumps(data))
        with pytest.raises(SnapshotVersionError, match="format_version"):
            load_world_snapshot(save_dir)

    def test_a_changed_map_is_refused(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        world = _world()
        map_file = tmp_path / "map.npz"
        map_file.write_bytes(b"the map")
        write_world_snapshot(
            world,
            save_dir=save_dir,
            run_id="r",
            config={},
            config_name="hamlet",
            config_path="",
            map_path="map.npz",
            map_sha256="0" * 64,
            wolf_simulator=WolfSimulator(seed=1),
        )
        snapshot = load_world_snapshot(save_dir)
        with pytest.raises(SnapshotMapMismatchError, match="sha256"):
            check_map(snapshot, tmp_path)

    def test_a_missing_map_is_refused(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        write_world_snapshot(
            _world(),
            save_dir=save_dir,
            run_id="r",
            config={},
            config_name="hamlet",
            config_path="",
            map_path="gone.npz",
            map_sha256="0" * 64,
            wolf_simulator=WolfSimulator(seed=1),
        )
        with pytest.raises(SnapshotMapMismatchError, match="Cannot read"):
            check_map(load_world_snapshot(save_dir), tmp_path)

    def test_no_map_passes(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        _write(save_dir, _world(), WolfSimulator(seed=1))
        check_map(load_world_snapshot(save_dir), tmp_path)


class TestRoundTrip:
    def test_the_world_comes_back_exactly(self, tmp_path: Path) -> None:
        original = _world()
        simulator = WolfSimulator(seed=5)
        for _ in range(11):
            simulator.rng.random()
        simulator.set_id_counter(4)
        save_dir = prepare_save_dir(tmp_path, original.tick)
        _write(save_dir, original, simulator)

        snapshot = load_world_snapshot(save_dir)
        restored = World(width=12, height=12)
        restore_world(snapshot, save_dir, restored)

        assert restored.tick == original.tick
        assert restored.day_length_ticks == original.day_length_ticks
        assert restored.new_moon_every_days == original.new_moon_every_days
        assert restored.settlement == original.settlement
        assert restored.object_id_seq == original.object_id_seq
        assert dict(restored.pending_respawns()) == dict(original.pending_respawns())
        assert [e.model_dump() for e in restored.all_entities().values()] == [
            e.model_dump() for e in original.all_entities().values()
        ]
        assert list(restored.all_objects()) == list(original.all_objects())
        # A wall still blocks, so the blocking index was rebuilt.
        assert restored.is_blocked(Position(x=5, y=5))
        # The dead settler holds no tile.
        assert restored.get_entity_at(Position(x=3, y=3)) is None
        assert restored.get_entity_at(Position(x=2, y=2)) is not None

        fresh = WolfSimulator(seed=1)
        restore_wolf_simulator(snapshot, fresh)
        assert fresh.rng_state() == simulator.rng_state()
        assert fresh.id_counter == 4

    def test_restoring_into_a_full_world_is_refused(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        _write(save_dir, _world(), WolfSimulator(seed=1))
        snapshot = load_world_snapshot(save_dir)
        busy = World(width=12, height=12)
        busy.add_entity(Entity(entity_id="x", position=Position(x=0, y=0)))
        with pytest.raises(SnapshotError, match="empty world"):
            restore_world(snapshot, save_dir, busy)

    def test_a_map_of_the_wrong_size_is_refused(self, tmp_path: Path) -> None:
        save_dir = prepare_save_dir(tmp_path, 123)
        _write(save_dir, _world(), WolfSimulator(seed=1))
        snapshot = load_world_snapshot(save_dir)
        with pytest.raises(SnapshotError, match="the snapshot is 12x12"):
            restore_world(snapshot, save_dir, World(width=8, height=8))


def _coordinator(tmp_path: Path, world: World, **overrides: Any) -> SaveCoordinator:
    settings = SaveSettings(
        run_dir=tmp_path,
        run_id="run",
        config={"world": {}},
        config_name="hamlet",
        wait_seconds=overrides.pop("wait_seconds", 5),
        poll_interval_s=0.01,
        **overrides,
    )
    return SaveCoordinator(world, WolfSimulator(seed=3), settings)


class TestWhenASaveIsDue:
    def test_due_at_the_offset_of_a_new_moon_night(self, tmp_path: Path) -> None:
        world = _world()
        coordinator = _coordinator(tmp_path, world)
        world.tick = NIGHT + 3
        assert coordinator.pending_save_tick() == world.tick

    def test_not_due_a_tick_earlier(self, tmp_path: Path) -> None:
        world = _world()
        coordinator = _coordinator(tmp_path, world)
        world.tick = NIGHT + 2
        assert coordinator.pending_save_tick() == 0

    def test_not_due_when_saving_is_off(self, tmp_path: Path) -> None:
        world = _world()
        coordinator = _coordinator(tmp_path, world, enabled=False)
        world.tick = NIGHT + 3
        assert coordinator.pending_save_tick() == 0

    def test_not_due_without_a_run_dir(self, tmp_path: Path) -> None:
        world = _world()
        coordinator = SaveCoordinator(world, WolfSimulator(seed=3))
        world.tick = NIGHT + 3
        assert coordinator.pending_save_tick() == 0

    def test_the_resumed_tick_never_saves_twice(self, tmp_path: Path) -> None:
        world = _world()
        world.tick = NIGHT + 3
        coordinator = _coordinator(tmp_path, world, suppress_tick=NIGHT + 3)
        assert coordinator.pending_save_tick() == 0

    def test_wolves_are_not_expected_to_report(self, tmp_path: Path) -> None:
        world = _world()
        world.add_entity(
            Entity(entity_id="wolf_1", position=Position(x=9, y=9), entity_type="wolf")
        )
        coordinator = _coordinator(tmp_path, world)
        assert coordinator.expected_entities() == ["ada", "bram"]


class TestTheWait:
    @pytest.mark.asyncio
    async def test_a_save_completes_once_every_settler_reports(
        self, tmp_path: Path
    ) -> None:
        world = _world()
        coordinator = _coordinator(tmp_path, world)
        save_dir = coordinator.prepare(world.tick)
        assert save_dir is not None
        for entity_id in ("ada", "bram"):
            agent_snapshot_path(save_dir, entity_id).write_bytes(b"x")

        assert await coordinator.wait_and_write(world.tick)
        assert is_complete(save_dir)
        snapshot = load_world_snapshot(save_dir)
        assert snapshot.tick == world.tick
        assert snapshot.config_name == "hamlet"

    @pytest.mark.asyncio
    async def test_a_timeout_abandons_the_save_and_keeps_the_files(
        self, tmp_path: Path
    ) -> None:
        world = _world()
        coordinator = _coordinator(tmp_path, world, wait_seconds=0)
        save_dir = coordinator.prepare(world.tick)
        assert save_dir is not None
        agent_snapshot_path(save_dir, "ada").write_bytes(b"x")

        assert not await coordinator.wait_and_write(world.tick)
        assert not save_dir.exists()
        abandoned = save_dir.with_name(save_dir.name + ".abandoned")
        assert (abandoned / "agents" / "ada.json.gz").is_file()
        assert not (abandoned / "world.json").exists()
