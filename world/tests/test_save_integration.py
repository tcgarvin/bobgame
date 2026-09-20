"""A real server takes a save on a new-moon night and resumes from it (docs/14).

Small worlds, short days and 50 ms ticks: a whole new-moon night happens in
well under a second. The "agents" are an asyncio task that drops a file into
the save directory as soon as it appears, which is all the world waits for.
"""

import asyncio
from pathlib import Path

import pytest

from world.exceptions import ResumeStartupError
from world.moon import NEW_MOON_SAVE_OFFSET
from world.save_coordinator import SaveSettings
from world.server import WorldServer
from world.snapshot import (
    agent_snapshot_path,
    is_complete,
    load_world_snapshot,
    restore_wolf_simulator,
    restore_world,
    save_dir_for,
)
from world.state import Entity, World, WorldObject, night_start_tick
from world.tick import TickConfig
from world.types import Position

DAY = 12
NIGHT = night_start_tick(DAY)
SAVE_TICK = NIGHT + NEW_MOON_SAVE_OFFSET
SETTLERS = ("ada", "bram")


def _world(tick: int = NIGHT - 1) -> World:
    """A 12x12 world whose every night is a new moon, one tick before it."""
    world = World(width=12, height=12, day_length_ticks=DAY, new_moon_every_days=1)
    world.settlement = Position(x=6, y=6)
    world.tick = tick
    world.add_entity(Entity(entity_id="ada", position=Position(x=4, y=4), fatigue=30))
    world.add_entity(Entity(entity_id="bram", position=Position(x=6, y=4), fatigue=5))
    world.add_object(
        WorldObject(object_id="bed_1", position=Position(x=4, y=5), object_type="bed")
    )
    return world


def _settings(run_dir: Path, **overrides: object) -> SaveSettings:
    defaults: dict[str, object] = {
        "run_dir": run_dir,
        "run_id": "20260921-000000-hamlet",
        "config": {"world": {"day_length_ticks": DAY}},
        "config_name": "hamlet",
        "wait_seconds": 5,
        "poll_interval_s": 0.02,
    }
    defaults.update(overrides)
    return SaveSettings(**defaults)  # type: ignore[arg-type]


async def _fake_agents(save_dir: Path, delay_s: float = 0.0) -> None:
    """Write one snapshot file per settler once the world makes the directory."""
    while not (save_dir / "agents").is_dir():
        await asyncio.sleep(0.01)
    await asyncio.sleep(delay_s)
    for entity_id in SETTLERS:
        agent_snapshot_path(save_dir, entity_id).write_bytes(b"agent state")


async def _run_until(server: WorldServer, tick: int, limit_s: float = 10.0) -> None:
    """Let the server tick until the world passes `tick`."""
    loop = asyncio.get_running_loop()
    give_up_at = loop.time() + limit_s
    while server.world.tick <= tick:
        if loop.time() >= give_up_at:
            raise AssertionError(
                f"world stuck at tick {server.world.tick}, wanted past {tick}"
            )
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
class TestTheNewMoonNight:
    async def test_the_world_saves_and_carries_on(self, tmp_path: Path) -> None:
        world = _world()
        server = WorldServer(
            world,
            port=50097,
            ws_port=18764,
            tick_config=TickConfig(tick_duration_ms=50, intent_deadline_ms=20),
            save_settings=_settings(tmp_path),
        )
        save_dir = save_dir_for(tmp_path, SAVE_TICK)
        agents = asyncio.create_task(_fake_agents(save_dir))
        try:
            await server.start()
            await _run_until(server, SAVE_TICK)
        finally:
            agents.cancel()
            await server.stop(grace_period=0.5)

        # Everyone fell asleep at nightfall and nothing woke them in the window.
        assert world.get_entity("ada").asleep
        assert world.get_entity("ada").sleeping_on == "bed_1"
        assert world.get_entity("bram").asleep
        assert not world.get_entity("ada").collapsed

        assert is_complete(save_dir)
        snapshot = load_world_snapshot(save_dir)
        assert snapshot.tick == SAVE_TICK
        assert snapshot.config_name == "hamlet"
        assert sorted(snapshot.settler_ids()) == list(SETTLERS)
        # The saved state is the start of T: the tick itself still ran.
        assert world.tick > SAVE_TICK

    async def test_a_timeout_never_blocks_the_run(self, tmp_path: Path) -> None:
        world = _world()
        server = WorldServer(
            world,
            port=50096,
            ws_port=18763,
            tick_config=TickConfig(tick_duration_ms=50, intent_deadline_ms=20),
            save_settings=_settings(tmp_path, wait_seconds=0),
        )
        try:
            await server.start()
            await _run_until(server, SAVE_TICK + 1)
        finally:
            await server.stop(grace_period=0.5)

        save_dir = save_dir_for(tmp_path, SAVE_TICK)
        assert not save_dir.exists()
        assert (save_dir.with_name(save_dir.name + ".abandoned")).is_dir()
        assert world.tick > SAVE_TICK + 1


@pytest.mark.asyncio
class TestResuming:
    async def _take_a_save(self, tmp_path: Path) -> Path:
        world = _world()
        server = WorldServer(
            world,
            port=50095,
            ws_port=18762,
            tick_config=TickConfig(tick_duration_ms=50, intent_deadline_ms=20),
            save_settings=_settings(tmp_path),
        )
        save_dir = save_dir_for(tmp_path, SAVE_TICK)
        agents = asyncio.create_task(_fake_agents(save_dir))
        try:
            await server.start()
            await _run_until(server, SAVE_TICK)
        finally:
            agents.cancel()
            await server.stop(grace_period=0.5)
        assert is_complete(save_dir)
        return save_dir

    async def test_the_save_comes_back_and_the_tick_carries_on(
        self, tmp_path: Path
    ) -> None:
        save_dir = await self._take_a_save(tmp_path / "parent")

        snapshot = load_world_snapshot(save_dir)
        resumed = World(width=12, height=12)
        restore_world(snapshot, save_dir, resumed)
        assert resumed.tick == SAVE_TICK
        assert resumed.get_entity("ada").asleep

        child_dir = tmp_path / "child"
        child_dir.mkdir()
        server = WorldServer(
            resumed,
            port=50094,
            ws_port=18761,
            tick_config=TickConfig(tick_duration_ms=50, intent_deadline_ms=20),
            # The tick we resume on already has a save; it must not take another.
            save_settings=_settings(child_dir, suppress_tick=SAVE_TICK),
        )
        restore_wolf_simulator(snapshot, server.tick_loop.wolf_simulator)
        try:
            await server.start()
            await _run_until(server, SAVE_TICK + 2)
        finally:
            await server.stop(grace_period=0.5)

        assert not save_dir_for(child_dir, SAVE_TICK).exists()
        assert resumed.tick > SAVE_TICK + 2

    async def test_a_resume_refuses_to_start_without_its_settlers(
        self, tmp_path: Path
    ) -> None:
        world = _world()
        server = WorldServer(
            world,
            port=50093,
            ws_port=18760,
            tick_config=TickConfig(tick_duration_ms=50, intent_deadline_ms=20),
        )
        with pytest.raises(ResumeStartupError, match="ada, bram"):
            await server.start(wait_for_entities=list(SETTLERS), wait_timeout_s=0.1)
        await server.stop(grace_period=0.5)
        # The tick loop never started, so the world never moved.
        assert world.tick == NIGHT - 1
