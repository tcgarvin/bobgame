"""Planner wiring, exercised with pydantic-ai's TestModel (no network)."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import AsyncIterator, Mapping

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)
from pydantic_ai.models.test import TestModel

from agents import world_pb2 as pb
from agents.jev_agent import planner as planner_module
from agents.jev_agent.build import BuildExecutor
from agents.jev_agent.items import RECIPES
from agents.jev_agent.planner import (
    SETTLEMENT_NARRATIVE,
    Planner,
    _direction_value as direction_value,
    PlannerDeps,
    build_planner_agent,
    describe_world,
    parse_tile_list,
    read_memory,
    threat_alert,
    trim_history,
)
from agents.jev_agent.conversation import ConversationReport
from agents.jev_agent.reflex import EMPTY_REFLEX, NO_REFLEX_LINE, ReflexBrief
from agents.jev_agent.stint import Brief, StintReport
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import TranscriptLine, WorldModel

from helpers import (
    converse_object,
    damaged_event,
    make_entity,
    make_object,
    make_observation,
    utterance_event,
)


class RecordingBridge:
    """An `AgentBridge` that records calls instead of touching a world."""

    def __init__(self, world_model: WorldModel) -> None:
        self._model = world_model
        self.briefs: list[Brief] = []
        self.drivers: list[object] = []
        self.actions: list[tuple[pb.Intent, str]] = []
        self.waits: list[int] = []
        self.thoughts: list[str] = []
        self.reflex = EMPTY_REFLEX
        self.reflex_notes: list[str] = []
        self.conversation_reports: list[ConversationReport] = []
        self.direct_result = ""
        # Consumed one per `direct_action` call, ahead of `direct_result`.
        self.direct_results: list[str] = []
        self.wake_calls: list[int] = []
        self.wake_result = ""

    @property
    def model(self) -> WorldModel:
        return self._model

    async def run_stint(
        self, brief: Brief, driver: object | None = None
    ) -> StintReport:
        self.briefs.append(brief)
        self.drivers.append(driver)
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
        if self.direct_results:
            return self.direct_results.pop(0)
        if self.direct_result:
            return self.direct_result
        return f"{description} -> ok"

    async def wait_ticks(self, ticks: int) -> str:
        self.waits.append(ticks)
        return f"waited {ticks}"

    async def await_wake(self, since_tick: int) -> str:
        self.wake_calls.append(since_tick)
        return self.wake_result

    async def await_conversation(self) -> ConversationReport | None:
        if not self.conversation_reports:
            return None
        return self.conversation_reports.pop(0)

    def set_reflex(self, brief: ReflexBrief) -> None:
        self.reflex = brief

    def clear_reflex(self) -> None:
        self.reflex = EMPTY_REFLEX

    def drain_reflex_notes(self, *, for_prompt: bool = False) -> list[str]:
        notes = list(self.reflex_notes)
        self.reflex_notes.clear()
        return notes

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


def test_look_lists_every_settler_met_even_after_they_walk_out_of_view(
    world_model: WorldModel,
) -> None:
    far_away = make_entity("ada", (40, 40), inventory={"wood": 2})
    world_model.update(make_observation(25, far_away))
    summary = describe_world(world_model)
    assert "entities in view: none" in summary
    assert "settlers you have met (last seen):" in summary
    assert "bob at (11, 10) (d30, 20 ticks ago)" in summary


def test_look_shows_where_a_shout_came_from_and_who_is_armed(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bob", (11, 10), wielded="sword")],
            events=[utterance_event("cleo", "Wolf here!", (50, 12), channel="shout")],
        )
    )
    summary = describe_world(world_model)
    assert 'cleo SHOUTED from (50, 12), 0 ticks ago: "Wolf here!"' in summary
    assert "wielding sword" in summary


async def test_shout_uses_the_shout_channel(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["shout"])):
        await agent.run("go", deps=deps)
    ((intent, _description),) = bridge.actions
    assert intent.say.channel == "shout"


def test_the_prompt_gives_the_goal_the_physics_numbers_and_the_budget() -> None:
    assert "build a civilization" in SETTLEMENT_NARRATIVE
    assert "has 16 health" in SETTLEMENT_NARRATIVE
    assert "for 3 every tick" in SETTLEMENT_NARRATIVE
    assert "sword +3" in SETTLEMENT_NARRATIVE
    assert "`shout` reaches 60 tiles" in SETTLEMENT_NARRATIVE
    assert "You get 30 tool calls per turn" in SETTLEMENT_NARRATIVE


def test_the_prompt_states_physics_and_leaves_strategy_to_the_settlers() -> None:
    advice = (
        "side by side",
        "gang up",
        "Alone",
        "wolf-proof",
        "town plan",
        "never wall",
        "build one early",
        "A house is",
    )
    for phrase in advice:
        assert phrase not in SETTLEMENT_NARRATIVE, phrase


def test_the_prompt_shows_whole_briefs_including_shout_phrases() -> None:
    """Two stint briefs and two reflex briefs, each shown in full."""
    for field in ("instruction:", "success_condition:", "max_ticks:"):
        assert SETTLEMENT_NARRATIVE.count(field) == 4, field
    assert SETTLEMENT_NARRATIVE.count("shouts:") == 2
    assert SETTLEMENT_NARRATIVE.count("trigger_distance:") == 2


def test_the_example_shouts_are_not_about_wolves() -> None:
    """The first run copied the example phrase verbatim 144 times."""
    shout_lines = [
        line for line in SETTLEMENT_NARRATIVE.splitlines() if "shouts:" in line
    ]
    assert shout_lines
    for line in shout_lines:
        assert "wolf" not in line.lower(), line


async def test_start_stint_passes_the_shout_phrases_to_the_brief(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    args = json.dumps(
        {
            "instruction": "Chop trees.",
            "success_condition": "you carry 4 wood",
            "max_ticks": 10,
            "shouts": [" Wolf near me! ", ""],
        }
    )

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("start_stint", args)])
        return ModelResponse(parts=[TextPart("Done.")])

    agent = build_planner_agent("test")
    with agent.override(model=FunctionModel(respond)):
        await agent.run("go", deps=deps)
    assert bridge.briefs[0].shouts == ("Wolf near me!",)


def test_too_many_or_too_long_shout_phrases_are_sent_back_to_the_model() -> None:
    with pytest.raises(ModelRetry, match="at most"):
        planner_module._validated_shouts(["a", "b", "c", "d", "e"])
    with pytest.raises(ModelRetry, match="characters"):
        planner_module._validated_shouts(["x" * 121])


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
        "rest",
        "dismantle",
        "build",
        "wait",
        "say",
        "shout",
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


def _turn(prompt: str, tool_rounds: int) -> list[ModelMessage]:
    """One planner turn: the prompt, `tool_rounds` call/return pairs, a reply."""
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(prompt)])]
    for index in range(tool_rounds):
        call_id = f"{prompt}-{index}"
        messages.append(
            ModelResponse(parts=[ToolCallPart("look", {}, tool_call_id=call_id)])
        )
        messages.append(
            ModelRequest(parts=[ToolReturnPart("look", "ok", tool_call_id=call_id)])
        )
    messages.append(ModelResponse(parts=[TextPart(f"done {prompt}")]))
    return messages


def test_history_trimming_cuts_on_a_turn_boundary_and_keeps_the_first_message() -> None:
    messages = _turn("t1", 3) + _turn("t2", 3) + _turn("t3", 3)  # 8 messages each
    trimmed = trim_history(messages, limit=12)
    assert trimmed[0] is messages[0]
    assert trimmed[1:] == messages[16:], "only the newest whole turn fits"


def test_history_trimming_keeps_the_newest_turn_whole_even_when_it_is_too_long() -> (
    None
):
    messages = _turn("t1", 1) + _turn("t2", 20)
    trimmed = trim_history(messages, limit=10)
    assert trimmed[1:] == messages[4:]


def test_history_trimming_never_orphans_a_tool_return() -> None:
    messages = _turn("t1", 5) + _turn("t2", 5) + _turn("t3", 5)
    trimmed = trim_history(messages, limit=20)
    call_ids = {
        part.tool_call_id
        for message in trimmed
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    return_ids = {
        part.tool_call_id
        for message in trimmed
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    assert return_ids <= call_ids


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
    assert prompt.startswith("Your name is ada.")
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
    assert str(result["result"]).startswith(
        "noted\n[tick 5 \u00b7 day 0 5/300 day; this turn has cost 0 ticks"
    )

    end = lines[-1]
    assert end["thought"] == "Noted."
    assert end["tool_calls"] == 1
    assert isinstance(end["duration_ms"], int)
    usage = end["usage"]
    assert isinstance(usage, dict)
    assert set(usage) == {
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "requests",
        "cost_usd",
        # TestModel responses carry no provider cost, so the turn is flagged.
        "cost_missing",
    }
    assert usage["requests"] >= 1
    assert usage["cost_missing"] is True


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


def _call_tool_forever(tool_name: str) -> FunctionModel:
    """A model that never stops calling `tool_name`, whatever it is told."""

    async def respond(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[DeltaToolCalls]:
        yield {0: DeltaToolCall(name=tool_name, json_args='{"text": "hi"}')}

    return FunctionModel(stream_function=respond)


def _call_tool_until_refused(tool_name: str) -> FunctionModel:
    """A model that calls `tool_name` until the budget refusal, then reflects."""

    async def respond(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        refused = any(
            isinstance(part, ToolReturnPart) and "NOT EXECUTED" in str(part.content)
            for part in messages[-1].parts
        )
        if refused:
            yield "Out of budget; stopping."
        else:
            yield {0: DeltaToolCall(name=tool_name, json_args='{"text": "hi"}')}

    return FunctionModel(stream_function=respond)


# --- the real-time clock and the hand-back alert -------------------------------


def _bitten(model: WorldModel, tick: int, health: int) -> None:
    """ada takes a 3-point bite from wolf_1, which stands next to her."""
    model.update(
        make_observation(
            tick,
            make_entity("ada", (10, 10), health=health),
            entities=[
                make_entity("wolf_1", (11, 10), entity_type="wolf", max_health=16)
            ],
            events=[damaged_event("ada", "wolf_1", 3, health)],
        )
    )


def test_no_alert_in_peace(world_model: WorldModel) -> None:
    assert threat_alert(world_model, since_tick=0) == ""


def test_a_bite_during_the_turn_tells_the_planner_to_hand_back(
    world_model: WorldModel,
) -> None:
    _bitten(world_model, tick=8, health=14)
    alert = threat_alert(world_model, since_tick=6)
    assert alert.startswith("!! UNDER ATTACK: wolf_1 took 3 health")
    assert "health 14/20" in alert
    assert "start_stint" in alert


def test_a_wolf_in_view_is_flagged_before_it_bites(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            entities=[make_entity("wolf_1", (15, 10), entity_type="wolf")],
        )
    )
    alert = threat_alert(world_model, since_tick=6)
    assert alert.startswith("!! WOLF NEAR: wolf_1 is 5 tiles away")
    assert "start_stint" in alert


async def test_tool_results_carry_the_clock_and_the_alert(
    deps: PlannerDeps, world_model: WorldModel
) -> None:
    agent = build_planner_agent("test")
    deps.budget.reset(30, tick=5)
    _bitten(world_model, tick=9, health=17)
    with agent.override(model=TestModel(call_tools=["recall"])):
        result = await agent.run("go", deps=deps)
    assert (
        "[tick 9 \u00b7 day 0 9/300 day; this turn has cost 4 ticks so far]"
        in result.output
    )
    assert "!! UNDER ATTACK" in result.output


async def test_an_old_bite_stops_being_reported_later_in_a_long_turn(
    deps: PlannerDeps, world_model: WorldModel
) -> None:
    agent = build_planner_agent("test")
    deps.budget.reset(30, tick=5)
    _bitten(world_model, tick=9, health=17)
    # The wolf is long dead and gone by the time the stint inside the turn ends.
    world_model.update(make_observation(200, make_entity("ada", (10, 10), health=20)))
    with agent.override(model=TestModel(call_tools=["recall"])):
        result = await agent.run("go", deps=deps)
    assert "this turn has cost 195 ticks" in result.output
    assert "!!" not in result.output


def test_the_alert_window_never_reaches_before_the_turn_or_past_a_few_ticks() -> None:
    assert planner_module.alert_window_start(tick=9, turn_start_tick=5) == 5
    recent = planner_module.RECENT_ATTACK_TICKS
    assert (
        planner_module.alert_window_start(tick=200, turn_start_tick=5) == 200 - recent
    )


def test_a_turn_that_starts_under_attack_opens_with_the_alert(
    bridge: RecordingBridge, world_model: WorldModel, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    _bitten(world_model, tick=9, health=17)
    prompt = planner.build_prompt()
    assert prompt.index("!! UNDER ATTACK") < prompt.index("you are ada at")


def test_the_prompt_explains_the_real_time_clock() -> None:
    assert "ticks every two\n  seconds whether or not you have answered" in (
        SETTLEMENT_NARRATIVE
    )
    assert "only happens\n  if Jev is doing it" in SETTLEMENT_NARRATIVE


async def test_every_tool_result_says_how_much_budget_is_left(
    deps: PlannerDeps,
) -> None:
    agent = build_planner_agent("test")
    deps.budget.reset(6, tick=5)
    with agent.override(model=TestModel(call_tools=["recall"])):
        result = await agent.run("go", deps=deps)
    assert "[tool budget: 5 of 6 calls left this turn]" in result.output
    assert "Nearly spent" in result.output


async def test_a_spent_budget_refuses_the_call_but_keeps_the_turn(
    bridge: RecordingBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planner_module, "MAX_TOOL_CALLS_PER_TURN", 2)
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    with planner.agent.override(model=_call_tool_until_refused("say")):
        thought = await planner.take_turn()

    assert thought == "Out of budget; stopping."
    assert len(bridge.actions) == 2, "the third say was refused, not submitted"
    assert planner.history, "the turn is remembered"
    events = [line["event"] for line in planner_lines(trace)]
    assert "tool_budget_spent" in events
    assert events[-1] == "turn_end"


async def test_the_hard_limit_ends_the_turn_and_still_keeps_its_history(
    bridge: RecordingBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planner_module, "MAX_TOOL_CALLS_PER_TURN", 1)
    monkeypatch.setattr(planner_module, "HARD_LIMIT_MARGIN", 2)
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    with planner.agent.override(model=_call_tool_forever("say")):
        thought = await planner.take_turn()

    assert "tool calls" in thought
    assert len(bridge.actions) == 1
    assert planner.history, "what the turn did is not forgotten"
    lines = planner_lines(trace)
    events = [line["event"] for line in lines]
    assert events[-1] == "tool_budget_reached"
    # The turn made requests before the backstop fired, so it records what they
    # cost (docs/11_cost_accounting.md).
    usage = lines[-1]["usage"]
    assert isinstance(usage, dict)
    assert usage["requests"] > 0
    assert usage["cost_missing"] is True

    # The kept history must be usable: the next turn runs without an error.
    with planner.agent.override(model=TestModel(call_tools=[])):
        await planner.take_turn()


# --- building (docs/08_building.md) ----------------------------------------


def test_the_prompt_teaches_every_recipe() -> None:
    for name, recipe in RECIPES.items():
        assert name in SETTLEMENT_NARRATIVE, name
        assert recipe.cost_text() in SETTLEMENT_NARRATIVE, name


def test_the_prompt_marks_each_recipes_station_and_work() -> None:
    for name, recipe in RECIPES.items():
        line = next(
            line
            for line in SETTLEMENT_NARRATIVE.splitlines()
            if line.startswith(f"  {name} =")
        )
        assert f"[{recipe.station or 'hand'}, {recipe.work} action" in line


def test_the_prompt_says_how_placed_objects_behave() -> None:
    for phrase in (
        "Walls block everyone",
        "A door blocks wolves",
        "message board",
        "can be dismantled",
    ):
        assert phrase in SETTLEMENT_NARRATIVE


def test_tile_lists_parse_and_complain_clearly() -> None:
    assert parse_tile_list("12,30; (13,31)") == [(12, 30), (13, 31)]
    assert parse_tile_list("") == []
    with pytest.raises(ModelRetry):
        parse_tile_list("12")
    with pytest.raises(ModelRetry):
        parse_tile_list("x,y")


def one_tool_call(tool_name: str, args: dict[str, object]) -> FunctionModel:
    """A model that makes exactly one tool call and then answers with text."""
    called = {"done": False}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if called["done"]:
            return ModelResponse(parts=[TextPart("built it")])
        called["done"] = True
        return ModelResponse(parts=[ToolCallPart(tool_name, args)])

    return FunctionModel(respond)


async def test_build_hands_the_bridge_a_driver_with_the_planned_tiles(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    model = one_tool_call(
        "build",
        {
            "kind": "wood_wall",
            "shape": "rect",
            "x1": 10,
            "y1": 10,
            "x2": 13,
            "y2": 13,
            "max_ticks": 40,
            "skip": "11,10",
        },
    )
    with agent.override(model=model):
        await agent.run("go", deps=deps)

    driver = bridge.drivers[0]
    assert isinstance(driver, BuildExecutor)
    assert (11, 10) not in driver.plan.tiles
    assert len(driver.plan.tiles) == 11
    assert bridge.briefs[0].max_ticks == 40
    assert "wood_wall" in bridge.briefs[0].instruction


async def test_build_asks_again_when_the_shape_makes_no_sense(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    model = one_tool_call(
        "build",
        {"kind": "berry", "shape": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 0},
    )
    with agent.override(model=model):
        await agent.run("go", deps=deps)
    assert not bridge.drivers


async def test_a_ground_piece_can_be_placed_without_a_direction(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("place", {"kind": "road"})):
        await agent.run("go", deps=deps)
    intent, _ = bridge.actions[-1]
    assert intent.place.kind == "road"
    assert intent.place.direction == pb.DIRECTION_UNSPECIFIED


async def test_a_structure_without_a_direction_is_refused(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("place", {"kind": "wood_wall"})):
        await agent.run("go", deps=deps)
    assert not bridge.actions


async def test_dismantle_and_rest_submit_their_intents(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("rest", {"object_id": "bed_1"})):
        await agent.run("go", deps=deps)
    with agent.override(model=one_tool_call("dismantle", {"object_id": "w_1"})):
        await agent.run("go", deps=deps)
    submitted = [intent for intent, _ in bridge.actions]
    assert any(intent.HasField("rest") for intent in submitted)
    assert any(intent.HasField("extract") for intent in submitted)


# -- reflex and conversation tools (docs/09) ---------------------------------


def _call_tool(tool_name: str, args: Mapping[str, object]) -> FunctionModel:
    """A model that calls `tool_name` once with `args`, then writes a line."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name, json.dumps(args))])
        return ModelResponse(parts=[TextPart(str(messages[-1].parts[0].content))])

    return FunctionModel(respond)


def _conversation_report() -> ConversationReport:
    """A finished conversation, as the tick loop would hand it to a tool."""
    return ConversationReport(
        conversation_id="conv_1",
        start_tick=5,
        end_tick=20,
        participants=("ada", "mira"),
        end_reason="closed",
        transcript=(TranscriptLine(6, "ada", "Who needs planks?"),),
        note="mira needs planks",
    )


async def test_set_reflex_registers_a_brief_and_clear_reflex_removes_it(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["set_reflex"])):
        await agent.run("go", deps=deps)
    assert bridge.reflex.registered
    assert 1 <= bridge.reflex.trigger_distance <= 8

    with agent.override(model=TestModel(call_tools=["clear_reflex"])):
        await agent.run("go", deps=deps)
    assert bridge.reflex == EMPTY_REFLEX


async def test_an_out_of_range_trigger_distance_is_clamped(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool(
            "set_reflex",
            {
                "instruction": "Go to the beds.",
                "success_condition": "you stand by a bed",
                "max_ticks": 10,
                "trigger_distance": 40,
            },
        )
    ):
        await agent.run("go", deps=deps)
    assert bridge.reflex.trigger_distance == 8


async def test_the_prompt_shows_the_reflex_brief_and_any_reflex_report(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(bridge, "ada", model_name="test", trace=AgentTrace.disabled())
    assert NO_REFLEX_LINE in planner.build_prompt()

    bridge.reflex = ReflexBrief(
        instruction="Walk to the beds.",
        success_condition="you are next to a bed",
        max_ticks=10,
        trigger_distance=5,
    )
    bridge.reflex_notes = [
        "[reflex ran ticks 3-9: ended because threat_gone; " "health 20 -> 17]"
    ]
    prompt = planner.build_prompt()
    assert "Walk to the beds." in prompt
    assert "[reflex ran ticks 3-9" in prompt


async def test_a_reflex_report_is_appended_to_the_next_tool_result(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.reflex_notes = [
        "[reflex ran ticks 3-9: ended because death; " "health 6 -> 0]"
    ]
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["recall"])):
        result = await agent.run("go", deps=deps)
    assert "[reflex ran ticks 3-9" in result.output


async def test_look_lists_the_conversations_in_view(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[converse_object("conv_1", (11, 10), ["mira"], utterances=2)],
        )
    )
    summary = describe_world(world_model)
    assert "conversations in view:" in summary
    assert "conv_1 at (11, 10): mira" in summary
    assert "3 free seats" in summary


async def test_open_conversation_submits_the_intent_and_returns_the_report(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "open a conversation to the E -> converse ok: open conv_1"
    bridge.conversation_reports = [_conversation_report()]
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool(
            "open_conversation",
            {"direction": "E", "opening_line": "Who needs planks?"},
        )
    ):
        result = await agent.run("go", deps=deps)

    intent, _ = bridge.actions[-1]
    assert intent.converse.action == "open"
    assert intent.converse.text == "Who needs planks?"
    assert intent.converse.direction == pb.EAST
    assert "CONVERSATION REPORT: conv_1" in result.output


async def test_a_refused_open_does_not_wait_for_a_conversation(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = (
        "open a conversation to the E -> converse failed: anchor is occupied by "
        "an entity"
    )
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool("open_conversation", {"direction": "E", "opening_line": "hi"})
    ):
        result = await agent.run("go", deps=deps)

    assert "anchor is occupied" in result.output
    assert "CONVERSATION REPORT" not in result.output


async def test_join_conversation_refuses_a_conversation_it_cannot_see(
    deps: PlannerDeps,
) -> None:
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool("join_conversation", {"conversation_id": "conv_9"})
    ):
        result = await agent.run("go", deps=deps)
    assert "you have not seen a conversation" in result.output


async def test_join_conversation_walks_then_joins(
    deps: PlannerDeps, bridge: RecordingBridge, world_model: WorldModel
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[converse_object("conv_1", (11, 10), ["mira"])],
        )
    )
    bridge.direct_result = "join conv_1 -> converse ok: join conv_1"
    bridge.conversation_reports = [_conversation_report()]
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool("join_conversation", {"conversation_id": "conv_1"})
    ):
        result = await agent.run("go", deps=deps)

    intent, _ = bridge.actions[-1]
    assert intent.converse.action == "join"
    assert intent.converse.conversation_id == "conv_1"
    assert not bridge.briefs, "already next to the anchor: no walk is needed"
    assert "CONVERSATION REPORT: conv_1" in result.output


async def test_give_submits_a_give_intent(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool("give", {"entity_id": "mira", "kind": "plank", "amount": 3})
    ):
        await agent.run("go", deps=deps)

    intent, description = bridge.actions[-1]
    assert intent.give.target_entity_id == "mira"
    assert intent.give.kind == "plank"
    assert intent.give.amount == 3
    assert description == "give 3 plank to mira"


# --- stations, work and sleep (docs/10_metal_and_sleep.md) ------------------


def test_the_prompt_gives_the_fatigue_the_day_and_the_tier_numbers() -> None:
    for phrase in (
        "Fatigue runs from 0 to 100",
        "every 4 ticks by day",
        "From 60 you are tired",
        "At 100 you collapse",
        "A day is 300 ticks",
        "1 fatigue per tick at\n  night",
        "copper_vein needs a wielded pickaxe",
        "3 work makes one unit",
        "workshop_table, furnace or anvil",
        "iron_sword +5",
    ):
        assert phrase in SETTLEMENT_NARRATIVE, phrase


def test_the_recipe_table_in_the_prompt_comes_from_items() -> None:
    for name, recipe in RECIPES.items():
        line = f"  {name} = {recipe.cost_text()}"
        assert line in SETTLEMENT_NARRATIVE, name
    assert "[furnace, 2 actions]" in SETTLEMENT_NARRATIVE
    assert "[hand, 1 action]" in SETTLEMENT_NARRATIVE


async def test_craft_repeats_the_action_until_the_recipe_completes(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_results = [
        "craft charcoal -> craft ok: charcoal 1/2",
        "craft charcoal -> craft ok: crafted charcoal x2",
    ]
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "charcoal"})):
        result = await agent.run("go", deps=deps)

    assert len(bridge.actions) == 2
    assert all(intent.craft.recipe == "charcoal" for intent, _ in bridge.actions)
    assert "charcoal 1/2" in result.output
    assert "crafted charcoal" in result.output


async def test_craft_stops_at_the_first_failed_action(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_results = [
        "craft iron_ingot -> craft failed: iron_ingot needs a furnace nearby"
    ]
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "iron_ingot"})):
        result = await agent.run("go", deps=deps)

    assert len(bridge.actions) == 1
    assert "needs a furnace nearby" in result.output


async def test_a_hand_recipe_still_takes_exactly_one_craft_action(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "craft plank -> craft ok: crafted plank x2"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "plank"})):
        await agent.run("go", deps=deps)
    assert len(bridge.actions) == 1


async def test_sleep_submits_the_intent_and_returns_the_wake(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    for reason in ("rested", "damaged", "hungry", "bed removed", "asked"):
        bridge.actions.clear()
        bridge.direct_result = "sleep on bed_1 -> sleep ok: asleep on bed_1"
        bridge.wake_result = (
            f"slept on bed_1 from tick 5 to tick 60 (55 ticks); woke because "
            f"{reason}; fatigue 80 -> 25"
        )
        agent = build_planner_agent("test")
        with agent.override(model=_call_tool("sleep", {"bed_object_id": "bed_1"})):
            result = await agent.run("go", deps=deps)

        intent, _ = bridge.actions[-1]
        assert intent.sleep.object_id == "bed_1"
        assert f"woke because {reason}" in result.output
        assert "fatigue 80 -> 25" in result.output


async def test_sleep_on_the_ground_names_no_bed(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "sleep on the ground -> sleep ok: asleep on the ground"
    bridge.wake_result = "slept on the ground from tick 5 to tick 9 (4 ticks)"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("sleep", {"bed_object_id": ""})):
        result = await agent.run("go", deps=deps)

    intent, description = bridge.actions[-1]
    assert intent.sleep.object_id == ""
    assert "the ground" in description
    assert "4 ticks" in result.output


async def test_a_refused_sleep_does_not_wait_for_a_wake(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "sleep on bed_1 -> sleep failed: bed_1 is taken"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("sleep", {"bed_object_id": "bed_1"})):
        result = await agent.run("go", deps=deps)

    assert not bridge.wake_calls
    assert "is taken" in result.output


async def test_wake_says_so_when_you_are_not_asleep(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("wake", {})):
        result = await agent.run("go", deps=deps)
    assert "you are not asleep" in result.output
    assert not bridge.actions


async def test_wake_submits_the_intent_while_asleep(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(9, make_entity("ada", (10, 10), fatigue=50, asleep=True))
    )
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("wake", {})):
        await agent.run("go", deps=deps)
    intent, _ = bridge.actions[-1]
    assert intent.HasField("wake")


def test_look_names_the_veins_and_stations_it_knows(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            7,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("fur_1", "furnace", (11, 10)),
                make_object("anv_1", "anvil", (9, 10)),
                make_object("cop_1", "copper_vein", (12, 12)),
                make_object("iro_1", "iron_vein", (12, 13)),
            ],
        )
    )
    summary = describe_world(world_model)
    for line in (
        "furnace: 1 known",
        "anvil: 1 known",
        "copper_vein: 1",
        "iron_vein: 1",
    ):
        assert line in summary
    assert "day 0 7/300 day" in summary
    assert "fatigue 0/100 (fresh)" in summary
