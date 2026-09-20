"""Shared doubles and builders for the agent tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Mapping, Sequence
from agents import world_pb2 as pb
from agents.jev_agent.agent import JevAgent
from agents.jev_agent.reflex import ReflexBrief
from helpers import (
    FakeJevClient,
    FakeJournalWriter,
    FakeWorldClient,
    build_agent,
    make_entity,
    make_observation,
)

__all__ = ["FakeJournalWriter", "FakeWorldClient", "build_agent"]


def observations(count: int, **kwargs: object) -> list[pb.Observation]:
    """`count` plain observations for `ada`, standing still."""
    return [
        make_observation(tick, make_entity("ada", (10, 10)), **kwargs)  # type: ignore[arg-type]
        for tick in range(1, count + 1)
    ]


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


def journal_agent(
    world: FakeWorldClient,
    jev: FakeJevClient,
    tmp_path: Path,
    writer: FakeJournalWriter,
) -> JevAgent:
    """An agent whose journal is written by `writer` instead of a model."""
    return JevAgent(
        world,  # type: ignore[arg-type]
        jev,
        "ada",
        log_root=tmp_path,
        planner_model="test",
        journal_writer=writer,
    )


async def idle_planner(agent: JevAgent) -> None:
    """Replace the planner loop with one that never takes a turn."""

    async def idle() -> None:
        await asyncio.sleep(3600)

    agent.planner.run = idle  # type: ignore[method-assign]
