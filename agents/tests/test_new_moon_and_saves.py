"""The new moon, draining, snapshots and resuming (docs/14)."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import AsyncIterator, Mapping, Sequence

import pytest

from agents import world_pb2 as pb
from agents.jev_agent import items
from agents.jev_agent.agent import (
    ASLEEP_REJECTION,
    MODE_CONVERSATION,
    MODE_PLANNING,
    SAVE_WAIT_ENV,
    DrainState,
    JevAgent,
    save_wait_seconds,
)
from agents.jev_agent.conversation import END_NEW_MOON
from agents.jev_agent.jevstate import build_state
from agents.jev_agent.planner import settlement_narrative, turn_clock_line
from agents.jev_agent.reflex import ReflexBrief
from agents.jev_agent.snapshot import (
    SNAPSHOT_FORMAT_VERSION,
    AgentSnapshot,
    SnapshotError,
    capture,
    read_snapshot,
    restore,
    snapshot_path,
    write_snapshot,
)
from agents.jev_agent.build import BUILD_OUT_OF_ITEMS
from agents.jev_agent.recipes import CraftTally, craft_chain, craft_once
from agents.jev_agent.stint import Brief
from agents.jev_agent.worldmodel import TileInfo, WorldClock, WorldModel

from helpers import (
    FakeConverser,
    FakeJevClient,
    acted_event,
    converse_object,
    damaged_event,
    died_event,
    make_clock,
    make_entity,
    make_object,
    make_observation,
    utterance_event,
)
from test_agent import FakeJournalWriter, FakeWorldClient, build_agent


NIGHT_START = items.night_start_tick(items.DEFAULT_DAY_LENGTH_TICKS)


def moon_clock(
    tick: int, *, tonight: bool = False, next_day: int = -1, save_tick: int = 0
) -> pb.WorldClock:
    """A clock carrying the new-moon and save fields."""
    return make_clock(
        tick,
        new_moon_tonight=tonight,
        next_new_moon_day=next_day,
        save_tick=save_tick,
    )


# --- what the settlers are told (docs/14 section 1) -------------------------


def test_the_clock_says_the_night_a_new_moon_falls_on() -> None:
    clock = WorldClock(day=1, tick_of_day=10, day_length=300, next_new_moon_day=2)

    assert clock.moon_text() == "next new moon: night of day 2"


def test_the_clock_says_tonight_and_the_tick_everyone_falls_asleep() -> None:
    clock = WorldClock(day=2, tick_of_day=10, day_length=300, new_moon_tonight=True)

    assert clock.moon_text() == (
        f"new moon tonight (everyone falls asleep at tick-of-day {NIGHT_START})"
    )


def test_a_world_without_a_new_moon_says_nothing_about_one() -> None:
    assert WorldClock(day=1, day_length=300).moon_text() == ""


def test_the_tool_result_clock_line_carries_tonight_s_new_moon(
    model: WorldModel,
) -> None:
    model.update(
        make_observation(
            40, make_entity("ada", (10, 10)), clock=moon_clock(40, tonight=True)
        )
    )

    line = turn_clock_line(model, 30)

    assert line.endswith(
        f"new moon tonight (everyone falls asleep at tick-of-day {NIGHT_START})]"
    )


def test_the_tool_result_clock_line_carries_the_next_new_moon(
    model: WorldModel,
) -> None:
    model.update(
        make_observation(
            40, make_entity("ada", (10, 10)), clock=moon_clock(40, next_day=5)
        )
    )

    assert turn_clock_line(model, 30).endswith("next new moon: night of day 5]")


def test_jev_is_told_about_the_new_moon_only_on_the_day(model: WorldModel) -> None:
    model.update(
        make_observation(
            40, make_entity("ada", (10, 10)), clock=moon_clock(40, next_day=5)
        )
    )
    quiet = build_state(model, instruction="go", success_condition="never")

    model.update(
        make_observation(
            41, make_entity("ada", (10, 10)), clock=moon_clock(41, tonight=True)
        )
    )
    tonight = build_state(model, instruction="go", success_condition="never")

    assert not any("new moon" in fact for fact in quiet["facts"])
    assert any("new moon tonight" in fact for fact in tonight["facts"])


def test_the_narrative_states_the_new_moon_physics() -> None:
    narrative = settlement_narrative(6)

    assert f"tick-of-day {NIGHT_START}" in narrative
    assert str(items.NEW_MOON_STILL_TICKS) in narrative
    assert "Every open conversation closes on that tick." in narrative
    assert "Wolves do not hunt from that tick until dawn" in narrative


# --- draining on a forced sleep (docs/14 section 2) -------------------------


def sleeping_script(
    asleep_from: int,
    count: int,
    *,
    tonight: bool = True,
    objects_by_tick: Mapping[int, Sequence[pb.WorldObject]] | None = None,
    events_by_tick: Mapping[int, Sequence[pb.ObservationEvent]] | None = None,
    save_tick: int = 0,
) -> list[pb.Observation]:
    """Ada awake, then asleep from `asleep_from` on a new-moon night."""
    return [
        make_observation(
            tick,
            make_entity("ada", (10, 10), fatigue=50, asleep=tick >= asleep_from),
            objects=list((objects_by_tick or {}).get(tick, ())),
            events=list((events_by_tick or {}).get(tick, ())),
            clock=moon_clock(
                tick,
                tonight=tonight,
                save_tick=save_tick if tick == save_tick else 0,
            ),
        )
        for tick in range(1, count + 1)
    ]


async def test_a_forced_sleep_mid_stint_ends_the_stint_with_the_new_moon(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(sleeping_script(3, 5))
    agent = build_agent(world, FakeJevClient(default_action="move_E"), tmp_path)
    reasons: list[str] = []

    async def plan() -> None:
        report = await agent.run_stint(Brief("Walk east", "never", 20))
        reasons.append(report.end_reason)
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert reasons == ["new_moon"]
    assert agent.mode == MODE_PLANNING


async def test_a_forced_sleep_mid_turn_answers_the_action_in_flight(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(sleeping_script(3, 5))
    agent = build_agent(world, FakeJevClient(), tmp_path)
    results: list[str] = []

    async def plan() -> None:
        results.append(await agent.wait_ticks(10))
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert results and results[0].endswith("failed: asleep")
    assert agent.planner.deps.budget.left == 0, "the turn is over"


async def test_a_forced_sleep_mid_conversation_closes_the_seat(
    tmp_path: Path,
) -> None:
    seated = converse_object("conv_1", (11, 10), ["mira", "ada"], speaker="mira")
    world = FakeWorldClient(
        sleeping_script(
            4,
            6,
            objects_by_tick={tick: [seated] for tick in (1, 2, 3)},
            events_by_tick={
                2: [acted_event("ada", "converse", True, "hailed conv_1 mira")]
            },
        )
    )
    agent = build_agent(world, FakeJevClient(), tmp_path)
    agent.converser = FakeConverser(note_text="mira wants planks")
    modes: list[str] = []

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    original = agent._handle_tick

    async def spy(observation: pb.Observation) -> None:
        await original(observation)
        modes.append(agent.mode)

    agent._handle_tick = spy  # type: ignore[method-assign]
    await agent.run()
    await asyncio.sleep(0)

    assert MODE_CONVERSATION in modes
    assert agent.mode == MODE_PLANNING
    notes = agent.drain_notes()
    assert any(f"ended because: {END_NEW_MOON}" in note for note in notes)
    assert any("closed every conversation" in note for note in notes)


async def test_a_forced_sleep_mid_reflex_ends_the_reflex_stint(
    tmp_path: Path,
) -> None:
    script = [
        make_observation(
            tick,
            make_entity("ada", (10, 10), fatigue=50, asleep=tick >= 3),
            entities=(
                [make_entity("wolf_1", (11, 10), entity_type="wolf")]
                if tick == 2
                else []
            ),
            events=[damaged_event("ada", "wolf_1", 3, 17)] if tick == 2 else [],
            clock=moon_clock(tick, tonight=True),
        )
        for tick in range(1, 5)
    ]
    agent = build_agent(
        FakeWorldClient(script), FakeJevClient(default_action="wait"), tmp_path
    )
    agent.set_reflex(
        ReflexBrief(
            instruction="Deal with the wolf",
            success_condition="it is gone",
            max_ticks=30,
            trigger_distance=4,
        )
    )

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()

    assert agent.mode == MODE_PLANNING
    assert any("reflex ran" in note for note in agent.drain_notes())


# --- the drained predicate (docs/14 section 2) ------------------------------


async def drained_agent(tmp_path: Path, tick: int = 4) -> JevAgent:
    """An agent whose body is asleep and whose planner is parked."""
    agent = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path)
    agent.model.update(
        make_observation(
            tick,
            make_entity("ada", (10, 10), fatigue=40, asleep=True),
            clock=moon_clock(tick, tonight=True),
        )
    )
    # The planner parks itself here on every tick it cannot act.
    asyncio.create_task(agent.await_active())
    await asyncio.sleep(0)
    return agent


async def test_an_asleep_settler_with_nothing_outstanding_is_drained(
    tmp_path: Path,
) -> None:
    agent = await drained_agent(tmp_path)

    assert agent.drained() == DrainState(drained=True, reason="")


async def test_an_awake_settler_is_not_drained(tmp_path: Path) -> None:
    agent = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path)
    agent.model.update(make_observation(4, make_entity("ada", (10, 10))))

    assert agent.drained().reason == "the body is awake"


async def test_a_settler_whose_planner_has_not_parked_is_not_drained(
    tmp_path: Path,
) -> None:
    agent = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path)
    agent.model.update(make_observation(4, make_entity("ada", (10, 10), asleep=True)))

    assert agent.drained().reason == "the planner turn has not ended yet"


async def test_a_stint_asked_for_by_a_sleeper_is_refused_not_queued(
    tmp_path: Path,
) -> None:
    """A multi-phase `build` kept asking after the sleep drain had run once.

    `_drain_for_sleep` sweeps the queues on the tick the body falls asleep;
    the tool's own Python loop then called `run_stint` for its next phase, the
    request sat in the queue all night, and `drained()` answered "a stint is
    queued", which abandoned the whole save.
    """
    agent = await drained_agent(tmp_path)

    report = await agent.run_stint(Brief("go", "never", 3))

    assert report.end_reason == END_NEW_MOON
    assert report.ticks_used == 0
    assert agent.drained() == DrainState(drained=True, reason="")


async def test_every_request_a_sleeper_makes_is_refused_at_once(
    tmp_path: Path,
) -> None:
    """Every entry point on the bridge, so no loop can park on one all night."""
    agent = await drained_agent(tmp_path)

    outcome = await agent.direct_action(pb.Intent(wait=pb.WaitIntent()), "wait")
    assert outcome.not_run == ASLEEP_REJECTION
    assert "failed: asleep" in await agent.wait_ticks(2)
    assert await agent.await_conversation() is None
    assert agent.drained() == DrainState(drained=True, reason="")


async def test_a_builds_resupply_rounds_stop_on_a_sleepers_refused_stint(
    tmp_path: Path,
) -> None:
    """`build` runs up to three stints per call; each refusal must end one.

    The tool's loop breaks on any end reason other than `build_out_of_items`,
    so the refused report is what gets it out of the shape and out of the
    call.
    """
    agent = await drained_agent(tmp_path)
    brief = Brief("lay the road", "every tile has a road", 60)

    reasons = [(await agent.run_stint(brief)).end_reason for _ in range(3)]

    assert reasons == [END_NEW_MOON, END_NEW_MOON, END_NEW_MOON]
    assert reasons[0] != BUILD_OUT_OF_ITEMS
    assert agent.drained() == DrainState(drained=True, reason="")


async def test_a_craft_loop_forced_asleep_stops_on_the_first_refusal(
    tmp_path: Path,
) -> None:
    """`craft_once` repeats its action per unit of work; one refusal ends it."""
    agent = await drained_agent(tmp_path)

    text = await craft_once(agent, items.PLANK, items.RECIPES[items.PLANK])

    assert text.count(ASLEEP_REJECTION) == 1
    assert agent.drained() == DrainState(drained=True, reason="")


async def test_a_craft_chain_forced_asleep_gives_up_rather_than_spinning(
    tmp_path: Path,
) -> None:
    """The chain's `while` loop ends when a craft makes nothing (docs/14)."""
    agent = await drained_agent(tmp_path)
    tally = CraftTally()

    await craft_chain(agent, items.PLANK, 4, tally)

    assert tally.total == 0
    assert tally.stopped
    assert agent.drained() == DrainState(drained=True, reason="")


def test_the_drain_wait_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SAVE_WAIT_ENV, "12.5")
    assert save_wait_seconds() == 12.5

    monkeypatch.setenv(SAVE_WAIT_ENV, "not a number")
    assert save_wait_seconds() == 170.0


# --- the snapshot (docs/14 section 3) ---------------------------------------


def lived_in_model() -> WorldModel:
    """A model with something in every one of its memories."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            10,
            make_entity("ada", (10, 10), inventory={"wood": 3}, fatigue=44),
            objects=[
                make_object("bush_1", "bush", (11, 10), {"berry_count": "1"}),
                make_object(
                    "sign_1",
                    "sign",
                    (12, 10),
                    {"text": "east", "author": "bo", "tick": "4"},
                ),
            ],
            entities=[make_entity("bo", (12, 12))],
            events=[
                acted_event("ada", "extract", True, "chopped tree_1"),
                utterance_event("bo", "hello", (12, 12)),
                damaged_event("ada", "wolf_1", 3, 17),
            ],
            clock=moon_clock(10, tonight=True),
        )
    )
    model.update(
        make_observation(
            11,
            make_entity("ada", (10, 10), inventory={"wood": 3}, fatigue=45),
            entities=[make_entity("wolf_1", (12, 10), entity_type="wolf")],
            events=[died_event("wolf_1", "ada")],
            clock=moon_clock(11, tonight=True),
        )
    )
    model.mark_board_read("board_1")
    return model


def test_the_world_model_survives_a_round_trip() -> None:
    model = lived_in_model()

    copy = WorldModel.from_payload(model.to_payload())

    assert copy.to_payload() == model.to_payload()
    assert copy.tiles == model.tiles
    assert copy.objects == model.objects
    assert copy.entities == model.entities
    assert copy.self_info == model.self_info
    assert copy.clock == model.clock
    assert list(copy.history) == list(model.history)
    assert list(copy.heard) == list(model.heard)
    assert list(copy.damage_log) == list(model.damage_log)
    assert list(copy.deaths_seen) == list(model.deaths_seen)
    assert copy.sign_texts_read == model.sign_texts_read
    assert copy.last_observed_tick == model.last_observed_tick


def test_the_restored_model_answers_walkability_from_rebuilt_indexes() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            3,
            make_entity("ada", (10, 10)),
            objects=[make_object("tree_1", "tree", (11, 10))],
            entities=[make_entity("bo", (9, 10))],
        )
    )

    copy = WorldModel.from_payload(model.to_payload())

    assert not copy.is_walkable((11, 10)), "the tree still blocks"
    assert not copy.is_walkable((9, 10)), "bo still stands there"
    assert copy.is_walkable((10, 11))


async def test_every_scalar_survives_a_snapshot_round_trip(tmp_path: Path) -> None:
    agent = await drained_agent(tmp_path)
    agent.planner.turn = 17
    agent.planner.last_thought = "Wood next."
    agent.planner.journal_sections = {"Me": "A builder."}
    agent.planner.day_log.add(9, "event", "you lay down to sleep")
    agent.ledger.add_planner({"cost_usd": 0.5})
    agent.ledger.add_jev(1000)
    agent.set_reflex(ReflexBrief("Run", "safe", 20, 5, places={"home": (10, 10)}))
    agent.reflex_watch.note_end(9)
    agent._note_for_planner("[a note nobody has read]")

    snapshot = capture(agent)
    written = tmp_path / "saves" / "tick-4" / "agents" / "ada.json.gz"
    write_snapshot(written, snapshot)
    read_back = read_snapshot(written, "ada")

    twin = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path / "twin")
    restore(twin, read_back)

    assert twin.planner.turn == 17
    assert twin.planner.last_thought == "Wood next."
    assert twin.planner.journal_sections == {"Me": "A builder."}
    assert twin.planner.day_log.to_payload() == agent.planner.day_log.to_payload()
    assert {row["text"] for row in twin.planner.day_log.to_payload()} == {
        "you lay down to sleep",
        "[a note nobody has read]",
    }
    assert twin.ledger.to_payload() == agent.ledger.to_payload()
    assert twin.reflex.places == {"home": (10, 10)}
    assert twin.reflex_watch.last_end_tick == 9
    assert twin.drain_notes() == ["[a note nobody has read]"]
    assert twin.drain_notes(for_prompt=True) == ["[a note nobody has read]"]
    assert twin.model.tick == agent.model.tick


async def test_the_partial_file_is_gone_once_the_snapshot_is_written(
    tmp_path: Path,
) -> None:
    agent = await drained_agent(tmp_path)
    path = tmp_path / "saves" / "tick-4" / "agents" / "ada.json.gz"

    write_snapshot(path, capture(agent))

    assert path.exists()
    assert not path.with_name(path.name + ".partial").exists()


def test_reading_a_snapshot_of_another_format_version_fails(tmp_path: Path) -> None:
    path = tmp_path / "ada.json.gz"
    payload = AgentSnapshot(
        format_version=SNAPSHOT_FORMAT_VERSION + 1,
        entity_id="ada",
        tick=4,
        world_model=WorldModel("ada").to_payload(),
        planner={},
        cost_ledger={},
        reflex_watch={},
        sleep={},
        notes_for_tools=[],
        notes_for_prompt=[],
    )
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(payload.model_dump_json())

    with pytest.raises(SnapshotError, match="format version"):
        read_snapshot(path, "ada")


def test_reading_another_settler_s_snapshot_fails(tmp_path: Path) -> None:
    path = tmp_path / "bo.json.gz"
    write_snapshot(
        path,
        AgentSnapshot(
            entity_id="bo",
            tick=4,
            world_model=WorldModel("bo").to_payload(),
            planner={},
            cost_ledger={},
            reflex_watch={},
            sleep={},
            notes_for_tools=[],
            notes_for_prompt=[],
        ),
    )

    with pytest.raises(SnapshotError, match="not 'ada'"):
        read_snapshot(path, "ada")


def test_restoring_another_settler_s_snapshot_fails(tmp_path: Path) -> None:
    agent = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path)
    snapshot = AgentSnapshot(
        entity_id="bo",
        tick=4,
        world_model=WorldModel("bo").to_payload(),
        planner={},
        cost_ledger={},
        reflex_watch={},
        sleep={},
        notes_for_tools=[],
        notes_for_prompt=[],
    )

    with pytest.raises(SnapshotError, match="not 'ada'"):
        restore(agent, snapshot)


def test_a_hundred_thousand_tiles_fit_in_a_small_file(tmp_path: Path) -> None:
    """The tile memory of a settler that has walked a long way, measured."""
    model = WorldModel("ada")
    model.tick = 5000
    model.tiles = {
        (x, y): TileInfo(
            position=(x, y),
            walkable=(x + y) % 7 != 0,
            opaque=(x + y) % 11 == 0,
            floor_type="grass" if (x + y) % 3 else "shallow_water",
            last_seen=(x * y) % 5000,
        )
        for x in range(1000, 1320)
        for y in range(900, 1220)
    }
    assert len(model.tiles) == 102_400
    snapshot = AgentSnapshot(
        entity_id="ada",
        tick=5000,
        world_model=model.to_payload(),
        planner={},
        cost_ledger={},
        reflex_watch={},
        sleep={},
        notes_for_tools=[],
        notes_for_prompt=[],
    )
    path = tmp_path / "ada.json.gz"

    write_snapshot(path, snapshot)

    size = path.stat().st_size
    assert size < 3_000_000, f"102,400 tiles cost {size} bytes"
    assert read_snapshot(path, "ada").world_model["tiles"]["x"][:1] == [1000]


# --- resuming (docs/14 section 4) -------------------------------------------


def test_a_repeated_observation_is_folded_as_a_refresh() -> None:
    model = WorldModel("ada")
    observation = make_observation(
        30,
        make_entity("ada", (10, 10)),
        events=[
            acted_event("ada", "extract", True, "chopped tree_1"),
            utterance_event("bo", "hello", (12, 12)),
        ],
    )
    model.update(observation)
    history = list(model.history)
    heard = list(model.heard)

    digest = model.update(observation)

    assert digest.repeated
    assert not digest.own_actions and not digest.utterances
    assert list(model.history) == history
    assert list(model.heard) == heard


async def test_the_re_delivered_tick_adds_no_day_log_or_trace_entries(
    tmp_path: Path,
) -> None:
    """A resumed agent is handed tick T again; it must count for nothing."""
    saved = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path)
    observation = make_observation(
        30,
        make_entity("ada", (10, 10), fatigue=40, asleep=True),
        events=[acted_event("ada", "sleep", True, "the ground")],
        clock=moon_clock(30, tonight=True),
    )
    saved._model.update(observation)
    snapshot = capture(saved)

    resumed = build_agent(
        FakeWorldClient([observation]), FakeJevClient(), tmp_path / "resumed"
    )
    restore(resumed, snapshot)

    async def idle() -> None:
        await asyncio.sleep(3600)

    resumed.planner.run = idle  # type: ignore[method-assign]
    await resumed.run()
    resumed.trace.close()

    assert resumed.planner.day_log.to_payload() == []
    assert not (tmp_path / "resumed" / "agent-ada" / "stints.jsonl.gz").exists() or (
        stint_events(tmp_path / "resumed" / "agent-ada") == []
    )


def stint_events(directory: Path) -> list[str]:
    """The `event` field of every record in the stints trace."""
    path = directory / "stints.jsonl.gz"
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line)["event"] for line in handle if line.strip()]


async def test_a_resumed_settler_wakes_into_a_journal_fed_turn(
    tmp_path: Path,
) -> None:
    saved = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path)
    asleep = make_observation(
        30,
        make_entity("ada", (10, 10), fatigue=40, asleep=True),
        clock=moon_clock(30, tonight=True),
    )
    saved._model.update(asleep)
    saved.planner.turn = 9
    saved.planner.history = ["a message from before the night"]  # type: ignore[list-item]
    snapshot = capture(saved)

    woke = acted_event("ada", "wake", True, "rested")
    script = [asleep] + [
        make_observation(
            tick,
            make_entity("ada", (10, 10), fatigue=0),
            events=[woke] if tick == 31 else [],
            clock=moon_clock(tick),
        )
        # A few quiet ticks after the wake, so the planner task gets its turn
        # before the fake observation stream runs out.
        for tick in range(31, 35)
    ]
    resumed = build_agent(
        FakeWorldClient(script), FakeJevClient(), tmp_path / "resumed"
    )
    restore(resumed, snapshot)
    prompts: list[str] = []

    async def plan() -> None:
        await resumed.await_active()
        prompts.append(await resumed.planner.build_prompt())
        await asyncio.sleep(3600)

    resumed.planner.run = plan  # type: ignore[method-assign]
    await resumed.run()

    assert resumed.planner.history == [], "no message history crosses the night"
    assert resumed.planner.turn == 9, "the turn counter continues"
    assert prompts, "the planner takes its first turn once the body is awake"
    assert "Your journal:" in prompts[0]


async def test_the_save_tick_writes_the_snapshot_where_the_world_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOBGAME_RUN_DIR", str(tmp_path))
    monkeypatch.setenv(SAVE_WAIT_ENV, "5")
    world = FakeWorldClient(sleeping_script(2, 6, save_tick=5))
    agent = build_agent(world, FakeJevClient(), tmp_path / "agents")
    agent.journal_writer = FakeJournalWriter()

    async def plan() -> None:
        # What the real planner loop does: park whenever the body cannot act.
        while True:
            await agent.await_active()
            await asyncio.sleep(0)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()
    agent.trace.close()

    written = snapshot_path(tmp_path / "saves" / "tick-5", "ada")
    assert written.exists()
    assert read_snapshot(written, "ada").tick == 5


async def test_a_settler_that_is_not_drained_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BOBGAME_RUN_DIR", str(tmp_path))
    monkeypatch.setenv(SAVE_WAIT_ENV, "0.05")
    agent = build_agent(FakeWorldClient([]), FakeJevClient(), tmp_path / "agents")
    agent.model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            clock=moon_clock(5, tonight=True, save_tick=5),
        )
    )

    await agent._maybe_save(5)
    agent.trace.close()

    assert not snapshot_path(tmp_path / "saves" / "tick-5", "ada").exists()
    events = planner_events(tmp_path / "agents" / "agent-ada")
    skipped = [line for line in events if line["event"] == "save_skipped"]
    assert skipped and skipped[0]["reason"] == "the body is awake"


def planner_events(directory: Path) -> list[dict]:
    """Every record in the planner trace."""
    with gzip.open(directory / "planner.jsonl.gz", "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def test_the_journal_writer_is_told_about_the_new_moon(tmp_path: Path) -> None:
    writer = FakeJournalWriter()
    world = FakeWorldClient(sleeping_script(2, 4))
    agent = build_agent(world, FakeJevClient(), tmp_path)
    agent.journal_writer = writer

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()
    await asyncio.sleep(0)

    assert writer.clock_facts == [
        f"new moon tonight (everyone falls asleep at tick-of-day {NIGHT_START})"
    ]


async def test_capturing_a_snapshot_does_not_consume_the_pending_notes(
    tmp_path: Path,
) -> None:
    """The run carries on after a save, still owing the planner its notes."""
    agent = await drained_agent(tmp_path)
    agent._note_for_planner("[a sign said: east]")

    snapshot = capture(agent)

    assert snapshot.notes_for_tools == ["[a sign said: east]"]
    assert agent.drain_notes() == ["[a sign said: east]"]
