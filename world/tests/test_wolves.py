"""Tests for the wolf simulation."""

import time

from world.events import TickEvents
from world.state import Entity, World
from world.tick_context import TickContext
from world.types import AttackIntent, MoveIntent, Position, chebyshev_distance
from world.wolves import (
    DESPAWN_DISTANCE,
    MAX_WOLVES,
    SPAWN_INTERVAL_TICKS,
    SPAWN_MAX_DISTANCE,
    SPAWN_MIN_DISTANCE,
    WolfSettings,
    WolfSimulator,
)


def _context(world: World) -> TickContext:
    now = int(time.time() * 1000)
    return TickContext(
        tick_id=world.tick, start_time_ms=now, deadline_ms=now + 10_000, world=world
    )


def _world_with_player(position: Position = Position(x=50, y=50)) -> World:
    world = World(width=120, height=120)
    world.add_entity(Entity(entity_id="alice", position=position, entity_type="player"))
    return world


def _add_wolf(world: World, entity_id: str, position: Position) -> Entity:
    wolf = Entity(
        entity_id=entity_id,
        position=position,
        entity_type="wolf",
        health=10,
        max_health=10,
        food=100,
    )
    world.add_entity(wolf)
    return wolf


class TestSpawning:
    def test_spawns_in_the_allowed_ring(self) -> None:
        world = _world_with_player()
        world.tick = SPAWN_INTERVAL_TICKS
        sim = WolfSimulator(seed=1)
        events = TickEvents()

        sim.step(world, _context(world), events)

        wolves = [e for e in world.all_entities().values() if e.entity_type == "wolf"]
        assert len(wolves) == 1
        distance = chebyshev_distance(wolves[0].position, Position(x=50, y=50))
        assert SPAWN_MIN_DISTANCE <= distance <= SPAWN_MAX_DISTANCE
        assert events.entities_spawned[0].entity_id == wolves[0].entity_id

    def test_settings_default_to_the_module_constants(self) -> None:
        settings = WolfSettings()
        assert settings.max_wolves == MAX_WOLVES
        assert settings.spawn_min_distance == SPAWN_MIN_DISTANCE
        assert settings.spawn_max_distance == SPAWN_MAX_DISTANCE
        assert settings.spawn_interval_ticks == SPAWN_INTERVAL_TICKS
        assert WolfSimulator(seed=1).settings == settings

    def test_a_configured_spawn_interval_is_the_only_tick_that_spawns(self) -> None:
        settings = WolfSettings(spawn_interval_ticks=120)
        simulator = WolfSimulator(seed=1, settings=settings)
        world = _world_with_player()

        world.tick = SPAWN_INTERVAL_TICKS
        simulator.step(world, _context(world), TickEvents())
        assert not [e for e in world.all_entities().values() if e.entity_type == "wolf"]

        world.tick = 120
        simulator.step(world, _context(world), TickEvents())
        assert [e for e in world.all_entities().values() if e.entity_type == "wolf"]

    def test_spawns_in_the_ring_the_settings_ask_for(self) -> None:
        settings = WolfSettings(
            max_wolves=1, spawn_min_distance=30, spawn_max_distance=45
        )
        for seed in range(6):
            world = _world_with_player()
            world.tick = SPAWN_INTERVAL_TICKS
            WolfSimulator(seed=seed, settings=settings).step(
                world, _context(world), TickEvents()
            )

            wolves = [
                e for e in world.all_entities().values() if e.entity_type == "wolf"
            ]
            assert len(wolves) == 1
            distance = chebyshev_distance(wolves[0].position, Position(x=50, y=50))
            assert 30 <= distance <= 45

    def test_a_one_wolf_world_never_gets_a_second(self) -> None:
        settings = WolfSettings(max_wolves=1)
        world = _world_with_player()
        sim = WolfSimulator(seed=1, settings=settings)
        for step in range(1, 6):
            world.tick = SPAWN_INTERVAL_TICKS * step
            sim.step(world, _context(world), TickEvents())

        wolves = [e for e in world.all_entities().values() if e.entity_type == "wolf"]
        assert len(wolves) == 1

    def test_max_wolves_zero_spawns_nothing(self) -> None:
        world = _world_with_player()
        world.tick = SPAWN_INTERVAL_TICKS
        WolfSimulator(seed=1, settings=WolfSettings(max_wolves=0)).step(
            world, _context(world), TickEvents()
        )

        assert world.entity_count() == 1

    def test_no_spawn_off_interval(self) -> None:
        world = _world_with_player()
        world.tick = SPAWN_INTERVAL_TICKS + 1
        sim = WolfSimulator(seed=1)

        sim.step(world, _context(world), TickEvents())

        assert world.entity_count() == 1

    def test_respects_the_wolf_cap(self) -> None:
        world = _world_with_player()
        for index in range(MAX_WOLVES):
            _add_wolf(world, f"wolf_{index}", Position(x=80 + index, y=80))
        world.tick = SPAWN_INTERVAL_TICKS
        sim = WolfSimulator(seed=1)

        sim.step(world, _context(world), TickEvents())

        wolves = [e for e in world.all_entities().values() if e.entity_type == "wolf"]
        assert len(wolves) == MAX_WOLVES

    def test_deterministic_for_a_seed(self) -> None:
        positions = []
        for _ in range(2):
            world = _world_with_player()
            world.tick = SPAWN_INTERVAL_TICKS
            WolfSimulator(seed=99).step(world, _context(world), TickEvents())
            positions.append(
                [
                    e.position
                    for e in world.all_entities().values()
                    if e.entity_type == "wolf"
                ]
            )
        assert positions[0] == positions[1]


class TestBehaviour:
    def test_adjacent_wolf_attacks(self) -> None:
        world = _world_with_player()
        _add_wolf(world, "wolf_1", Position(x=51, y=50))
        ctx = _context(world)

        WolfSimulator(seed=1).step(world, ctx, TickEvents())

        intent = ctx.intents["wolf_1"]
        assert isinstance(intent, AttackIntent)
        assert intent.target_entity_id == "alice"

    def test_wolf_chases_within_radius(self) -> None:
        world = _world_with_player()
        _add_wolf(world, "wolf_1", Position(x=55, y=50))
        ctx = _context(world)

        WolfSimulator(seed=1).step(world, ctx, TickEvents())

        intent = ctx.intents["wolf_1"]
        assert isinstance(intent, MoveIntent)
        new_position = Position(x=55, y=50).offset(intent.direction)
        assert chebyshev_distance(new_position, Position(x=50, y=50)) == 4

    def test_far_wolf_wanders_or_idles(self) -> None:
        world = _world_with_player()
        _add_wolf(world, "wolf_1", Position(x=70, y=70))
        moves = 0
        for seed in range(20):
            ctx = _context(world)
            WolfSimulator(seed=seed)._submit_intents(world, ctx)
            if "wolf_1" in ctx.intents:
                assert isinstance(ctx.intents["wolf_1"], MoveIntent)
                moves += 1
        assert 0 < moves < 20

    def test_despawns_beyond_the_limit(self) -> None:
        world = _world_with_player(Position(x=5, y=5))
        _add_wolf(world, "wolf_1", Position(x=5 + DESPAWN_DISTANCE + 1, y=5))
        events = TickEvents()

        WolfSimulator(seed=1).step(world, _context(world), events)

        assert "wolf_1" not in world.all_entities()
        assert events.entities_despawned[0].reason == "too_far"

    def test_keeps_wolves_inside_the_limit(self) -> None:
        world = _world_with_player(Position(x=5, y=5))
        _add_wolf(world, "wolf_1", Position(x=5 + DESPAWN_DISTANCE, y=5))

        WolfSimulator(seed=1).step(world, _context(world), TickEvents())

        assert "wolf_1" in world.all_entities()

    def test_dead_players_are_ignored(self) -> None:
        world = _world_with_player()
        alice = world.get_entity("alice")
        world.detach_entity("alice")
        world.set_entity(alice.as_dead())
        _add_wolf(world, "wolf_1", Position(x=51, y=50))
        ctx = _context(world)

        WolfSimulator(seed=1).step(world, ctx, TickEvents())

        assert "wolf_1" in world.all_entities()
        assert not isinstance(ctx.intents.get("wolf_1"), AttackIntent)
