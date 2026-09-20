"""Piles, chests, boards, signs and the journal tools."""

from __future__ import annotations

from pathlib import Path
from pydantic_ai.models.test import TestModel
from agents.jev_agent.planner import (
    PlannerDeps,
    build_planner_agent,
    describe_world,
    read_memory,
)
from agents.jev_agent.journal import SECTION_SCRATCH, Journal
from agents.jev_agent.worldmodel import WorldModel

from conftest import _call_tool_once, deps, world_model


async def test_read_board_renders_the_notes(deps: PlannerDeps) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["read_board"])):
        result = await agent.run("go", deps=deps)
    # TestModel passes a generated board id, so the tool reports the miss rather
    # than inventing content.
    assert "board" in result.output.lower() or "have not seen" in result.output


async def test_read_board_on_an_unknown_id_lists_the_boards_known(
    deps: PlannerDeps,
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool_once("read_board", '{"board_id": "board_9"}')):
        result = await agent.run("go", deps=deps)
    assert "you have not seen a board called 'board_9'" in result.output
    assert "board_1 at (11, 11)" in result.output


async def test_read_board_marks_notes_read_and_look_stops_marking_them_new(
    deps: PlannerDeps, world_model: WorldModel
) -> None:
    assert "(new)" in describe_world(world_model)
    assert "1 new since you last read" in describe_world(world_model)

    agent = build_planner_agent("test")
    with agent.override(model=_call_tool_once("read_board", '{"board_id": "board_1"}')):
        result = await agent.run("go", deps=deps)
    assert "Wood pile" in result.output

    text = describe_world(world_model)
    assert "(new)" not in text
    assert "new since you last read" not in text


async def test_remember_appends_under_todays_notes(deps: PlannerDeps) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["remember"])):
        await agent.run("go", deps=deps)
    assert deps.memory_path.exists()
    journal = Journal.parse(deps.memory_path.read_text(encoding="utf-8"))
    assert journal.scratch and journal.scratch[0].startswith("- ")
    assert SECTION_SCRATCH in read_memory(deps.memory_path)


def test_read_memory_seeds_a_missing_journal(tmp_path: Path) -> None:
    path = tmp_path / "missing.md"
    text = read_memory(path, "ada")
    assert "## Story so far" in text
    assert "ada woke up on a large, wild island" in text
    assert path.exists(), "the seed is written out on first read"
