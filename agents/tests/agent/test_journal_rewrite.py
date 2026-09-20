"""The sleep-time journal rewrite, and the lives that reset a turn."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from agents.jev_agent.journal import (
    SECTION_SCRATCH,
    SECTION_STORY,
    SECTION_TOMORROW,
    Journal,
)
from helpers import (
    FakeJevClient,
    died_event,
    make_entity,
    make_observation,
    respawned_event,
)

from conftest import (
    FakeJournalWriter,
    FakeWorldClient,
    build_agent,
    idle_planner,
    journal_agent,
    sleeping_observations,
)


def planner_trace_lines(directory: Path) -> list[dict]:
    """Every record in the agent's planner trace."""
    with gzip.open(directory / "planner.jsonl.gz", "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def test_falling_asleep_rewrites_the_journal_exactly_once(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    writer = FakeJournalWriter()
    world = FakeWorldClient(sleeping_observations([2, 3, 4], 6))
    agent = journal_agent(world, fake_jev, tmp_path, writer)
    await idle_planner(agent)
    agent.planner.day_log.add(1, "call", "look({})")

    await agent.run()
    await asyncio.gather(*list(agent._background))
    agent.trace.close()

    assert len(writer.calls) == 1, "one sleep, one rewrite"
    day_log, entity_id = writer.calls[0]
    assert entity_id == "ada"
    assert "look({})" in day_log
    remaining = [e.text for e in agent.planner.day_log.entries]
    assert all(
        "slept on the ground" in text for text in remaining
    ), "the snapshot was taken and cleared; only the wake came after it"

    journal = Journal.load(agent.trace.memory_path)
    assert journal.story_so_far == "I slept."
    assert journal.tomorrow == "Chop six wood."

    rewrites = [
        line
        for line in planner_trace_lines(tmp_path / "agent-ada")
        if line["event"] == "journal_rewrite"
    ]
    assert len(rewrites) == 1
    assert rewrites[0]["trigger"] == "sleep"
    assert rewrites[0]["model"] == "fake-journal-model"
    assert rewrites[0]["truncated"] == []
    assert rewrites[0]["usage"] == {"cost_usd": 0.002}
    traced = rewrites[0]["journal"]
    assert traced[SECTION_STORY] == "I slept."
    assert traced[SECTION_TOMORROW] == "Chop six wood."
    assert traced[SECTION_SCRATCH] == "", "a rewrite folds the scratch in and clears it"
    assert agent.ledger.journal_rewrites == 1


async def test_dying_rewrites_the_journal_and_ends_the_planner_turn(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    writer = FakeJournalWriter()
    script = [
        make_observation(
            tick,
            make_entity("ada", (10, 10), alive=tick != 2),
            events=[died_event("ada", "wolf_1")] if tick == 2 else [],
        )
        for tick in range(1, 4)
    ]
    world = FakeWorldClient(script)
    agent = journal_agent(world, fake_jev, tmp_path, writer)
    await idle_planner(agent)
    agent.planner.deps.budget.reset(20, tick=1)

    await agent.run()
    await asyncio.gather(*list(agent._background))
    agent.trace.close()

    assert len(writer.calls) == 1
    rewrites = [
        line
        for line in planner_trace_lines(tmp_path / "agent-ada")
        if line["event"] == "journal_rewrite"
    ]
    assert rewrites[0]["trigger"] == "death"
    assert agent.planner.deps.budget.left == 0, "the turn ends where the body died"
    notes = agent.drain_notes()
    assert any("you died at tick 2" in note for note in notes)


async def test_await_active_returns_on_the_tick_the_body_wakes(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    world = FakeWorldClient(sleeping_observations([2, 3], 5))
    agent = build_agent(world, fake_jev, tmp_path)
    woke_at: list[int] = []

    async def plan() -> None:
        while agent.model.tick < 1:
            await asyncio.sleep(0)
        await agent.await_active()  # awake at tick 1: returns at once
        woke_at.append(agent.model.tick)
        while agent.model.tick < 2:
            await asyncio.sleep(0)
        await agent.await_active()
        woke_at.append(agent.model.tick)
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert woke_at == [1, 4], "the parked turn resumes on the first awake tick"


async def test_await_active_returns_on_the_tick_the_body_respawns(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    script = [
        make_observation(
            tick,
            make_entity("ada", (10, 10), alive=tick not in (2, 3)),
            events=(
                [died_event("ada")]
                if tick == 2
                else [respawned_event("ada")] if tick == 4 else []
            ),
        )
        for tick in range(1, 6)
    ]
    agent = build_agent(FakeWorldClient(script), fake_jev, tmp_path)
    resumed: list[int] = []

    async def plan() -> None:
        while agent.model.tick < 2:
            await asyncio.sleep(0)
        await agent.await_active()
        resumed.append(agent.model.tick)
        await asyncio.sleep(3600)

    agent.planner.run = plan  # type: ignore[method-assign]
    await agent.run()

    assert resumed == [4]


async def test_a_failed_rewrite_is_traced_and_the_day_log_comes_back(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    writer = FakeJournalWriter(error=RuntimeError("no route to host"))
    world = FakeWorldClient(sleeping_observations([2, 3], 4))
    agent = journal_agent(world, fake_jev, tmp_path, writer)
    await idle_planner(agent)
    agent.planner.day_log.add(1, "call", "look({})")
    Journal(story_so_far="Yesterday.").save(agent.trace.memory_path)
    before = agent.trace.memory_path.read_text(encoding="utf-8")

    await agent.run()
    await asyncio.gather(*list(agent._background))
    agent.trace.close()

    failures = [
        line
        for line in planner_trace_lines(tmp_path / "agent-ada")
        if line["event"] == "journal_rewrite_failed"
    ]
    assert len(failures) == 1
    assert "no route to host" in failures[0]["error"]
    assert agent.trace.memory_path.read_text(encoding="utf-8") == before
    assert [e.text for e in agent.planner.day_log.entries][0] == "look({})"
    assert agent.ledger.journal_rewrites == 0


async def test_a_waking_or_respawning_body_tells_the_planner_to_start_over(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    writer = FakeJournalWriter()
    script = [
        make_observation(
            tick,
            make_entity("ada", (10, 10), alive=tick != 2),
            events=(
                [died_event("ada")]
                if tick == 2
                else [respawned_event("ada")] if tick == 3 else []
            ),
        )
        for tick in range(1, 4)
    ]
    agent = journal_agent(FakeWorldClient(script), fake_jev, tmp_path, writer)
    await idle_planner(agent)

    await agent.run()
    await asyncio.gather(*list(agent._background))

    assert agent.planner._history_reset_reason == "respawned"


async def test_a_wolf_death_you_witness_goes_into_the_day_log(
    fake_jev: FakeJevClient, tmp_path: Path
) -> None:
    """The journal writer needs to know the wolf it hunted is gone (round 3)."""
    script = [
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            entities=[make_entity("wolf_6", (12, 10), entity_type="wolf")],
        ),
        make_observation(
            2, make_entity("ada", (10, 10)), events=[died_event("wolf_6", "esme")]
        ),
    ]
    world = FakeWorldClient(script)
    agent = journal_agent(world, fake_jev, tmp_path, FakeJournalWriter())
    await idle_planner(agent)
    await agent.run()
    agent.trace.close()

    texts = [entry.text for entry in agent.planner.day_log.entries]
    assert "wolf_6 died at tick 2 (killed by esme)" in texts
