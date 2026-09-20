"""The reflex brief: what it pre-empts, and what it hands back."""

from __future__ import annotations

import asyncio
from pathlib import Path
from agents import world_pb2 as pb
from agents.jev_agent.agent import MODE_CONVERSATION, MODE_REFLEX
from agents.jev_agent.options import Option
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

from conftest import FakeWorldClient, GUARD_REFLEX, build_agent, wolf_observations


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
            (
                await agent.direct_action(
                    pb.Intent(extract=pb.ExtractIntent(object_id="tree_1")),
                    "extract tree_1",
                )
            ).text()
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


async def test_a_reflex_pauses_a_conversation_and_hands_it_back(
    tmp_path: Path,
) -> None:
    seated = converse_object("conv_1", (11, 10), ["mira", "ada"], speaker="mira")
    world = FakeWorldClient(
        wolf_observations(
            [4],
            9,
            objects_by_tick={tick: [seated] for tick in range(1, 10)},
            events_by_tick={
                2: [acted_event("ada", "converse", True, "hailed conv_1 mira")]
            },
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
