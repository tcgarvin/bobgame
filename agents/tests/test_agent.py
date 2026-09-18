"""The tick loop: one intent per tick, and the planner handshake."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import AsyncIterator, Mapping, Sequence

import pytest

from agents import world_pb2 as pb
from agents.jev_agent.agent import (
    MODE_CONVERSATION,
    MODE_PLANNING,
    MODE_REFLEX,
    MODE_STINT,
    JevAgent,
)
from agents.jev_agent.client import IntentResult
from agents.jev_agent.options import Option
from agents.jev_agent.pricing import JEV_USD_PER_MILLION_INPUT_TOKENS
from agents.jev_agent.reflex import ReflexBrief
from agents.jev_agent.stint import Brief, DriverChoice

from helpers import (
    FakeConverser,
    FakeJevClient,
    acted_event,
    converse_object,
    damaged_event,
    make_entity,
    make_observation,
)


class FakeWorldClient:
    """Replays a fixed observation script and records everything submitted."""

    def __init__(self, observations: Sequence[pb.Observation]) -> None:
        self._observations = list(observations)
        self.lease_id = "lease-1"
        self.submitted: list[pb.Intent] = []
        self.statuses: list[tuple[str, str, str, str]] = []
        self.closed = False

    async def acquire_lease(self) -> None:
        return None

    async def run_lease_renewal(self) -> None:
        while True:
            await asyncio.sleep(3600)

    async def observations(self) -> AsyncIterator[pb.Observation]:
        for observation in self._observations:
            # Yield control so the planner task can make progress between ticks,
            # exactly as it would while waiting on the real stream.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            yield observation

    async def submit_intent(self, tick_id: int, intent: pb.Intent) -> IntentResult:
        self.submitted.append(intent)
        return IntentResult(accepted=True, reason="")

    async def report_status(self, *status: str) -> bool:
        self.statuses.append(tuple(status))  # type: ignore[arg-type]
        return True

    async def close(self) -> None:
        self.closed = True


def observations(count: int, **kwargs: object) -> list[pb.Observation]:
    """`count` plain observations for `ada`, standing still."""
    return [
        make_observation(tick, make_entity("ada", (10, 10)), **kwargs)  # type: ignore[arg-type]
        for tick in range(1, count + 1)
    ]


def build_agent(world: FakeWorldClient, jev: FakeJevClient, tmp_path: Path) -> JevAgent:
    """A JevAgent wired to fakes and a throwaway log directory."""
    return JevAgent(
        world,  # type: ignore[arg-type]
        jev,
        "ada",
        log_root=tmp_path,
        planner_model="test",
    )


def test_starting_an_agent_records_the_prices_it_is_billed_at(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    agent = build_agent(FakeWorldClient([]), fake_jev, tmp_path)

    payload = json.loads(agent.trace.pricing_path.read_text(encoding="utf-8"))

    assert payload == {
        "jev_usd_per_million_input_tokens": JEV_USD_PER_MILLION_INPUT_TOKENS,
        "planner_model": "test",
    }


async def test_planning_mode_waits_when_the_planner_asks_for_nothing(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    world = FakeWorldClient(observations(3))
    agent = build_agent(world, fake_jev, tmp_path)

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()

    assert len(world.submitted) == 3
    assert all(intent.HasField("wait") for intent in world.submitted)
    assert agent.mode == MODE_PLANNING
    assert not fake_jev.calls, "Jev is not consulted outside a stint"


async def test_a_new_planner_thought_is_said_once_on_the_thought_channel(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    world = FakeWorldClient(observations(3))
    agent = build_agent(world, fake_jev, tmp_path)

    async def speak() -> None:
        agent.set_thought("Heading east for wood.")
        await asyncio.sleep(3600)

    agent.planner.run = speak  # type: ignore[method-assign]
    await agent.run()

    said = [intent for intent in world.submitted if intent.HasField("say")]
    assert len(said) == 1
    assert said[0].say.channel == "thought"
    assert said[0].say.text == "Heading east for wood."


async def test_start_stint_runs_to_completion_while_the_tick_loop_keeps_going(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="move_E")
    world = FakeWorldClient(observations(6))
    agent = build_agent(world, jev, tmp_path)
    reports = []

    async def plan() -> None:
        reports.append(
            await agent.run_stint(
                Brief(
                    instruction="Walk east",
                    success_condition="you are 3 tiles east",
                    max_ticks=2,
                )
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    moves = [intent for intent in world.submitted if intent.HasField("move")]
    assert len(moves) == 2, "the stint spends exactly its tick budget"
    assert len(jev.calls) == 2
    assert len(reports) == 1
    assert reports[0].ticks_used == 2
    assert reports[0].end_reason == "ticks_exhausted"
    assert agent.mode == MODE_PLANNING, "control returns to the planner"


async def test_the_agent_reports_stint_mode_while_jev_holds_the_controls(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(observations(6))
    agent = build_agent(world, jev, tmp_path)
    modes: list[str] = []

    async def plan() -> None:
        await agent.run_stint(
            Brief(instruction="Stand guard", success_condition="never", max_ticks=3)
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    original = agent._handle_tick

    async def spy(observation: pb.Observation) -> None:
        await original(observation)
        modes.append(agent.mode)

    agent._handle_tick = spy  # type: ignore[method-assign]
    await agent.run()

    assert MODE_STINT in modes
    assert any(status[0] == MODE_STINT for status in world.statuses)


async def test_a_direct_action_is_submitted_and_its_outcome_returned(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    chopped = acted_event("ada", "extract", True, "chopped tree_1 (+1 wood)")
    # The world reports the chop on whichever tick follows the submission, so
    # every later observation carries it.
    script = [make_observation(1, make_entity("ada", (10, 10)))] + [
        make_observation(
            tick, make_entity("ada", (10, 10), inventory={"wood": 1}), events=[chopped]
        )
        for tick in (2, 3, 4)
    ]
    world = FakeWorldClient(script)
    agent = build_agent(world, fake_jev, tmp_path)
    outcomes: list[str] = []

    async def plan() -> None:
        outcomes.append(
            await agent.direct_action(
                pb.Intent(extract=pb.ExtractIntent(object_id="tree_1")),
                "extract tree_1",
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert any(intent.HasField("extract") for intent in world.submitted)
    assert outcomes == ["extract tree_1 -> extract ok: chopped tree_1 (+1 wood)"]


async def test_wait_ticks_holds_position_for_the_requested_ticks(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    world = FakeWorldClient(observations(6))
    agent = build_agent(world, fake_jev, tmp_path)
    outcomes: list[str] = []

    async def plan() -> None:
        outcomes.append(await agent.wait_ticks(3))
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert outcomes and outcomes[0].startswith("wait 3 ticks ->")
    assert all(intent.HasField("wait") for intent in world.submitted)


async def test_a_rejected_intent_is_logged_but_does_not_stop_the_loop(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    world = FakeWorldClient(observations(3))

    async def reject(tick_id: int, intent: pb.Intent) -> IntentResult:
        world.submitted.append(intent)
        return IntentResult(accepted=False, reason="late_tick")

    world.submit_intent = reject  # type: ignore[method-assign]
    agent = build_agent(world, fake_jev, tmp_path)

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()

    assert len(world.submitted) == 3


async def test_pending_planner_work_is_released_when_the_agent_stops(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    world = FakeWorldClient(observations(1))
    agent = build_agent(world, fake_jev, tmp_path)
    failures: list[str] = []

    async def plan() -> None:
        try:
            await agent.run_stint(
                Brief(instruction="x", success_condition="y", max_ticks=99)
            )
        except RuntimeError as error:
            failures.append(str(error))

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()
    await asyncio.sleep(0)

    assert agent.mode in (MODE_PLANNING, MODE_STINT)


@pytest.mark.parametrize("ticks", [1, 4])
async def test_exactly_one_intent_is_submitted_per_tick(
    fake_jev: FakeJevClient, tmp_path: Path, ticks: int
) -> None:
    world = FakeWorldClient(observations(ticks))
    agent = build_agent(world, fake_jev, tmp_path)

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()
    assert len(world.submitted) == ticks


async def test_the_agent_writes_its_trace_files_under_the_log_root(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="move_E")
    world = FakeWorldClient(observations(6))
    agent = build_agent(world, jev, tmp_path)

    async def plan() -> None:
        await agent.run_stint(
            Brief(
                instruction="Walk east",
                success_condition="you are 2 tiles east",
                max_ticks=2,
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()
    agent.trace.close()

    directory = tmp_path / "agent-ada"
    assert agent.planner.memory_path == directory / "memory.md"
    with gzip.open(directory / "stints.jsonl.gz", "rt", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert [line.get("event", "record") for line in lines] == [
        "stint_start",
        "record",
        "record",
        "stint_end",
    ]
    assert {line["stint_id"] for line in lines} == {"ada-1"}
    assert (directory / "jev_states.jsonl.gz").exists()


async def test_the_log_root_falls_back_to_the_run_directory(
    fake_jev: FakeJevClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOBGAME_RUN_DIR", str(tmp_path / "run"))
    agent = JevAgent(
        FakeWorldClient([]),  # type: ignore[arg-type]
        fake_jev,
        "ada",
        planner_model="test",
    )
    try:
        assert agent.log_root == tmp_path / "run" / "agents"
        assert agent.trace.directory == tmp_path / "run" / "agents" / "agent-ada"
    finally:
        agent.trace.close()


# -- reflex and conversation modes (docs/09) ---------------------------------

GUARD_REFLEX = ReflexBrief(
    instruction="Back away to the settlement and keep your weapon up.",
    success_condition="no wolf is in view",
    max_ticks=30,
    trigger_distance=4,
)


def wolf_observations(
    wolf_ticks: Sequence[int],
    count: int,
    *,
    objects_by_tick: Mapping[int, Sequence[pb.WorldObject]] | None = None,
    events_by_tick: Mapping[int, Sequence[pb.ObservationEvent]] | None = None,
) -> list[pb.Observation]:
    """`count` observations for ada, with a wolf next to her on `wolf_ticks`."""
    script: list[pb.Observation] = []
    for tick in range(1, count + 1):
        entities = (
            [make_entity("wolf_1", (11, 10), entity_type="wolf")]
            if tick in wolf_ticks
            else []
        )
        script.append(
            make_observation(
                tick,
                make_entity("ada", (10, 10)),
                entities=entities,
                objects=list((objects_by_tick or {}).get(tick, ())),
                events=list((events_by_tick or {}).get(tick, ())),
            )
        )
    return script


class EastDriver:
    """A `StintDriver` that walks east for ever, so a stint has to be stopped."""

    name = "east"

    def stop_reason(self, model: object) -> str:
        """Never stops on its own."""
        return ""

    def choose(self, model: object) -> DriverChoice:
        """One step east."""
        return DriverChoice(
            option=Option(
                key="east_step",
                description="walk east",
                intent=pb.Intent(move=pb.MoveIntent(direction=pb.EAST)),
            )
        )

    def summary(self) -> str:
        """Nothing worth reporting."""
        return "EAST"


async def test_a_reflex_interrupts_an_in_flight_direct_action(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(wolf_observations([2, 3], 8))
    agent = build_agent(world, jev, tmp_path)
    agent.set_reflex(GUARD_REFLEX)
    outcomes: list[str] = []
    modes: list[str] = []

    async def plan() -> None:
        outcomes.append(
            await agent.direct_action(
                pb.Intent(extract=pb.ExtractIntent(object_id="tree_1")),
                "extract tree_1",
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    original = agent._handle_tick

    async def spy(observation: pb.Observation) -> None:
        await original(observation)
        modes.append(agent.mode)

    agent._handle_tick = spy  # type: ignore[method-assign]
    await agent.run()

    assert outcomes == ["extract tree_1 -> interrupted: reflex stint started"]
    assert MODE_REFLEX in modes
    assert jev.calls, "Jev runs the reflex brief"
    assert jev.last_state["brief"]["instruction"] == GUARD_REFLEX.instruction


async def test_a_direct_action_asked_for_during_a_reflex_is_refused_at_once(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(wolf_observations([1, 2, 3], 6))
    agent = build_agent(world, jev, tmp_path)
    agent.set_reflex(GUARD_REFLEX)
    outcomes: list[str] = []

    async def plan() -> None:
        await asyncio.sleep(0)
        for _ in range(3):
            outcomes.append(await agent.wait_ticks(1))
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert any("interrupted: reflex stint started" in line for line in outcomes)


async def test_a_reflex_ends_a_driver_stint_and_reports_both(tmp_path: Path) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(wolf_observations([3], 12))
    agent = build_agent(world, jev, tmp_path)
    agent.set_reflex(GUARD_REFLEX)
    reports: list[object] = []

    async def plan() -> None:
        reports.append(
            await agent.run_stint(
                Brief(
                    instruction="Walk east",
                    success_condition="never",
                    max_ticks=50,
                ),
                EastDriver(),
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert len(reports) == 1, "the tool call returns once both have finished"
    report = reports[0]
    assert report.end_reason == "reflex"  # type: ignore[attr-defined]
    text = report.to_text()  # type: ignore[attr-defined]
    assert "[reflex ran ticks" in text
    assert "threat_gone" in text


async def test_an_ordinary_jev_stint_is_never_pre_empted(tmp_path: Path) -> None:
    jev = FakeJevClient(default_action="wait")
    # The wolf only turns up once Jev already has the controls.
    world = FakeWorldClient(wolf_observations([3, 4, 5], 8))
    agent = build_agent(world, jev, tmp_path)
    agent.set_reflex(GUARD_REFLEX)
    reports: list[object] = []
    modes: list[str] = []

    async def plan() -> None:
        reports.append(
            await agent.run_stint(
                Brief(instruction="Hold", success_condition="never", max_ticks=5)
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    original = agent._handle_tick

    async def spy(observation: pb.Observation) -> None:
        await original(observation)
        modes.append(agent.mode)

    agent._handle_tick = spy  # type: ignore[method-assign]
    await agent.run()

    assert MODE_REFLEX not in modes
    assert reports[0].end_reason == "ticks_exhausted"  # type: ignore[attr-defined]


async def test_a_reflex_pauses_a_conversation_and_hands_it_back(
    tmp_path: Path,
) -> None:
    seated = converse_object("conv_1", (11, 10), ["mira", "ada"], speaker="mira")
    world = FakeWorldClient(
        wolf_observations(
            [4],
            9,
            objects_by_tick={tick: [seated] for tick in range(1, 10)},
            events_by_tick={2: [acted_event("ada", "converse", True, "join conv_1")]},
        )
    )
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser()
    agent.set_reflex(GUARD_REFLEX)
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

    assert MODE_CONVERSATION in modes
    assert MODE_REFLEX in modes
    assert modes[-1] == MODE_CONVERSATION, "the seat is picked up again"


async def test_a_join_during_a_stint_ends_it_and_appends_the_conversation(
    tmp_path: Path,
) -> None:
    seated = converse_object("conv_1", (11, 10), ["mira", "ada"], speaker="mira")
    objects = {1: [seated], 2: [seated], 3: [seated], 4: [seated]}
    world = FakeWorldClient(
        wolf_observations(
            [],
            8,
            objects_by_tick=objects,
            events_by_tick={2: [acted_event("ada", "converse", True, "join conv_1")]},
        )
    )
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser(note_text="mira wants planks")
    reports: list[object] = []

    async def plan() -> None:
        reports.append(
            await agent.run_stint(
                Brief(
                    instruction="Look around", success_condition="never", max_ticks=30
                )
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()
    await asyncio.sleep(0)

    assert len(reports) == 1
    report = reports[0]
    assert report.end_reason == "joined_conversation"  # type: ignore[attr-defined]
    text = report.to_text()  # type: ignore[attr-defined]
    assert "CONVERSATION REPORT: conv_1" in text
    assert "mira wants planks" in text


# -- sleep (docs/10_metal_and_sleep.md) --------------------------------------


def sleeping_observations(
    asleep_ticks: Sequence[int],
    count: int,
    *,
    events_by_tick: Mapping[int, Sequence[pb.ObservationEvent]] | None = None,
) -> list[pb.Observation]:
    """`count` observations for ada, asleep on the ticks listed."""
    return [
        make_observation(
            tick,
            make_entity("ada", (10, 10), fatigue=50, asleep=tick in asleep_ticks),
            events=list((events_by_tick or {}).get(tick, ())),
        )
        for tick in range(1, count + 1)
    ]


def stint_lines(directory: Path) -> list[dict]:
    """Every record in the agent's stints trace."""
    with gzip.open(directory / "stints.jsonl.gz", "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def test_a_sleeping_agent_submits_nothing_and_asks_nobody(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(sleeping_observations([2, 3, 4], 5))
    agent = build_agent(world, jev, tmp_path)
    turns: list[int] = []

    async def plan() -> None:
        turns.append(1)
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert len(world.submitted) == 2, "only the two awake ticks act"
    assert not jev.calls


async def test_a_stint_resumes_on_the_tick_the_settler_wakes(tmp_path: Path) -> None:
    jev = FakeJevClient(default_action="move_E")
    world = FakeWorldClient(sleeping_observations([2, 3], 5))
    agent = build_agent(world, jev, tmp_path)

    async def plan() -> None:
        await agent.run_stint(
            Brief(instruction="Walk east", success_condition="never", max_ticks=10)
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    # Ticks 1, 4 and 5 act; 2 and 3 are slept through.
    assert len(jev.calls) == 3
    assert len(world.submitted) == 3
    assert agent.mode == MODE_STINT


async def test_the_sleep_and_the_wake_are_traced_with_the_reason(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    woke = acted_event("ada", "wake", True, "woke up: damaged")
    fell = acted_event("ada", "sleep", True, "asleep on bed_1")
    world = FakeWorldClient(
        sleeping_observations([2, 3], 4, events_by_tick={2: [fell], 4: [woke]})
    )
    agent = build_agent(world, fake_jev, tmp_path)

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()
    agent.trace.close()

    lines = stint_lines(tmp_path / "agent-ada")
    start = next(line for line in lines if line["event"] == "sleep_start")
    end = next(line for line in lines if line["event"] == "sleep_end")
    assert start["where"] == "asleep on bed_1"
    assert start["tick"] == 2
    assert end["reason"] == "woke up: damaged"
    assert end["ticks_slept"] == 2


async def test_the_reflex_waits_for_the_wake_and_fires_on_that_tick(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="wait")
    bitten = damaged_event("ada", "wolf_1", 3, 17)
    script = [
        make_observation(
            tick,
            make_entity("ada", (10, 10), fatigue=50, asleep=tick in (2, 3)),
            entities=(
                [make_entity("wolf_1", (11, 10), entity_type="wolf")]
                if tick >= 3
                else []
            ),
            events=[bitten] if tick == 4 else [],
        )
        for tick in range(1, 7)
    ]
    world = FakeWorldClient(script)
    agent = build_agent(world, jev, tmp_path)
    agent.set_reflex(GUARD_REFLEX)
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

    assert modes[1] != MODE_REFLEX and modes[2] != MODE_REFLEX, "asleep is quiet"
    assert modes[3] == MODE_REFLEX, "the reflex runs on the tick it wakes"


async def test_the_sleep_tool_returns_when_the_settler_wakes(tmp_path: Path) -> None:
    fell = acted_event("ada", "sleep", True, "asleep on the ground")
    woke = acted_event("ada", "wake", True, "woke up: rested")
    world = FakeWorldClient(
        sleeping_observations([2, 3, 4], 6, events_by_tick={2: [fell], 5: [woke]})
    )
    jev = FakeJevClient(default_action="wait")
    agent = build_agent(world, jev, tmp_path)
    results: list[str] = []

    async def plan() -> None:
        outcome = await agent.direct_action(
            pb.Intent(sleep=pb.SleepIntent()), "sleep on the ground"
        )
        results.append(outcome)
        results.append(await agent.await_wake(1))
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert "sleep ok" in results[0]
    assert "woke because woke up: rested" in results[1]
    assert "fatigue 50 -> 50" in results[1]


async def test_a_wake_intent_is_the_one_thing_a_sleeper_may_submit(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(sleeping_observations([2, 3, 4], 5))
    jev = FakeJevClient(default_action="wait")
    agent = build_agent(world, jev, tmp_path)
    outcomes: list[str] = []

    async def plan() -> None:
        await asyncio.sleep(0)
        outcomes.append(
            await agent.direct_action(pb.Intent(wake=pb.WakeIntent()), "wake up")
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert any(intent.HasField("wake") for intent in world.submitted)


async def test_a_sleeper_holds_the_wake_and_refuses_other_queued_actions(
    tmp_path: Path,
) -> None:
    """Regression: the Intent oneof is `action`; reading it wrongly crashed agents."""
    from agents.jev_agent.agent import _DirectRequest

    world = FakeWorldClient(sleeping_observations([], 1))
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    loop = asyncio.get_running_loop()
    move = _DirectRequest(
        intent=pb.Intent(move=pb.MoveIntent(direction=pb.NORTH)),
        description="move north",
        future=loop.create_future(),
    )
    wake = _DirectRequest(
        intent=pb.Intent(wake=pb.WakeIntent()),
        description="wake up",
        future=loop.create_future(),
    )
    agent._direct_requests.put_nowait(move)
    agent._direct_requests.put_nowait(wake)

    picked = agent._wake_request_while_asleep()

    assert picked is wake
    assert move.future.result() == "move north -> failed: asleep"


async def test_the_status_report_carries_the_running_cost(tmp_path: Path) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(observations(4))
    agent = build_agent(world, jev, tmp_path)

    async def plan() -> None:
        await agent.run_stint(
            Brief(instruction="Stand guard", success_condition="never", max_ticks=2)
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    costs = [json.loads(status[4]) for status in world.statuses]
    assert costs, "every status report carries a cost block"
    assert costs[-1]["jev_calls"] == len(jev.calls) == 2
    assert costs[-1]["jev_usd"] > costs[0]["jev_usd"], "Jev ticks cost money"
    assert costs[0]["jev_calls"] == 1, "the first report follows the first Jev tick"
    assert costs[-1]["total_usd"] == costs[-1]["jev_usd"]


# -- invitations: a conversation that starts by itself (docs/09 section 8.3) --


def accepted_observations(count: int, *, seated_until: int) -> list[pb.Observation]:
    """ada is pulled into conv_1 at tick 2, which closes after `seated_until`."""
    seated = converse_object("conv_1", (11, 10), ["mira", "ada"], speaker="mira")
    return [
        make_observation(
            tick,
            make_entity("ada", (10, 10)),
            objects=[seated] if tick <= seated_until else [],
            events=(
                [acted_event("ada", "converse", True, "join conv_1")]
                if tick == 2
                else []
            ),
        )
        for tick in range(1, count + 1)
    ]


async def test_a_conversation_that_starts_by_itself_interrupts_a_direct_action(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(accepted_observations(8, seated_until=4))
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser()
    outcomes: list[str] = []

    async def plan() -> None:
        outcomes.append(
            await agent.direct_action(
                pb.Intent(eat=pb.EatIntent(item_type="berry", amount=1)), "eat berry"
            )
        )
        # Anything asked for while the seat lasts gets the same answer at once.
        outcomes.append(await agent.wait_ticks(1))
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert outcomes[0] == "eat berry -> interrupted: conversation conv_1 started"
    assert outcomes[1] == "wait 1 ticks -> interrupted: conversation conv_1 started"


async def test_an_uninvited_conversation_is_traced_as_accepted(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(accepted_observations(6, seated_until=4))
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser()

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()
    agent.trace.close()

    with gzip.open(
        tmp_path / "agent-ada" / "conversations.jsonl.gz", "rt", encoding="utf-8"
    ) as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    start = next(line for line in lines if line["event"] == "conversation_start")
    assert start["via"] == "accepted"


async def test_the_report_of_a_conversation_nobody_asked_for_reaches_the_planner(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(accepted_observations(8, seated_until=4))
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser(note_text="mira wants planks")

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
    await agent.run()
    await asyncio.sleep(0)

    notes = agent.drain_notes()
    assert any("CONVERSATION REPORT: conv_1" in note for note in notes)
    assert any("mira wants planks" in note for note in notes)
    # The same note is waiting for the next turn prompt.
    assert agent.drain_notes(for_prompt=True) == notes


async def test_a_queued_stint_waits_for_the_conversation_and_then_runs(
    tmp_path: Path,
) -> None:
    jev = FakeJevClient(default_action="wait")
    world = FakeWorldClient(accepted_observations(10, seated_until=4))
    agent = build_agent(world, jev, tmp_path)
    agent.converser = FakeConverser()
    reports: list[object] = []
    seated = asyncio.Event()

    async def plan() -> None:
        # Queued while the seat is held, so it can only run afterwards.
        await seated.wait()
        reports.append(
            await agent.run_stint(
                Brief(instruction="Hold", success_condition="never", max_ticks=3)
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    original = agent._handle_tick

    async def spy(observation: pb.Observation) -> None:
        await original(observation)
        if agent.mode == MODE_CONVERSATION:
            seated.set()

    agent._handle_tick = spy  # type: ignore[method-assign]
    await agent.run()

    assert len(reports) == 1
    assert reports[0].end_reason == "ticks_exhausted"  # type: ignore[attr-defined]
    # Jev was asked on exactly the three stint ticks: none while seated.
    assert len(jev.calls) == 3
