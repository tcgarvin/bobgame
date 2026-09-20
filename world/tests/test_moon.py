"""The new moon: forced sleep, the still window, wolves lying low (docs/14)."""

from typing import Any

import pytest

from world.config import WorldConfig
from world.conversations import all_conversations
from world.events import TickEvents
from world.moon import (
    NEW_MOON_SAVE_OFFSET,
    NEW_MOON_STILL_TICKS,
    apply_new_moon,
    in_still_window,
    is_new_moon_day,
    is_still,
    next_new_moon_day,
    save_tick_of_day,
    wolves_lie_low,
)
from world.state import Entity, World, WorldObject, night_start_tick
from world.stats import RESPAWN_DELAY_TICKS, process_respawns
from world.tick import TickContext, process_tick
from world.types import ConverseIntent, Direction, Position, WakeIntent
from world.wolves import WolfSettings, WolfSimulator

DAY = 300
NIGHT = night_start_tick(DAY)


def _world(every_days: int = 3, day_length: int = DAY) -> World:
    return World(
        width=20,
        height=20,
        day_length_ticks=day_length,
        new_moon_every_days=every_days,
    )


def _settler(world: World, entity_id: str, x: int, y: int, **kwargs: Any) -> Entity:
    entity = Entity(entity_id=entity_id, position=Position(x=x, y=y), **kwargs)
    world.add_entity(entity)
    return entity


def _bed(world: World, object_id: str, x: int, y: int) -> WorldObject:
    bed = WorldObject(
        object_id=object_id, position=Position(x=x, y=y), object_type="bed"
    )
    world.add_object(bed)
    return bed


def _context(world: World) -> TickContext:
    return TickContext(tick_id=world.tick, start_time_ms=0, deadline_ms=0, world=world)


class TestHelpers:
    @pytest.mark.parametrize(
        "day,expected", [(0, False), (1, False), (2, True), (5, True), (8, True)]
    )
    def test_every_third_night_is_a_new_moon(self, day: int, expected: bool) -> None:
        assert is_new_moon_day(day, 3) is expected

    def test_zero_means_the_world_never_has_one(self) -> None:
        assert not any(is_new_moon_day(day, 0) for day in range(20))

    def test_next_new_moon_is_today_when_today_is_one(self) -> None:
        assert next_new_moon_day(2, 3) == 2

    def test_next_new_moon_looks_forward(self) -> None:
        assert [next_new_moon_day(day, 3) for day in (0, 1, 3, 4)] == [2, 2, 5, 5]

    def test_next_new_moon_is_minus_one_without_a_moon(self) -> None:
        assert next_new_moon_day(7, 0) == -1

    def test_the_still_window_starts_at_nightfall(self) -> None:
        assert in_still_window(NIGHT, DAY)
        assert in_still_window(NIGHT + NEW_MOON_STILL_TICKS - 1, DAY)
        assert not in_still_window(NIGHT - 1, DAY)
        assert not in_still_window(NIGHT + NEW_MOON_STILL_TICKS, DAY)

    def test_the_save_sits_inside_the_still_window(self) -> None:
        assert save_tick_of_day(DAY) == NIGHT + NEW_MOON_SAVE_OFFSET
        assert in_still_window(save_tick_of_day(DAY), DAY)


class TestClock:
    def test_the_clock_names_tonights_moon(self) -> None:
        world = _world()
        world.tick = 2 * DAY + 10
        clock = world.clock
        assert clock.new_moon_tonight
        assert clock.next_new_moon_day == 2
        assert clock.save_tick == 0

    def test_the_clock_points_at_the_next_moon(self) -> None:
        world = _world()
        world.tick = 10
        assert not world.clock.new_moon_tonight
        assert world.clock.next_new_moon_day == 2

    def test_a_world_without_a_moon_says_so(self) -> None:
        world = _world(every_days=0)
        assert world.clock.next_new_moon_day == -1


class TestForcedSleep:
    def test_everyone_awake_lies_down(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5, fatigue=1)
        _settler(world, "bram", 7, 7, fatigue=90, food=5)
        world.tick = 2 * DAY + NIGHT
        events = TickEvents()

        apply_new_moon(world, events)

        assert world.get_entity("ada").asleep
        assert world.get_entity("bram").asleep
        # The usual refusals (fatigue floor, hunger) do not apply.
        assert [action.details for action in events.action_results] == [
            "new moon",
            "new moon",
        ]

    def test_it_is_not_a_collapse(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5)
        world.tick = 2 * DAY + NIGHT
        apply_new_moon(world, TickEvents())
        assert not world.get_entity("ada").collapsed

    def test_an_adjacent_free_bed_wins_over_the_ground(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5)
        _bed(world, "bed_9", 5, 6)
        _bed(world, "bed_2", 6, 5)
        world.tick = 2 * DAY + NIGHT
        apply_new_moon(world, TickEvents())
        # Lowest object id of the beds within reach.
        assert world.get_entity("ada").sleeping_on == "bed_2"

    def test_two_settlers_never_share_one_bed(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5)
        _settler(world, "bram", 5, 7)
        _bed(world, "bed_1", 5, 6)
        world.tick = 2 * DAY + NIGHT
        apply_new_moon(world, TickEvents())
        places = sorted(world.get_entity(eid).sleeping_on for eid in ("ada", "bram"))
        assert places == ["", "bed_1"]

    def test_a_bed_already_slept_on_is_not_offered(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5, asleep=True, sleeping_on="bed_1", fatigue=50)
        _settler(world, "bram", 5, 7)
        _bed(world, "bed_1", 5, 6)
        world.tick = 2 * DAY + NIGHT
        apply_new_moon(world, TickEvents())
        assert world.get_entity("bram").sleeping_on == ""

    def test_wolves_and_the_dead_are_left_alone(self) -> None:
        world = _world()
        _settler(world, "wolf_1", 5, 5, entity_type="wolf")
        dead = _settler(world, "ada", 6, 6)
        world.set_entity(dead.as_dead())
        world.tick = 2 * DAY + NIGHT
        apply_new_moon(world, TickEvents())
        assert not world.get_entity("wolf_1").asleep
        assert not world.get_entity("ada").asleep

    def test_nothing_happens_on_an_ordinary_night(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5)
        world.tick = DAY + NIGHT
        apply_new_moon(world, TickEvents())
        assert not world.get_entity("ada").asleep

    def test_nothing_happens_without_a_moon(self) -> None:
        world = _world(every_days=0)
        _settler(world, "ada", 5, 5)
        world.tick = 2 * DAY + NIGHT
        apply_new_moon(world, TickEvents())
        assert not world.get_entity("ada").asleep


class TestConversationsClose:
    def test_every_conversation_ends(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5)
        _settler(world, "bram", 6, 6)
        events = TickEvents()
        world.tick = 2 * DAY + NIGHT - 1
        process_tick(
            world,
            _make_context_with(
                world,
                ConverseIntent(
                    entity_id="ada",
                    action="open",
                    direction=Direction.EAST,
                    text="hello",
                ),
            ),
        )
        assert all_conversations(world)

        world.advance_tick()
        events = TickEvents()
        apply_new_moon(world, events)
        assert all_conversations(world) == []
        assert [removed.object_id for removed in events.objects_removed] == ["conv_1"]


def _make_context_with(world: World, intent: Any) -> TickContext:
    ctx = _context(world)
    ctx.submit_intent(intent.entity_id, intent, enforce_deadline=False)
    return ctx


class TestStillWindow:
    def test_a_rested_sleeper_stays_down(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5, fatigue=0, asleep=True)
        world.tick = 2 * DAY + NIGHT + 1
        assert is_still(world)
        process_tick(world, _context(world))
        assert world.get_entity("ada").asleep

    def test_the_window_ends_and_the_rested_get_up(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5, fatigue=0, asleep=True)
        world.tick = 2 * DAY + NIGHT + NEW_MOON_STILL_TICKS
        assert not is_still(world)
        process_tick(world, _context(world))
        assert not world.get_entity("ada").asleep

    def test_a_wake_intent_is_refused_with_the_moon(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5, fatigue=40, asleep=True)
        world.tick = 2 * DAY + NIGHT + 1
        ctx = _context(world)
        ctx.submit_intent("ada", WakeIntent(entity_id="ada"), enforce_deadline=False)
        result = process_tick(world, ctx)
        refusals = [
            action
            for action in result.action_results
            if action.action_type == "wake" and not action.success
        ]
        assert [action.details for action in refusals] == ["new moon"]
        assert world.get_entity("ada").asleep

    def test_a_due_respawn_is_held_to_the_end_of_the_window(self) -> None:
        world = _world()
        dead = _settler(world, "ada", 5, 5)
        world.settlement = Position(x=10, y=10)
        world.set_entity(dead.as_dead())
        world.detach_entity("ada")
        world.tick = 2 * DAY + NIGHT
        world.mark_death("ada", world.tick - RESPAWN_DELAY_TICKS)

        for _ in range(NEW_MOON_STILL_TICKS):
            events = TickEvents()
            process_respawns(world, events)
            assert events.respawns == []
            world.advance_tick()

        events = TickEvents()
        process_respawns(world, events)
        assert [respawn.entity_id for respawn in events.respawns] == ["ada"]


class TestWolvesLieLow:
    def test_the_night_is_quiet_from_nightfall_to_dawn(self) -> None:
        world = _world()
        for tick_of_day in (NIGHT, NIGHT + 50, DAY - 1):
            world.tick = 2 * DAY + tick_of_day
            assert wolves_lie_low(world)
        world.tick = 2 * DAY + NIGHT - 1
        assert not wolves_lie_low(world)
        world.tick = 3 * DAY
        assert not wolves_lie_low(world)

    def test_no_intent_no_spawn_and_the_rng_is_untouched(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5)
        _settler(world, "wolf_1", 6, 5, entity_type="wolf", health=16, max_health=16)
        simulator = WolfSimulator(seed=7, settings=WolfSettings(max_wolves=3))
        before = simulator.rng_state()

        world.tick = 2 * DAY + NIGHT + 1
        ctx = _context(world)
        simulator.step(world, ctx, TickEvents())

        assert ctx.intents == {}
        assert simulator.rng_state() == before
        assert len(world.all_entities()) == 2

    def test_the_wolf_hunts_again_by_day(self) -> None:
        world = _world()
        _settler(world, "ada", 5, 5)
        _settler(world, "wolf_1", 6, 5, entity_type="wolf", health=16, max_health=16)
        simulator = WolfSimulator(seed=7, settings=WolfSettings(max_wolves=3))

        world.tick = 2 * DAY + 10
        ctx = _context(world)
        simulator.step(world, ctx, TickEvents())
        assert "wolf_1" in ctx.intents


class TestConfigValidation:
    def test_the_defaults_leave_the_world_moonless(self) -> None:
        config = WorldConfig()
        assert config.new_moon_every_days == 0
        assert config.save_on_new_moon is True
        assert config.save_wait_seconds == 180

    def test_a_negative_period_is_refused(self) -> None:
        with pytest.raises(ValueError, match="new_moon_every_days"):
            WorldConfig(new_moon_every_days=-1)

    def test_a_zero_wait_is_refused(self) -> None:
        with pytest.raises(ValueError, match="save_wait_seconds"):
            WorldConfig(save_wait_seconds=0)
