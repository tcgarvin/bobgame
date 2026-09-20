"""A save is an exact continuation (docs/14, section 5).

The determinism test runs one world for fifty ticks against a scripted intent
script, and the same world snapshotted at tick k, reloaded and run on with the
same script. Every `TickResult` and the whole final state must match.
"""

import json
from pathlib import Path
from typing import Any, Callable

from world.crafting import RECIPES
from world.recording import RunRecorder
from world.snapshot import (
    load_world_snapshot,
    prepare_save_dir,
    restore_wolf_simulator,
    restore_world,
    write_world_snapshot,
)
from world.state import Entity, Inventory, World, WorldObject, night_start_tick
from world.tick import TickConfig, TickContext, process_tick
from world.types import (
    ConverseIntent,
    CraftIntent,
    Direction,
    DropIntent,
    EntityIntent,
    MoveIntent,
    Position,
    SayIntent,
    WaitIntent,
)
from world.wolves import WolfSettings, WolfSimulator

DAY = 120
NIGHT = night_start_tick(DAY)
TICKS = 50
SNAPSHOT_AT = 17

SETTLERS = ("ada", "bram", "cleo", "dov")


def _build_world() -> World:
    """A small world holding one of everything a save has to carry.

    Wolves on with a fixed seed, a conversation about to open, craft progress
    on a workshop table, a dead settler awaiting respawn, a hail cooldown, an
    item pile and a placed wall.
    """
    world = World(width=30, height=30, day_length_ticks=DAY, new_moon_every_days=4)
    world.settlement = Position(x=15, y=15)

    world.add_entity(
        Entity(
            entity_id="ada",
            position=Position(x=10, y=10),
            inventory=Inventory(items=(("wood", 8), ("fiber", 6))),
            food=70,
            fatigue=25,
        )
    )
    world.add_entity(Entity(entity_id="bram", position=Position(x=11, y=10), food=64))
    world.add_entity(
        Entity(
            entity_id="cleo",
            position=Position(x=12, y=12),
            last_conversation_end_tick=3,
        )
    )
    dov = Entity(entity_id="dov", position=Position(x=20, y=20))
    world.add_entity(dov)
    world.set_entity(dov.as_dead())
    world.detach_entity("dov")
    world.mark_death("dov", 2)

    world.add_entity(
        Entity(
            entity_id="wolf_1",
            position=Position(x=18, y=10),
            entity_type="wolf",
            health=16,
            max_health=16,
            food=100,
        )
    )

    world.add_object(
        WorldObject(
            object_id="table_1",
            position=Position(x=10, y=11),
            object_type="workshop_table",
            state=(("craft:ada", "rope:1"),),
        )
    )
    world.add_object(
        WorldObject(
            object_id="wall_1",
            position=Position(x=14, y=10),
            object_type="wood_wall",
        )
    )
    world.add_object(
        WorldObject(
            object_id="pile_1",
            position=Position(x=9, y=9),
            object_type="item_pile",
            state=(("contents", "stone:3"),),
        )
    )
    world.add_object(
        WorldObject(
            object_id="bush_1",
            position=Position(x=9, y=11),
            object_type="bush",
            state=(("berry_count", "1"),),
        )
    )
    world.set_object_id_seq(11)
    return world


def _script(tick: int) -> list[EntityIntent]:
    """The intents submitted on `tick`, the same in both runs."""
    intents: list[EntityIntent] = []
    if tick == 1:
        intents.append(
            ConverseIntent(
                entity_id="ada",
                action="open",
                direction=Direction.SOUTH,
                text="shall we build",
            )
        )
    elif tick == 2:
        intents.append(
            ConverseIntent(entity_id="bram", action="join", conversation_id="conv_1")
        )
    elif tick in (4, 8, 12):
        intents.append(
            ConverseIntent(entity_id="ada", action="speak", text=f"line {tick}")
        )
    elif tick in (5, 9):
        intents.append(ConverseIntent(entity_id="bram", action="speak", text="aye"))
    elif tick in (20, 21, 22):
        intents.append(CraftIntent(entity_id="ada", recipe="rope"))
    elif tick == 25:
        intents.append(DropIntent(entity_id="ada", kind="wood", amount=2))
    elif tick in (30, 31, 32):
        intents.append(MoveIntent(entity_id="cleo", direction=Direction.NORTH))
    elif tick == 35:
        intents.append(SayIntent(entity_id="bram", channel="shout", text="a wolf!"))
    else:
        intents.append(WaitIntent(entity_id="ada"))
    return intents


def _run(
    world: World,
    simulator: WolfSimulator,
    start: int,
    stop: int,
    script: Callable[[int], list[EntityIntent]],
) -> list[dict[str, Any]]:
    """Run ticks [start, stop) and return a comparable digest of each result."""
    digests = []
    for tick in range(start, stop):
        assert world.tick == tick
        ctx = TickContext(tick_id=tick, start_time_ms=0, deadline_ms=0, world=world)
        for intent in script(tick):
            ctx.submit_intent(intent.entity_id, intent, enforce_deadline=False)
        result = process_tick(world, ctx, simulator)
        digests.append(_result_digest(result))
        world.advance_tick()
    return digests


def _result_digest(result: Any) -> dict[str, Any]:
    """Everything about a TickResult that must not drift; no wall times."""
    return {
        "tick": result.tick_id,
        "moves": [
            (m.entity_id, m.success, str(m.from_pos), str(m.to_pos), m.failure_reason)
            for m in result.move_results
        ],
        "actions": [
            (a.entity_id, a.action_type, a.success, a.details)
            for a in result.action_results
        ],
        "object_changes": [
            (c.object_id, c.field, c.old_value, c.new_value)
            for c in result.object_changes
        ],
        "objects_added": [a.obj.object_id for a in result.objects_added],
        "objects_removed": [r.object_id for r in result.objects_removed],
        "damage": [
            (d.entity_id, d.attacker_id, d.amount) for d in result.damage_events
        ],
        "deaths": [(d.entity_id, d.killer_id) for d in result.deaths],
        "respawns": [(r.entity_id, str(r.position)) for r in result.respawns],
        "utterances": [
            (u.speaker_id, u.channel, u.text, u.conversation_id)
            for u in result.utterances
        ],
        "spawned": [(s.entity_id, str(s.position)) for s in result.entities_spawned],
        "despawned": [d.entity_id for d in result.entities_despawned],
    }


def _world_digest(world: World, simulator: WolfSimulator) -> dict[str, Any]:
    """The whole world state, in a form two runs can be compared on."""
    return {
        "tick": world.tick,
        "entities": [e.model_dump() for e in world.all_entities().values()],
        "objects": [
            (o.object_id, str(o.position), o.object_type, sorted(o.state))
            for o in world.all_objects().values()
        ],
        "object_id_seq": world.object_id_seq,
        "death_ticks": dict(world.pending_respawns()),
        "rng": simulator.rng_state(),
        "wolf_id_counter": simulator.id_counter,
    }


class TestDeterminism:
    def test_a_snapshot_continues_the_run_exactly(self, tmp_path: Path) -> None:
        # The uninterrupted run.
        straight_world = _build_world()
        straight_sim = WolfSimulator(seed=4242, settings=WolfSettings(max_wolves=2))
        straight = _run(straight_world, straight_sim, 0, TICKS, _script)

        # The same run, saved at SNAPSHOT_AT and reloaded.
        saved_world = _build_world()
        saved_sim = WolfSimulator(seed=4242, settings=WolfSettings(max_wolves=2))
        first_half = _run(saved_world, saved_sim, 0, SNAPSHOT_AT, _script)

        save_dir = prepare_save_dir(tmp_path, saved_world.tick)
        write_world_snapshot(
            saved_world,
            save_dir=save_dir,
            run_id="run",
            config={},
            config_name="hamlet",
            config_path="",
            map_path="",
            map_sha256="",
            wolf_simulator=saved_sim,
        )

        snapshot = load_world_snapshot(save_dir)
        resumed_world = World(width=30, height=30)
        restore_world(snapshot, save_dir, resumed_world)
        resumed_sim = WolfSimulator(seed=1, settings=WolfSettings(max_wolves=2))
        restore_wolf_simulator(snapshot, resumed_sim)

        second_half = _run(resumed_world, resumed_sim, SNAPSHOT_AT, TICKS, _script)

        assert first_half + second_half == straight
        assert _world_digest(resumed_world, resumed_sim) == _world_digest(
            straight_world, straight_sim
        )

    def test_the_scripted_run_actually_exercises_everything(self) -> None:
        """A guard: the fixture would be worthless if nothing ever happened."""
        world = _build_world()
        simulator = WolfSimulator(seed=4242, settings=WolfSettings(max_wolves=2))
        digests = _run(world, simulator, 0, TICKS, _script)

        actions = {
            (entry[1], entry[2]) for digest in digests for entry in digest["actions"]
        }
        assert ("converse", True) in actions
        assert ("craft", True) in actions
        assert ("drop", True) in actions
        assert ("say", True) in actions
        assert any(digest["respawns"] for digest in digests)
        assert "rope" in RECIPES


class TestResumeMeta:
    def test_meta_json_names_the_parent_run(self, tmp_path: Path) -> None:
        world = _build_world()
        world.tick = 900
        recorder = RunRecorder(
            run_dir=tmp_path / "child",
            run_id="20260921-000000-hamlet",
            config_name="hamlet",
            config_path="world/configs/hamlet.toml",
            world=world,
            tick_config=TickConfig(),
            parent_run_id="20260920-000000-hamlet",
            resumed_from_tick=900,
        )
        recorder.start()
        recorder.close()

        meta = json.loads((tmp_path / "child" / "meta.json").read_text())
        assert meta["parent_run_id"] == "20260920-000000-hamlet"
        assert meta["resumed_from_tick"] == 900
        # The baseline is the restored object set, not an empty world.
        assert meta["world_size"] == {"width": 30, "height": 30}

    def test_a_fresh_run_has_no_parent(self, tmp_path: Path) -> None:
        recorder = RunRecorder(
            run_dir=tmp_path / "fresh",
            run_id="r",
            config_name="hamlet",
            config_path="",
            world=_build_world(),
            tick_config=TickConfig(),
        )
        recorder.start()
        recorder.close()
        meta = json.loads((tmp_path / "fresh" / "meta.json").read_text())
        assert meta["parent_run_id"] is None
        assert meta["resumed_from_tick"] is None
