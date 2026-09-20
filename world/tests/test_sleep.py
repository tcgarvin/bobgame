"""Tests for the day clock, fatigue, sleep, collapse and waking (docs/10)."""

from typing import Any

import pytest

from world.events import DamageEvent, TickEvents
from world.recording import tick_record
from world.sleep import (
    COLLAPSE_WAKE_FATIGUE,
    HUNGRY_WAKE_FOOD,
    MIN_SLEEP_FATIGUE,
    RESPAWN_FATIGUE,
    TIRED_FATIGUE,
    is_tired,
    process_fatigue_phase,
    process_sleep_phase,
)
from world.state import Entity, World, WorldObject, night_start_tick
from world.stats import RESPAWN_DELAY_TICKS, process_health_regen, process_respawns
from world.tick import TickConfig, TickContext, process_tick
from world.types import (
    MoveIntent,
    Direction,
    Position,
    SleepIntent,
    WakeIntent,
)
from world.viewer_payload import clock_payload, entity_from_state, entity_state

DAY = 300


def _world(day_length: int = DAY) -> World:
    return World(width=10, height=10, day_length_ticks=day_length)


def _with_settler(world: World, **kwargs: Any) -> Entity:
    entity = Entity(entity_id="bob", position=Position(x=5, y=5), **kwargs)
    world.add_entity(entity)
    return entity


def _bed(world: World, x: int = 5, y: int = 6, object_id: str = "bed_1") -> WorldObject:
    bed = WorldObject(
        object_id=object_id, position=Position(x=x, y=y), object_type="bed"
    )
    world.add_object(bed)
    return bed


def _sleep(world: World, entity_id: str, object_id: str = "") -> TickEvents:
    events = TickEvents()
    process_sleep_phase(
        world,
        {entity_id: SleepIntent(entity_id=entity_id, object_id=object_id)},
        {},
        events,
    )
    return events


def _details(events: TickEvents, action_type: str) -> list[str]:
    return [a.details for a in events.action_results if a.action_type == action_type]


# --- The day clock ---------------------------------------------------------


class TestClock:
    def test_day_and_tick_of_day(self) -> None:
        world = _world()
        world.tick = 742
        clock = world.clock
        assert (clock.day, clock.tick_of_day, clock.day_length) == (2, 142, 300)

    def test_night_starts_two_thirds_through(self) -> None:
        assert night_start_tick(DAY) == 200
        world = _world()
        world.tick = 199
        assert world.clock.night is False
        world.tick = 200
        assert world.clock.night is True
        world.tick = 299
        assert world.clock.night is True
        world.tick = 300
        assert world.clock.night is False
        assert world.clock.day == 1

    def test_short_day_length(self) -> None:
        world = _world(day_length=9)
        world.tick = 6
        assert world.clock.night is True
        world.tick = 5
        assert world.clock.night is False


# --- Fatigue accumulation --------------------------------------------------


class TestFatigueAccumulation:
    def test_day_interval_is_four_ticks(self) -> None:
        world = _world()
        _with_settler(world)
        for tick in (1, 2, 3):
            world.tick = tick
            process_fatigue_phase(world, TickEvents())
        assert world.get_entity("bob").fatigue == 0
        world.tick = 4
        process_fatigue_phase(world, TickEvents())
        assert world.get_entity("bob").fatigue == 1

    def test_night_interval_is_three_ticks(self) -> None:
        world = _world()
        _with_settler(world)
        for tick in (202, 203):
            world.tick = tick
            process_fatigue_phase(world, TickEvents())
        assert world.get_entity("bob").fatigue == 0
        world.tick = 204
        process_fatigue_phase(world, TickEvents())
        assert world.get_entity("bob").fatigue == 1

    def test_wolves_never_tire(self) -> None:
        world = _world()
        world.add_entity(
            Entity(
                entity_id="wolf_1",
                position=Position(x=1, y=1),
                entity_type="wolf",
            )
        )
        world.tick = 6
        process_fatigue_phase(world, TickEvents())
        assert world.get_entity("wolf_1").fatigue == 0

    def test_is_tired_threshold(self) -> None:
        fresh = Entity(entity_id="a", position=Position(x=0, y=0), fatigue=59)
        tired = Entity(
            entity_id="b", position=Position(x=1, y=0), fatigue=TIRED_FATIGUE
        )
        assert is_tired(fresh) is False
        assert is_tired(tired) is True

    def test_tired_settlers_do_not_regenerate(self) -> None:
        world = _world()
        _with_settler(world, health=10, food=90, fatigue=TIRED_FATIGUE)
        world.tick = 5
        process_health_regen(world)
        assert world.get_entity("bob").health == 10

    def test_fresh_settlers_still_regenerate(self) -> None:
        world = _world()
        _with_settler(world, health=10, food=90, fatigue=TIRED_FATIGUE - 1)
        world.tick = 5
        process_health_regen(world)
        assert world.get_entity("bob").health == 11


# --- Falling asleep --------------------------------------------------------


class TestSleepIntent:
    def test_sleep_on_the_ground(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40)
        events = _sleep(world, "bob")
        bob = world.get_entity("bob")
        assert bob.asleep is True
        assert bob.sleeping_on == ""
        assert bob.collapsed is False
        assert _details(events, "sleep") == ["asleep on the ground"]

    def test_sleep_on_an_adjacent_bed(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40)
        _bed(world)
        events = _sleep(world, "bob", "bed_1")
        assert world.get_entity("bob").sleeping_on == "bed_1"
        assert _details(events, "sleep") == ["asleep on bed_1"]

    def test_distant_bed_is_refused(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40)
        _bed(world, x=0, y=0)
        events = _sleep(world, "bob", "bed_1")
        assert _details(events, "sleep") == ["bed_1 is not adjacent"]
        assert world.get_entity("bob").asleep is False

    def test_non_bed_object_is_refused(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40)
        world.add_object(
            WorldObject(
                object_id="chest_1", position=Position(x=5, y=6), object_type="chest"
            )
        )
        events = _sleep(world, "bob", "chest_1")
        assert _details(events, "sleep") == ["chest_1 is not a bed"]

    def test_missing_object_is_refused(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40)
        events = _sleep(world, "bob", "bed_9")
        assert _details(events, "sleep") == ["no object bed_9"]

    def test_too_hungry_to_sleep(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, food=0)
        events = _sleep(world, "bob")
        assert _details(events, "sleep") == [
            "too hungry to sleep: food 0, and a sleeper wakes at food 20"
        ]

    def test_too_hungry_to_sleep_at_the_wake_threshold(self) -> None:
        """The refusal and the wake share one number (hamlet round 2)."""
        world = _world()
        _with_settler(world, fatigue=40, food=HUNGRY_WAKE_FOOD)
        events = _sleep(world, "bob")
        assert _details(events, "sleep") == [
            f"too hungry to sleep: food {HUNGRY_WAKE_FOOD}, and a sleeper "
            f"wakes at food {HUNGRY_WAKE_FOOD}"
        ]
        assert world.get_entity("bob").asleep is False

    def test_sleeps_just_above_the_threshold(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, food=HUNGRY_WAKE_FOOD + 1)
        _sleep(world, "bob")
        assert world.get_entity("bob").asleep is True

    def test_not_tired(self) -> None:
        world = _world()
        _with_settler(world, fatigue=0)
        events = _sleep(world, "bob")
        assert _details(events, "sleep") == [
            "not tired enough to sleep: fatigue 0, and sleep needs fatigue 20"
        ]

    def test_below_the_minimum_fatigue_is_refused(self) -> None:
        """A settler took ten sleeps of one or two ticks at fatigue 1."""
        world = _world()
        _with_settler(world, fatigue=MIN_SLEEP_FATIGUE - 1)
        events = _sleep(world, "bob")
        assert _details(events, "sleep") == [
            f"not tired enough to sleep: fatigue {MIN_SLEEP_FATIGUE - 1}, "
            f"and sleep needs fatigue {MIN_SLEEP_FATIGUE}"
        ]
        assert not world.get_entity("bob").asleep

    def test_at_the_minimum_fatigue_it_sleeps(self) -> None:
        world = _world()
        _with_settler(world, fatigue=MIN_SLEEP_FATIGUE)
        _sleep(world, "bob")
        assert world.get_entity("bob").asleep

    def test_bed_contention_smallest_id_wins(self) -> None:
        world = _world()
        world.add_entity(
            Entity(entity_id="ada", position=Position(x=5, y=5), fatigue=40)
        )
        world.add_entity(
            Entity(entity_id="zed", position=Position(x=5, y=7), fatigue=40)
        )
        _bed(world, x=5, y=6)
        events = TickEvents()
        process_sleep_phase(
            world,
            {
                "zed": SleepIntent(entity_id="zed", object_id="bed_1"),
                "ada": SleepIntent(entity_id="ada", object_id="bed_1"),
            },
            {},
            events,
        )
        assert world.get_entity("ada").sleeping_on == "bed_1"
        assert world.get_entity("zed").asleep is False
        assert _details(events, "sleep") == ["asleep on bed_1", "bed_1 is taken"]

    def test_bed_already_occupied(self) -> None:
        world = _world()
        world.add_entity(
            Entity(
                entity_id="ada",
                position=Position(x=5, y=5),
                fatigue=40,
                asleep=True,
                sleeping_on="bed_1",
            )
        )
        world.add_entity(
            Entity(entity_id="zed", position=Position(x=5, y=7), fatigue=40)
        )
        _bed(world, x=5, y=6)
        events = _sleep(world, "zed", "bed_1")
        assert _details(events, "sleep") == ["bed_1 is taken"]


# --- Recovery --------------------------------------------------------------


class TestRecovery:
    @pytest.mark.parametrize(
        "sleeping_on,tick,expected",
        [
            ("bed_1", 201, 39),  # bed at night: one per tick
            ("bed_1", 202, 39),
            ("", 201, 38),  # ground at night: two per three ticks
            ("", 202, 40),
            ("bed_1", 101, 40),  # bed by day: two per three ticks
            ("bed_1", 102, 38),
            ("", 102, 39),  # ground by day: one per two ticks
            ("", 103, 40),
        ],
    )
    def test_recovery_rates(self, sleeping_on: str, tick: int, expected: int) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True, sleeping_on=sleeping_on)
        _bed(world)
        world.tick = tick
        process_fatigue_phase(world, TickEvents())
        assert world.get_entity("bob").fatigue == expected

    def test_bed_sleeper_heals_regardless_of_food(self) -> None:
        world = _world()
        _with_settler(
            world, fatigue=40, health=10, food=1, asleep=True, sleeping_on="bed_1"
        )
        _bed(world)
        world.tick = 105
        process_fatigue_phase(world, TickEvents())
        process_health_regen(world)
        assert world.get_entity("bob").health == 11

    def test_ground_sleeper_does_not_get_the_bed_heal(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, health=10, food=1, asleep=True)
        world.tick = 105
        process_fatigue_phase(world, TickEvents())
        process_health_regen(world)
        assert world.get_entity("bob").health == 10


# --- Waking ----------------------------------------------------------------


class TestWaking:
    def test_wake_rested(self) -> None:
        world = _world()
        _with_settler(world, fatigue=1, asleep=True, sleeping_on="bed_1")
        _bed(world)
        world.tick = 201
        events = TickEvents()
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is False
        assert _details(events, "wake") == ["woke up: rested"]

    def test_wake_damaged(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True)
        world.tick = 7
        events = TickEvents()
        events.damage_events.append(
            DamageEvent(
                entity_id="bob",
                attacker_id="wolf_1",
                amount=3,
                remaining_health=17,
                position=Position(x=5, y=5),
            )
        )
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is False
        assert _details(events, "wake") == ["woke up: damaged"]

    def test_wake_hungry(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, food=0, asleep=True)
        world.tick = 7
        events = TickEvents()
        process_fatigue_phase(world, events)
        assert _details(events, "wake") == ["woke up: hungry"]

    def test_wake_at_the_hungry_threshold(self) -> None:
        """A sleeper wakes at food 20, not at food 0 (hamlet round 2).

        The old rule let a settler sleep from food 39 through the night and
        die four ticks after waking.
        """
        world = _world()
        _with_settler(world, fatigue=40, food=HUNGRY_WAKE_FOOD, asleep=True)
        world.tick = 7
        events = TickEvents()
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is False
        assert _details(events, "wake") == ["woke up: hungry"]

    def test_sleeps_on_above_the_hungry_threshold(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, food=HUNGRY_WAKE_FOOD + 1, asleep=True)
        world.tick = 7
        events = TickEvents()
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is True

    def test_collapsed_sleeper_ignores_hunger(self) -> None:
        world = _world()
        _with_settler(world, fatigue=90, food=0, asleep=True, collapsed=True)
        world.tick = 7
        events = TickEvents()
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is True

    def test_wake_bed_removed(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True, sleeping_on="bed_1")
        world.tick = 7
        events = TickEvents()
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is False
        assert _details(events, "wake") == ["woke up: bed removed"]

    def test_wake_asked(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True)
        events = TickEvents()
        process_sleep_phase(world, {}, {"bob": WakeIntent(entity_id="bob")}, events)
        assert world.get_entity("bob").asleep is False
        assert _details(events, "wake") == ["woke up: asked"]

    def test_wake_intent_while_awake_fails(self) -> None:
        world = _world()
        _with_settler(world)
        events = TickEvents()
        process_sleep_phase(world, {}, {"bob": WakeIntent(entity_id="bob")}, events)
        assert _details(events, "wake") == ["not asleep"]


# --- Collapse --------------------------------------------------------------


class TestCollapse:
    def test_collapse_at_max_fatigue(self) -> None:
        world = _world()
        _with_settler(world, fatigue=99)
        world.tick = 4
        events = TickEvents()
        process_fatigue_phase(world, events)
        bob = world.get_entity("bob")
        assert bob.asleep is True
        assert bob.collapsed is True
        assert bob.sleeping_on == ""
        assert _details(events, "collapse") == ["collapsed from exhaustion"]

    def test_collapsed_sleeper_ignores_damage(self) -> None:
        world = _world()
        _with_settler(world, fatigue=100, asleep=True, collapsed=True)
        world.tick = 7
        events = TickEvents()
        events.damage_events.append(
            DamageEvent(
                entity_id="bob",
                attacker_id="wolf_1",
                amount=3,
                remaining_health=17,
                position=Position(x=5, y=5),
            )
        )
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is True
        assert _details(events, "wake") == []

    def test_collapsed_sleeper_ignores_starvation(self) -> None:
        world = _world()
        _with_settler(world, fatigue=100, food=0, asleep=True, collapsed=True)
        world.tick = 7
        events = TickEvents()
        process_fatigue_phase(world, events)
        assert world.get_entity("bob").asleep is True

    def test_collapsed_sleeper_wakes_at_seventy(self) -> None:
        world = _world()
        _with_settler(
            world, fatigue=COLLAPSE_WAKE_FATIGUE + 2, asleep=True, collapsed=True
        )
        # Ground, at night: two fatigue shed on every third tick.
        world.tick = 201
        events = TickEvents()
        process_fatigue_phase(world, events)
        bob = world.get_entity("bob")
        assert bob.fatigue == COLLAPSE_WAKE_FATIGUE
        assert bob.asleep is False
        assert _details(events, "wake") == ["woke up: rested"]

    def test_collapsed_sleeper_cannot_be_asked_to_wake(self) -> None:
        world = _world()
        _with_settler(world, fatigue=100, asleep=True, collapsed=True)
        events = TickEvents()
        process_sleep_phase(world, {}, {"bob": WakeIntent(entity_id="bob")}, events)
        assert world.get_entity("bob").asleep is True
        assert _details(events, "wake") == ["collapsed"]


# --- The asleep guard ------------------------------------------------------


def _context(world: World) -> TickContext:
    return TickContext(tick_id=world.tick, start_time_ms=0, deadline_ms=0, world=world)


class TestAsleepGuard:
    def test_submit_intent_refuses_a_sleeper(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True)
        ctx = _context(world)
        accepted, reason = ctx.submit_intent(
            "bob",
            MoveIntent(entity_id="bob", direction=Direction.NORTH),
            enforce_deadline=False,
        )
        assert (accepted, reason) == (False, "asleep")

    def test_submit_intent_allows_wake(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True)
        ctx = _context(world)
        accepted, reason = ctx.submit_intent(
            "bob", WakeIntent(entity_id="bob"), enforce_deadline=False
        )
        assert (accepted, reason) == (True, "")

    def test_sleeper_does_not_move(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True)
        ctx = _context(world)
        ctx.intents["bob"] = MoveIntent(entity_id="bob", direction=Direction.NORTH)
        result = process_tick(world, ctx)
        assert result.move_results == []
        assert world.get_entity("bob").position == Position(x=5, y=5)

    def test_other_phases_report_asleep(self) -> None:
        world = _world()
        _with_settler(world, fatigue=40, asleep=True)
        ctx = _context(world)
        ctx.intents["bob"] = SleepIntent(entity_id="bob")
        result = process_tick(world, ctx)
        assert [
            (a.action_type, a.success, a.details)
            for a in result.action_results
            if a.action_type == "sleep"
        ] == [("sleep", False, "asleep")]


# --- Respawn ---------------------------------------------------------------


class TestRespawnFatigue:
    def test_respawn_resets_fatigue_and_sleep(self) -> None:
        world = _world()
        bob = _with_settler(world, fatigue=100, asleep=True, collapsed=True)
        world.set_entity(bob.as_dead())
        world.detach_entity("bob")
        world.mark_death("bob", 0)
        world.tick = RESPAWN_DELAY_TICKS
        process_respawns(world, TickEvents())
        respawned = world.get_entity("bob")
        assert respawned.fatigue == RESPAWN_FATIGUE
        assert respawned.asleep is False
        assert respawned.collapsed is False


# --- Payload and recording -------------------------------------------------


class TestPayloads:
    def test_entity_state_carries_the_body_clock(self) -> None:
        entity = Entity(
            entity_id="bob",
            position=Position(x=1, y=2),
            fatigue=42,
            asleep=True,
            sleeping_on="bed_1",
        )
        state = entity_state(entity)
        assert state["fatigue"] == 42
        assert state["max_fatigue"] == 100
        assert state["asleep"] is True
        assert state["sleeping_on"] == "bed_1"
        assert entity_from_state(state) == entity

    def test_entity_from_state_reads_the_legacy_hunger_keys(self) -> None:
        """Payloads recorded before 2026-09-18 call the food stat `hunger`."""
        entity = Entity(entity_id="bob", position=Position(x=1, y=2), food=37)
        state = entity_state(entity)
        state["hunger"] = state.pop("food")
        state["max_hunger"] = state.pop("max_food")

        rebuilt = entity_from_state(state)
        assert rebuilt.food == 37
        assert rebuilt.max_food == entity.max_food

    def test_entity_from_state_ignores_a_legacy_open_to_talk_key(self) -> None:
        """Runs recorded before the invitation mechanic went carry the key."""
        entity = Entity(entity_id="bob", position=Position(x=1, y=2))
        state = entity_state(entity)
        state["open_to_talk"] = True

        assert entity_from_state(state) == entity

    def test_clock_payload_shape(self) -> None:
        world = _world()
        world.tick = 250
        assert clock_payload(world.clock) == {
            "day": 0,
            "tick_of_day": 250,
            "day_length": 300,
            "night": True,
            "new_moon_tonight": False,
            "next_new_moon_day": -1,
            "save_tick": 0,
        }

    def test_tick_record_carries_the_clock(self) -> None:
        world = _world()
        _with_settler(world, fatigue=12)
        world.tick = 249
        ctx = _context(world)
        result = process_tick(world, ctx)
        record = tick_record(result, world, wall_ms=1)
        assert record["clock"] == {
            "day": 0,
            "tick_of_day": 249,
            "day_length": 300,
            "night": True,
            "new_moon_tonight": False,
            "next_new_moon_day": -1,
            "save_tick": 0,
        }
        assert record["entity_updates"][0]["fatigue"] == 13

    def test_meta_records_day_length(self, tmp_path: Any) -> None:
        import json

        from world.recording import RunRecorder

        world = _world(day_length=120)
        recorder = RunRecorder(
            run_dir=tmp_path / "run",
            run_id="test",
            config_name="test",
            config_path="",
            world=world,
            tick_config=TickConfig(),
        )
        recorder.start()
        recorder.close()
        meta = json.loads((tmp_path / "run" / "meta.json").read_text())
        assert meta["day_length_ticks"] == 120
