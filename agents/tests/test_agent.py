"""The tick loop: one intent per tick, and the planner handshake."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import AsyncIterator, Sequence

import pytest

from agents import world_pb2 as pb
from agents.jev_agent.agent import MODE_PLANNING, MODE_STINT, JevAgent
from agents.jev_agent.client import IntentResult
from agents.jev_agent.stint import Brief

from helpers import FakeJevClient, acted_event, make_entity, make_observation


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
