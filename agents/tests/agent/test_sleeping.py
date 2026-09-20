"""A sleeping body: what it submits, what it refuses, what it traces."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from agents import world_pb2 as pb
from agents.jev_agent.agent import MODE_PLANNING
from agents.jev_agent.stint import Brief
from helpers import FakeJevClient, acted_event

from conftest import (
    FakeJournalWriter,
    FakeWorldClient,
    build_agent,
    idle_planner,
    journal_agent,
    sleeping_observations,
)


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


async def test_falling_asleep_ends_the_stint_instead_of_holding_the_turn(
    tmp_path: Path,
) -> None:
    """A sleeper submits nothing, so the stint (and the tool call) ends there."""
    jev = FakeJevClient(default_action="move_E")
    world = FakeWorldClient(sleeping_observations([2, 3], 5))
    agent = build_agent(world, jev, tmp_path)
    reports: list[str] = []

    async def plan() -> None:
        report = await agent.run_stint(
            Brief(instruction="Walk east", success_condition="never", max_ticks=10)
        )
        reports.append(report.end_reason)
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    # Only tick 1 is Jev's: the stint ends the tick the body falls asleep.
    assert len(jev.calls) == 1
    assert reports == ["asleep"]
    assert agent.mode == MODE_PLANNING


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
        results.append(outcome.text())
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
            (
                await agent.direct_action(pb.Intent(wake=pb.WakeIntent()), "wake up")
            ).text()
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
    assert move.future.result().text() == "move north -> failed: asleep"


async def test_falling_asleep_by_itself_ends_the_planner_turn(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    fell = acted_event("ada", "sleep", True, "the ground")
    world = FakeWorldClient(
        sleeping_observations([2, 3], 3, events_by_tick={2: [fell]})
    )
    agent = journal_agent(world, fake_jev, tmp_path, FakeJournalWriter())
    await idle_planner(agent)
    agent.planner.deps.budget.reset(20, tick=1)

    await agent.run()
    await asyncio.gather(*list(agent._background))

    assert agent.planner.deps.budget.left == 0, "the turn ends where the body slept"
    notes = agent.drain_notes()
    assert any("you fell asleep on the ground at tick 2" in note for note in notes)


async def test_the_sleep_tool_gets_no_extra_note_when_the_body_sleeps(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    world = FakeWorldClient(sleeping_observations([2, 3], 4))
    agent = journal_agent(world, fake_jev, tmp_path, FakeJournalWriter())

    async def plan() -> None:
        await agent.await_wake(since_tick=1)
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()
    await asyncio.gather(*list(agent._background))

    assert not any("you fell asleep" in note for note in agent.drain_notes())
