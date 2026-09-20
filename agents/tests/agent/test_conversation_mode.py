"""Seats taken with and without asking, and what they interrupt."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from agents import world_pb2 as pb
from agents.jev_agent.agent import MODE_CONVERSATION
from agents.jev_agent.stint import Brief
from helpers import (
    FakeConverser,
    FakeJevClient,
    acted_event,
    converse_object,
    make_entity,
    make_observation,
)

from conftest import FakeWorldClient, build_agent, wolf_observations


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
            events_by_tick={
                2: [acted_event("ada", "converse", True, "hailed conv_1 mira")]
            },
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


def hailed_observations(count: int, *, seated_until: int) -> list[pb.Observation]:
    """mira hails ada into conv_1 at tick 2, which closes after `seated_until`."""
    seated = converse_object("conv_1", (11, 10), ["mira", "ada"], speaker="mira")
    return [
        make_observation(
            tick,
            make_entity("ada", (10, 10)),
            objects=[seated] if tick <= seated_until else [],
            events=(
                [acted_event("ada", "converse", True, "hailed conv_1 mira")]
                if tick == 2
                else []
            ),
        )
        for tick in range(1, count + 1)
    ]


async def test_a_conversation_that_starts_by_itself_interrupts_a_direct_action(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(hailed_observations(8, seated_until=4))
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser()
    outcomes: list[str] = []

    async def plan() -> None:
        outcomes.append(
            (
                await agent.direct_action(
                    pb.Intent(eat=pb.EatIntent(item_type="berry", amount=1)),
                    "eat berry",
                )
            ).text()
        )
        # Anything asked for while the seat lasts gets the same answer at once.
        outcomes.append(await agent.wait_ticks(1))
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert outcomes[0] == "eat berry -> interrupted: conversation conv_1 started"
    assert outcomes[1] == "wait 1 ticks -> interrupted: conversation conv_1 started"


async def test_an_unasked_for_conversation_is_traced_as_hailed(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(hailed_observations(6, seated_until=4))
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
    assert start["via"] == "hailed"


async def test_the_report_of_a_conversation_nobody_asked_for_reaches_the_planner(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(hailed_observations(8, seated_until=4))
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
    world = FakeWorldClient(hailed_observations(10, seated_until=4))
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


def hailed_observations(count: int, *, seated_until: int) -> list[pb.Observation]:
    """ivo hails ada at tick 2; the conversation closes after `seated_until`."""
    seated = converse_object("conv_1", (11, 10), ["ivo", "ada"], speaker="ada")
    return [
        make_observation(
            tick,
            make_entity("ada", (10, 10)),
            objects=[seated] if tick <= seated_until else [],
            events=(
                [acted_event("ada", "converse", True, "hailed conv_1 ivo")]
                if tick == 2
                else []
            ),
        )
        for tick in range(1, count + 1)
    ]


async def test_being_hailed_interrupts_a_direct_action(tmp_path: Path) -> None:
    world = FakeWorldClient(hailed_observations(8, seated_until=4))
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser()
    outcomes: list[str] = []

    async def plan() -> None:
        outcomes.append(
            (
                await agent.direct_action(
                    pb.Intent(eat=pb.EatIntent(item_type="berry", amount=1)),
                    "eat berry",
                )
            ).text()
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert outcomes[0] == "eat berry -> interrupted: conversation conv_1 started"


async def test_a_hailed_seat_is_traced_as_hailed(tmp_path: Path) -> None:
    world = FakeWorldClient(hailed_observations(6, seated_until=4))
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
    assert start["via"] == "hailed"


async def test_being_hailed_during_a_stint_ends_it_with_the_report(
    tmp_path: Path,
) -> None:
    world = FakeWorldClient(hailed_observations(10, seated_until=4))
    agent = build_agent(world, FakeJevClient(default_action="wait"), tmp_path)
    agent.converser = FakeConverser(note_text="ivo wants rope")
    reports: list[object] = []

    async def plan() -> None:
        reports.append(
            await agent.run_stint(
                Brief(instruction="Hold", success_condition="never", max_ticks=8)
            )
        )
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert len(reports) == 1
    assert reports[0].end_reason == "joined_conversation"  # type: ignore[attr-defined]
    text = reports[0].to_text()  # type: ignore[attr-defined]
    assert "CONVERSATION REPORT: conv_1" in text
    assert "ivo wants rope" in text
