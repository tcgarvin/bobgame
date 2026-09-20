"""The tick loop: one intent a tick, the handshakes, and the traces."""

from __future__ import annotations

import asyncio
import time
import gzip
import json
from pathlib import Path
import pytest
from agents import world_pb2 as pb
from agents.jev_agent.agent import MODE_PLANNING, MODE_REFLEX, MODE_STINT, JevAgent
from agents.jev_agent.client import IntentResult
from agents.jev_agent.stint import Brief
from helpers import FakeJevClient, acted_event, make_entity, make_observation

from conftest import (
    FakeJournalWriter,
    FakeWorldClient,
    GUARD_REFLEX,
    build_agent,
    idle_planner,
    journal_agent,
    observations,
    sleeping_observations,
    wolf_observations,
)


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
            (
                await agent.direct_action(
                    pb.Intent(extract=pb.ExtractIntent(object_id="tree_1")),
                    "extract tree_1",
                )
            ).text()
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


async def test_a_trigger_while_a_rewrite_is_in_flight_is_skipped(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    writer = FakeJournalWriter()
    writer.gate.clear()
    world = FakeWorldClient(sleeping_observations([2, 5], 7))
    agent = journal_agent(world, fake_jev, tmp_path, writer)
    await idle_planner(agent)

    await agent.run()
    assert len(writer.calls) == 1, "the second sleep found one already running"
    writer.gate.set()
    await asyncio.gather(*list(agent._background))


async def test_no_deadline_means_no_wait(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    """Without a world deadline the loop chooses instantly, as it always did."""
    agent = build_agent(FakeWorldClient([]), fake_jev, tmp_path)
    agent.mode = "planning"
    assert agent._submit_window_ms() == 0.0
    await asyncio.wait_for(agent._await_planner_work(), timeout=0.5)
    agent.trace.close()


async def test_the_loop_waits_out_the_window_for_a_planner_action(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    agent = build_agent(FakeWorldClient([]), fake_jev, tmp_path)
    agent.mode = "planning"
    agent._deadline_ms = time.time() * 1000.0 + 1_000.0

    from agents.jev_agent.agent import _DirectRequest

    async def queue_soon() -> None:
        await asyncio.sleep(0.05)
        await agent._direct_requests.put(
            _DirectRequest(
                intent=pb.Intent(wait=pb.WaitIntent()),
                description="wait",
                future=asyncio.get_running_loop().create_future(),
            )
        )

    queued = asyncio.create_task(queue_soon())
    await asyncio.wait_for(agent._await_planner_work(), timeout=2.0)
    await queued
    assert not agent._direct_requests.empty(), "the loop waited and found the action"
    agent.trace.close()


async def test_the_wait_ends_when_the_window_closes(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    agent = build_agent(FakeWorldClient([]), fake_jev, tmp_path)
    agent.mode = "planning"
    agent._deadline_ms = time.time() * 1000.0 + 250.0
    await asyncio.wait_for(agent._await_planner_work(), timeout=2.0)
    assert agent._direct_requests.empty()
    agent.trace.close()
