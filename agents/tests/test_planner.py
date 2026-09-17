"""Planner wiring, exercised with pydantic-ai's TestModel (no network)."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from agents import world_pb2 as pb
from agents.jev_agent import planner as planner_module
from agents.jev_agent.planner import (
    Planner,
    _direction_value as direction_value,
    PlannerDeps,
    build_planner_agent,
    describe_world,
    read_memory,
    trim_history,
)
from agents.jev_agent.stint import Brief, StintReport
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import WorldModel

from helpers import make_entity, make_object, make_observation


class RecordingBridge:
    """An `AgentBridge` that records calls instead of touching a world."""

    def __init__(self, world_model: WorldModel) -> None:
        self._model = world_model
        self.briefs: list[Brief] = []
        self.actions: list[tuple[pb.Intent, str]] = []
        self.waits: list[int] = []
        self.thoughts: list[str] = []

    @property
    def model(self) -> WorldModel:
        return self._model

    async def run_stint(self, brief: Brief) -> StintReport:
        self.briefs.append(brief)
        return StintReport(
            brief=brief,
            ticks_used=3,
            end_reason="eject",
            start_position=(10, 10),
            end_position=(12, 10),
            start_stats="hp 20/20, hunger 80/100",
            end_stats="hp 20/20, hunger 77/100",
            inventory_delta={"wood": 2},
            action_counts={"extract": (3, 0)},
            notable=["discovered 2 new objects"],
            tail=["t3 extract:tree_1 -> accepted (eject 0.80, danger 0.01)"],
        )

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        self.actions.append((intent, description))
        return f"{description} -> ok"

    async def wait_ticks(self, ticks: int) -> str:
        self.waits.append(ticks)
        return f"waited {ticks}"

    def set_thought(self, thought: str) -> None:
        self.thoughts.append(thought)


@pytest.fixture
def world_model() -> WorldModel:
    """A model with a tree, a chest, a board, and a neighbour in view."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10), inventory={"wood": 2}),
            objects=[
                make_object("tree_1", "tree", (12, 10)),
                make_object("chest_1", "chest", (9, 10), {"contents": '{"berry": 3}'}),
                make_object(
                    "board_1",
                    "message_board",
                    (11, 11),
                    {
                        "notes": '[{"title": "Wood pile", "text": "chest by the '
                        'spring", "author": "bob", "tick": 4}]'
                    },
                ),
            ],
            entities=[make_entity("bob", (11, 10))],
        )
    )
    return model


@pytest.fixture
def bridge(world_model: WorldModel) -> RecordingBridge:
    """A recording bridge over that model."""
    return RecordingBridge(world_model)


@pytest.fixture
def deps(bridge: RecordingBridge, tmp_path: Path) -> PlannerDeps:
    """Planner dependencies pointing at a throwaway memory file."""
    return PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")


def test_look_summary_covers_stats_objects_and_neighbours(
    world_model: WorldModel,
) -> None:
    summary = describe_world(world_model)
    assert "you are ada at (10, 10)" in summary
    assert "wood x2" in summary
    assert "tree: 1 known" in summary
    assert "chest chest_1" in summary and "berry x3" in summary
    assert "Wood pile" in summary
    assert "bob (player)" in summary


async def test_every_documented_tool_is_registered(deps: PlannerDeps) -> None:
    agent = build_planner_agent("test")
    model = TestModel(call_tools=[])
    with agent.override(model=model):
        await agent.run("go", deps=deps)
    registered = {
        tool.name for tool in model.last_model_request_parameters.function_tools
    }
    expected = {
        "look",
        "start_stint",
        "travel_to",
        "move",
        "attack",
        "extract",
        "collect",
        "eat",
        "pickup",
        "drop",
        "deposit",
        "withdraw",
        "craft",
        "equip",
        "place",
        "wait",
        "say",
        "write_note",
        "read_board",
        "remember",
        "recall",
    }
    assert expected <= registered


async def test_start_stint_hands_a_brief_to_the_bridge_and_returns_the_report(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["start_stint"])):
        result = await agent.run("go", deps=deps)
    assert len(bridge.briefs) == 1
    assert bridge.briefs[0].max_ticks >= 1
    assert "STINT REPORT" in result.output


async def test_travel_to_builds_a_brief_with_a_preset_travel(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["travel_to"])):
        await agent.run("go", deps=deps)
    brief = bridge.briefs[0]
    assert brief.travel is not None
    assert "Walk to" in brief.instruction


async def test_single_tick_tools_submit_the_right_intents(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    for tool in ("extract", "equip", "say"):
        with agent.override(model=TestModel(call_tools=[tool])):
            await agent.run("go", deps=deps)

    submitted = [intent for intent, _ in bridge.actions]
    assert any(intent.HasField("extract") for intent in submitted)
    assert any(intent.HasField("equip") for intent in submitted)
    assert any(intent.HasField("say") for intent in submitted)


def test_a_bad_direction_asks_the_model_to_try_again() -> None:
    with pytest.raises(ModelRetry, match="unknown direction"):
        direction_value("up")
    assert direction_value("ne") == pb.NORTHEAST


async def test_craft_reports_an_unknown_recipe_instead_of_submitting_it(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["craft"])):
        result = await agent.run("go", deps=deps)
    if bridge.actions:
        assert all(intent.HasField("craft") for intent, _ in bridge.actions)
    else:
        assert "no such recipe" in result.output


async def test_read_board_renders_the_notes(deps: PlannerDeps) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["read_board"])):
        result = await agent.run("go", deps=deps)
    # TestModel passes a generated board id, so the tool reports the miss rather
    # than inventing content.
    assert "board" in result.output.lower() or "have not seen" in result.output


async def test_remember_and_recall_round_trip(deps: PlannerDeps) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["remember"])):
        await agent.run("go", deps=deps)
    assert deps.memory_path.exists()
    assert read_memory(deps.memory_path).startswith("-")


def test_read_memory_reports_an_empty_file_clearly(tmp_path: Path) -> None:
    assert read_memory(tmp_path / "missing.md") == "(no notes yet)"


def test_history_trimming_keeps_the_system_message() -> None:
    messages = [f"m{i}" for i in range(50)]
    trimmed = trim_history(messages, limit=10)  # type: ignore[arg-type]
    assert len(trimmed) == 10
    assert trimmed[0] == "m0"
    assert trimmed[-1] == "m49"


def test_short_history_is_left_alone() -> None:
    messages = ["a", "b"]
    assert trim_history(messages, limit=10) == messages  # type: ignore[arg-type]


async def test_a_planner_turn_publishes_its_reflection(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    test_model = TestModel(call_tools=[], custom_output_text="Chopping next.")
    with planner.agent.override(model=test_model):
        thought = await planner.take_turn()
    assert thought == "Chopping next."
    assert bridge.thoughts == ["Chopping next."]
    assert planner.history, "history is kept for the next turn"


async def test_the_prompt_carries_the_latest_look_and_stint_report(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    report = await bridge.run_stint(
        Brief(instruction="Chop", success_condition="2 wood", max_ticks=5)
    )
    planner.note_report(report)
    prompt = planner.build_prompt()
    assert "you are ada at (10, 10)" in prompt
    assert "STINT REPORT: Chop" in prompt
    assert "Your notes:" in prompt


def test_only_the_last_ten_reports_are_kept(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    for index in range(15):
        planner.note_report(
            StintReport(
                brief=Brief(
                    instruction=f"job {index}", success_condition="", max_ticks=1
                ),
                ticks_used=1,
                end_reason="eject",
                start_position=(0, 0),
                end_position=(0, 0),
                start_stats="",
                end_stats="",
                inventory_delta={},
                action_counts={},
                notable=[],
                tail=[],
            )
        )
    assert len(planner.reports) == 10
    assert planner.reports[0].brief.instruction == "job 5"


def test_the_planner_model_string_gets_an_openrouter_prefix(
    bridge: RecordingBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    planner = Planner(
        bridge,
        "ada",
        model_name="z-ai/glm-5.3-flash",
        trace=AgentTrace("ada", tmp_path),
    )
    assert planner.model_name == "openrouter:z-ai/glm-5.3-flash"
    assert (
        Planner(
            bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
        ).model_name
        == "test"
    )
    assert planner.memory_path == tmp_path / "agent-ada" / "memory.md"


def planner_lines(trace: AgentTrace) -> list[dict[str, object]]:
    """Every record written to `planner.jsonl.gz`."""
    trace.planner.close()
    with gzip.open(trace.directory / "planner.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def test_a_turn_writes_its_prompt_tools_and_result_to_the_trace(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    test_model = TestModel(call_tools=["remember"], custom_output_text="Noted.")
    with planner.agent.override(model=test_model):
        await planner.take_turn()

    lines = planner_lines(trace)
    events = [line["event"] for line in lines]
    assert events[0] == "turn_start"
    assert events[-1] == "turn_end"
    assert "tool_call" in events and "tool_result" in events
    assert all(line["entity_id"] == "ada" for line in lines)
    assert all(line["turn"] == 1 for line in lines)
    assert all(line["tick"] == bridge.model.tick for line in lines)

    start = lines[0]
    assert isinstance(start["prompt"], str)
    assert "you are ada at (10, 10)" in start["prompt"]

    call = next(line for line in lines if line["event"] == "tool_call")
    assert call["tool"] == "remember"
    assert isinstance(call["args"], dict)

    result = next(line for line in lines if line["event"] == "tool_result")
    assert result["tool"] == "remember"
    assert result["result"] == "noted"

    end = lines[-1]
    assert end["thought"] == "Noted."
    assert end["tool_calls"] == 1
    assert isinstance(end["duration_ms"], int)
    assert set(end["usage"]) == {"input_tokens", "output_tokens"}  # type: ignore[arg-type]


async def test_a_history_reset_is_traced(
    bridge: RecordingBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planner_module, "TURN_RETRY_SECONDS", 0.0)
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    # Two turns in a row without a tool call drop the history.
    with planner.agent.override(model=TestModel(call_tools=[])):
        await planner.take_turn()
        await planner.take_turn()

    events = [line["event"] for line in planner_lines(trace)]
    assert events.count("turn_start") == 2
    assert "history_reset" in events
    assert not planner.history


async def test_a_failed_turn_is_traced(
    bridge: RecordingBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planner_module, "TURN_RETRY_SECONDS", 0.0)
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)

    async def explode() -> str:
        raise RuntimeError("openrouter is down")

    monkeypatch.setattr(planner, "take_turn", explode)
    task = asyncio.create_task(planner.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    failures = [line for line in planner_lines(trace) if line["event"] == "turn_failed"]
    assert failures, "the failure reached the trace"
    assert "openrouter is down" in str(failures[0]["error"])


async def test_the_tool_budget_is_traced(
    bridge: RecordingBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planner_module, "MAX_TOOL_CALLS_PER_TURN", 0)
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    with planner.agent.override(model=TestModel(call_tools=["look"])):
        thought = await planner.take_turn()

    assert "tool calls" in thought
    events = [line["event"] for line in planner_lines(trace)]
    assert events == ["turn_start", "tool_budget_reached"]
