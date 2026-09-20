"""Planner wiring, exercised with pydantic-ai's TestModel (no network)."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import AsyncIterator, Callable, Mapping

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
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
from agents.jev_agent import items, planner as planner_module
from agents.jev_agent.build import BuildExecutor
from agents.jev_agent.items import RECIPES
from agents.jev_agent.planner import (
    SETTLEMENT_NARRATIVE,
    settlement_narrative,
    Planner,
    _direction_value as direction_value,
    PlannerDeps,
    body_alerts,
    build_planner_agent,
    describe_world,
    parse_tile_list,
    read_memory,
    threat_alert,
    travel_arrival,
    travel_budget,
    trim_history,
    _validated_hails as validated_hails,
)
from agents.jev_agent.conversation import (
    CONVERSER_NARRATIVE,
    converser_narrative,
    ApproachDriver,
    ConversationReport,
)
from agents.jev_agent.journal import (
    ALL_SECTIONS,
    JOURNAL_NARRATIVE,
    journal_narrative,
    SECTION_SCRATCH,
    SECTION_STORY,
    SECTION_TOMORROW,
    Journal,
)
from agents.jev_agent.options import BriefHail
from agents.jev_agent.reflex import EMPTY_REFLEX, NO_REFLEX_LINE, ReflexBrief
from agents.jev_agent.stint import (
    INTERRUPTED_BY_REFLEX,
    Brief,
    StintReport,
    conversation_interruption,
    never_ends,
)
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import TranscriptLine, WorldModel

from helpers import (
    converse_object,
    damaged_event,
    died_event,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
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
        self.conversation_purposes: list[str] = []
        self.direct_result = ""
        # Consumed one per `direct_action` call, ahead of `direct_result`.
        self.direct_results: list[str] = []
        self.wake_calls: list[int] = []
        self.wake_result = ""
        self.journal_waits = 0
        self.active_waits = 0
        # Called while a turn waits for the journal, to stand in for a rewrite
        # finishing between turns.
        self.on_journal_wait: Callable[[], None] = lambda: None
        # The extra end rules `travel_to` hands down, one per stint.
        self.end_checks: list[Callable[[WorldModel], str]] = []
        # The end reason every recorded stint reports back.
        self.stint_end_reason = "eject"

    @property
    def model(self) -> WorldModel:
        return self._model

    async def run_stint(
        self,
        brief: Brief,
        driver: object | None = None,
        end_check: Callable[[WorldModel], str] = never_ends,
    ) -> StintReport:
        self.briefs.append(brief)
        self.drivers.append(driver)
        self.end_checks.append(end_check)
        return StintReport(
            brief=brief,
            ticks_used=3,
            end_reason=self.stint_end_reason,
            start_position=(10, 10),
            end_position=(12, 10),
            start_stats="hp 20/20, food 80/100",
            end_stats="hp 20/20, food 77/100",
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

    async def await_active(self) -> None:
        self.active_waits += 1

    async def await_conversation(self) -> ConversationReport | None:
        if not self.conversation_reports:
            return None
        return self.conversation_reports.pop(0)

    def set_reflex(self, brief: ReflexBrief) -> None:
        self.reflex = brief

    def clear_reflex(self) -> None:
        self.reflex = EMPTY_REFLEX

    def set_conversation_purpose(self, purpose: str) -> None:
        self.conversation_purposes.append(purpose)

    def drain_notes(self, *, for_prompt: bool = False) -> list[str]:
        notes = list(self.reflex_notes)
        self.reflex_notes.clear()
        return notes

    async def await_journal(self) -> None:
        self.journal_waits += 1
        self.on_journal_wait()

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


def test_look_lists_item_piles_with_their_contents(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[
                make_object(
                    "item_pile_3",
                    "item_pile",
                    (14, 10),
                    {"contents": '{"axe": 1, "wood": 5}'},
                ),
                make_object("item_pile_4", "item_pile", (20, 10), {"contents": "{}"}),
            ],
        )
    )
    text = describe_world(world_model)
    assert "item pile item_pile_3 at (14, 10) (d4): axe x1, wood x5" in text
    assert "item_pile_4" not in text.split("item pile item_pile_3")[1]


async def test_a_failed_pickup_names_the_nearest_piles(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[
                make_object(
                    "item_pile_3", "item_pile", (14, 10), {"contents": '{"axe": 1}'}
                )
            ],
        )
    )
    bridge.direct_result = "pickup 1 axe -> pickup failed: no item pile here"
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("pickup", {"kind": "axe"})):
        result = await agent.run("go", deps=deps)
    returned = [
        str(part.content)
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert "no item pile here" in returned[0]
    assert "item_pile_3 at (14, 10) (d4): axe x1" in returned[0]


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


def test_look_marks_an_asleep_settler_in_view_and_in_the_roster(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"wood": 2}),
            entities=[make_entity("bob", (11, 10), asleep=True)],
        )
    )
    summary = describe_world(world_model)
    assert "bob (player) at (11, 10), hp 20/20, asleep" in summary
    assert "bob at (11, 10) (d1, now, asleep as of now)" in summary


def test_look_marks_a_dead_settler_over_asleep(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"wood": 2}),
            entities=[make_entity("bob", (11, 10), asleep=True, alive=False)],
        )
    )
    summary = describe_world(world_model)
    assert "bob (player) at (11, 10), hp 20/20, dead" in summary
    assert "bob at (11, 10) (d1, now, dead)" in summary


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
    assert "You get 20 tool calls per turn" in SETTLEMENT_NARRATIVE
    assert "costs about\n  3 ticks all told" in SETTLEMENT_NARRATIVE


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
    """Three stint briefs and two reflex briefs, each shown in full."""
    for field in ("instruction:", "success_condition:", "max_ticks:"):
        assert SETTLEMENT_NARRATIVE.count(field) == 5, field
    assert SETTLEMENT_NARRATIVE.count("shouts:") == 2
    assert SETTLEMENT_NARRATIVE.count("places:") == 1
    assert SETTLEMENT_NARRATIVE.count("trigger_distance:") == 2


def test_the_prompt_explains_how_jev_sees_the_world() -> None:
    """The planner is told what Jev can and cannot read (docs/05)."""
    assert "Jev does\n  not understand absolute coordinates" in SETTLEMENT_NARRATIVE
    assert 'the stint ends\n  with "lost"' in SETTLEMENT_NARRATIVE
    assert "bushes marked B" in SETTLEMENT_NARRATIVE
    assert "each stage leaves a\n  mark Jev can see" in SETTLEMENT_NARRATIVE


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


def test_a_place_name_must_be_short_lowercase_and_unused(
    world_model: WorldModel,
) -> None:
    assert planner_module._validated_places({"the_lake": [3, 4]}, world_model) == {
        "the_lake": (3, 4)
    }
    with pytest.raises(ModelRetry, match="at most"):
        planner_module._validated_places(
            {f"p{i}": [0, 0] for i in range(7)}, world_model
        )
    with pytest.raises(ModelRetry, match="lowercase"):
        planner_module._validated_places({"The Lake": [3, 4]}, world_model)
    with pytest.raises(ModelRetry, match="already the id"):
        planner_module._validated_places({"tree_1": [3, 4]}, world_model)
    with pytest.raises(ModelRetry, match="already the id"):
        planner_module._validated_places({"bob": [3, 4]}, world_model)
    with pytest.raises(ModelRetry, match=r"\[x, y\] pair"):
        planner_module._validated_places({"the_lake": [3]}, world_model)


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
        "shout",
        "write_note",
        "read_board",
        "remember",
        "recall",
    }
    assert expected <= registered
    # Removed on purpose: walking, fighting, mining and picking berries are
    # Jev's (or `travel_to`'s / `build`'s), because a planner call costs about
    # three ticks where Jev costs one.
    assert registered.isdisjoint({"move", "attack", "extract", "collect"})
    assert {"start_stint", "travel_to", "eat", "give"} <= registered


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
    assert brief.instruction == "Walk to the destination."
    # Jev is given the offset to a named place, never the numbers themselves.
    assert list(brief.places) == ["destination"]


async def test_single_tick_tools_submit_the_right_intents(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    for tool in ("equip", "shout"):
        with agent.override(model=TestModel(call_tools=[tool])):
            await agent.run("go", deps=deps)

    submitted = [intent for intent, _ in bridge.actions]
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


async def test_join_conversation_on_an_unknown_id_lists_conversations_in_view(
    deps: PlannerDeps, bridge: RecordingBridge, world_model: WorldModel
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("conv_1", "conversation", (12, 10), {"speaker": "bob"})
            ],
        )
    )
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool_once("join_conversation", '{"conversation_id": "conv_9"}')
    ):
        result = await agent.run("go", deps=deps)
    assert "you have not seen a conversation called 'conv_9'" in result.output
    assert "conv_1" in result.output
    assert "talk_to" in result.output


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
    assert bridge.active_waits == 1, "a turn waits for an awake, living body first"
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
    prompt = await planner.build_prompt()
    assert prompt.startswith("Your name is ada.")
    assert "you are ada at (10, 10)" in prompt
    assert "STINT REPORT: Chop" in prompt
    assert "Your journal:" in prompt
    assert bridge.journal_waits == 1, "the prompt waits for the journal rewrite"


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
    assert "[tick 9 \u00b7 day 0 9/300 day; this turn has cost 4 ticks so far" in (
        result.output
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


async def test_a_turn_that_starts_under_attack_opens_with_the_alert(
    bridge: RecordingBridge, world_model: WorldModel, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    _bitten(world_model, tick=9, health=17)
    prompt = await planner.build_prompt()
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
    with planner.agent.override(model=_call_tool_until_refused("shout")):
        thought = await planner.take_turn()

    assert thought == "Out of budget; stopping."
    assert len(bridge.actions) == 2, "the third shout was refused, not submitted"
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
    with planner.agent.override(model=_call_tool_forever("shout")):
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


def carrying(model: WorldModel, inventory: dict[str, int]) -> None:
    """Re-observe the model's actor with this pack."""
    model.update(
        make_observation(
            model.tick + 1, make_entity("ada", (10, 10), inventory=inventory)
        )
    )


async def test_build_hands_the_bridge_a_driver_with_the_planned_tiles(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    carrying(bridge.model, {"wood_wall": 3})
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


async def test_build_with_no_pieces_runs_nothing_and_names_the_recipe(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    model = one_tool_call(
        "build",
        {"kind": "wood_wall", "shape": "line", "x1": 10, "y1": 12, "x2": 13, "y2": 12},
    )
    with agent.override(model=model):
        result = await agent.run("go", deps=deps)
    returned = [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert not bridge.briefs, "no stint is started for an empty pack"
    text = str(returned[0])
    assert "you carry no wood_wall" in text
    assert "needs 4" in text
    assert "craft wood_wall 4 times" in text
    assert "8 plank in all" in text


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
    bridge.model.update(
        make_observation(6, make_entity("ada", (10, 10), inventory={"road": 1}))
    )
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
        agreed="mira needs planks",
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
    assert NO_REFLEX_LINE in await planner.build_prompt()

    bridge.reflex = ReflexBrief(
        instruction="Walk to the beds.",
        success_condition="you are next to a bed",
        max_ticks=10,
        trigger_distance=5,
    )
    bridge.reflex_notes = [
        "[reflex ran ticks 3-9: ended because threat_gone; " "health 20 -> 17]"
    ]
    prompt = await planner.build_prompt()
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
            {
                "direction": "E",
                "opening_line": "Who needs planks?",
                "purpose": "find planks",
            },
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
        model=_call_tool(
            "open_conversation",
            {"direction": "E", "opening_line": "hi", "purpose": "say hi"},
        )
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
        # `sleep` spends the budget, so each pass needs a fresh turn.
        deps.budget.reset(planner_module.MAX_TOOL_CALLS_PER_TURN, 0)
        bridge.direct_result = "sleep on bed_1 -> sleep ok: asleep on bed_1"
        bridge.wake_result = (
            f"slept on bed_1 from tick 5 to tick 60 (55 ticks); woke because "
            f"{reason}; fatigue 80 -> 25"
        )
        agent = build_planner_agent("test")
        with agent.override(model=_call_tool("sleep", {"bed": "bed_1"})):
            result = await agent.run("go", deps=deps)

        intent, _ = bridge.actions[-1]
        assert intent.sleep.object_id == "bed_1"
        assert f"woke because {reason}" in result.output
        assert "fatigue 80 -> 25" in result.output
        assert "Your turn ends here" in result.output


async def test_sleep_on_the_ground_names_no_bed(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "sleep on the ground -> sleep ok: asleep on the ground"
    bridge.wake_result = "slept on the ground from tick 5 to tick 9 (4 ticks)"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("sleep", {"bed": ""})):
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
    with agent.override(model=_call_tool("sleep", {"bed": "bed_1"})):
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


# --- brief hails and the channel narrative (docs/09 sections 9 and 10) ------


def _call_tool_once(tool_name: str, json_args: str) -> FunctionModel:
    """Calls `tool_name` once with fixed arguments, then echoes its result.

    Echoing puts the tool's own text in `result.output`, so a test can read
    what the planner was told without digging through the message history.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returned = [
            part for part in messages[-1].parts if isinstance(part, ToolReturnPart)
        ]
        if returned:
            return ModelResponse(parts=[TextPart(str(returned[0].content))])
        return ModelResponse(parts=[ToolCallPart(tool_name, json_args)])

    return FunctionModel(respond)


async def test_shout_reports_who_heard_it_within_sixty_tiles(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_results = ["shout 'wolf!' -> say ok: heard: cleo, finn, mira"]
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool_once("shout", '{"text": "wolf!"}')):
        result = await agent.run("go", deps=deps)

    assert "shouted to cleo, finn, mira (within 60 tiles)" in result.output


async def test_start_stint_passes_the_hails_into_the_brief(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    deps.bridge.model.update(
        make_observation(
            6, make_entity("ada", (10, 10)), entities=[make_entity("mira", (12, 10))]
        )
    )
    args = (
        '{"instruction": "Gather stone", "success_condition": "10 stone", '
        '"max_ticks": 30, "hails": [{"settler": "mira", '
        '"line": "Shall we plan the wall?"}]}'
    )
    with agent.override(model=_call_tool_once("start_stint", args)):
        await agent.run("go", deps=deps)

    assert bridge.briefs[0].hails == (BriefHail("mira", "Shall we plan the wall?"),)


def test_a_brief_hail_must_name_a_settler_this_actor_has_met() -> None:
    model = _seen_model((11, 10))
    assert validated_hails([{"settler": "mira", "line": "hi"}], model) == (
        BriefHail("mira", "hi"),
    )
    with pytest.raises(ModelRetry, match="settlers you have met: mira"):
        validated_hails([{"settler": "zeno", "line": "hi"}], model)
    with pytest.raises(ModelRetry, match="cannot hail yourself"):
        validated_hails([{"settler": "ada", "line": "hi"}], model)
    with pytest.raises(ModelRetry, match="at most 3 hails"):
        validated_hails([{"settler": "mira", "line": "hi"}] * 4, model)
    with pytest.raises(ModelRetry, match="at most 300"):
        validated_hails([{"settler": "mira", "line": "x" * 301}], model)
    with pytest.raises(ModelRetry, match="needs a `settler` and a `line`"):
        validated_hails([{"settler": "mira", "line": "  "}], model)


async def test_talk_to_refuses_a_settler_it_has_never_seen(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool_once(
            "talk_to",
            '{"entity_id": "zeno", "opening_line": "hello", "purpose": "catch up"}',
        )
    ):
        result = await agent.run("go", deps=deps)

    assert not bridge.actions, "nothing is submitted for a settler nobody has seen"
    assert "never seen a settler called zeno" in result.output


async def test_the_planner_has_no_say_tool(deps: PlannerDeps) -> None:
    agent = build_planner_agent("test")
    model = TestModel(call_tools=[])
    with agent.override(model=model):
        await agent.run("go", deps=deps)
    names = {tool.name for tool in model.last_model_request_parameters.function_tools}
    assert "say" not in names
    assert {"shout", "talk_to", "start_stint"} <= names


def test_the_prompt_no_longer_offers_invitations() -> None:
    condensed = " ".join(SETTLEMENT_NARRATIVE.split())
    assert "open_to_talk" not in SETTLEMENT_NARRATIVE
    assert "invitation" not in condensed
    assert "interrupted: conversation conv_N started" in SETTLEMENT_NARRATIVE
    assert "hails: []" in SETTLEMENT_NARRATIVE
    assert "hail a settler you named with the line you wrote" in condensed


def test_the_prompt_lists_the_four_channels_with_their_purpose() -> None:
    condensed = " ".join(SETTLEMENT_NARRATIVE.split())
    assert "Reaching the others, and what each way is good for:" in condensed
    assert "`shout` reaches 60 tiles" in condensed
    assert "The opening line is heard by everyone within 10 tiles" in condensed
    assert "A message board: twenty notes" in condensed
    assert "Sign: holds one line" in condensed
    assert "crafts one from 2 wood if you need it" in condensed


# -- the goal, and the brief example's hail cue ------------------------------


def test_the_prompt_gives_the_settler_a_place_of_its_own_to_find() -> None:
    assert (
        "Each of you also has to find your place in it: what you do, whom you work "
        "with,\nand what you are known for." in SETTLEMENT_NARRATIVE
    )


def test_the_goal_names_a_shelter_of_your_own_and_a_dependable_tomorrow() -> None:
    assert (
        "Together, build a civilization: a settlement that\nlasts, where every one "
        "of you has a shelter of your own to sleep in, and where\nfood, safety and "
        "rest are things you can count on tomorrow and not only today."
        in SETTLEMENT_NARRATIVE
    )


def test_the_settler_count_comes_from_the_scenario() -> None:
    assert SETTLEMENT_NARRATIVE.startswith("You are one of twelve people")
    assert settlement_narrative(6).startswith("You are one of six people")
    assert settlement_narrative(2).startswith("You are one of two people")
    # Outside the spelled range the digits are used rather than a wrong word.
    assert settlement_narrative(20).startswith("You are one of 20 people")
    # Nothing but the count changes.
    assert settlement_narrative(6).replace("six", "twelve", 1) == (SETTLEMENT_NARRATIVE)


def test_the_converser_and_journal_prompts_take_the_same_count() -> None:
    assert converser_narrative(6).startswith("You are one of six people")
    assert journal_narrative(6).startswith("You are one of six people")
    assert CONVERSER_NARRATIVE.startswith("You are one of twelve people")
    assert JOURNAL_NARRATIVE.startswith("You are one of twelve people")


def test_the_converser_prompt_states_the_goal_once_and_briefly() -> None:
    assert (
        "Together, build a\ncivilization that lasts: a shelter of your own each, and "
        "food, safety and rest\nyou can count on tomorrow." in CONVERSER_NARRATIVE
    )


def test_the_brief_example_says_when_to_use_the_hail() -> None:
    assert (
        "If dov comes\n      into view, walk over and hail him." in SETTLEMENT_NARRATIVE
    )


# --- the sleep-time journal (docs/12_sleep_journal.md) -----------------------


async def test_a_turn_that_lived_through_a_wake_drops_its_history(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    with planner.agent.override(model=TestModel(call_tools=[], custom_output_text="A")):
        await planner.take_turn()
    assert planner.history, "an ordinary turn keeps its history"

    planner.note_life_event("woke")
    with planner.agent.override(model=TestModel(call_tools=[], custom_output_text="B")):
        await planner.take_turn()

    # The wake landed between turns, so the reset happens before the next one
    # starts, and nothing from before the sleep is in what the model sees.
    assert not any("A" in str(message) for message in planner.history)
    events = [
        (line["event"], line.get("reason"))
        for line in planner_lines(trace)
        if line["event"] in ("turn_start", "history_reset")
    ]
    assert events[:3] == [
        ("turn_start", None),
        ("history_reset", "woke"),
        ("turn_start", None),
    ]


async def test_a_respawn_drops_the_history_with_its_own_reason(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)

    planner.note_life_event("respawned")
    with planner.agent.override(model=TestModel(call_tools=[], custom_output_text="A")):
        await planner.take_turn()

    # Reset before the turn, so the turn's own messages are what remains.
    assert planner.history
    reasons = [
        line.get("reason")
        for line in planner_lines(trace)
        if line["event"] == "history_reset"
    ]
    assert reasons == ["respawned"]


async def test_the_prompt_waits_for_an_in_flight_rewrite_before_reading_the_journal(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    # The rewrite lands while the turn is parked in `await_journal`.
    bridge.on_journal_wait = lambda: Journal(
        story_so_far="I slept by the lake.", tomorrow="Chop six wood."
    ).save(planner.memory_path)

    prompt = await planner.build_prompt()

    assert bridge.journal_waits == 1
    assert "I slept by the lake." in prompt
    assert "Chop six wood." in prompt


async def test_turn_start_traces_the_journal_as_sections(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    trace = AgentTrace("ada", tmp_path)
    planner = Planner(bridge, "ada", model_name="test", trace=trace)
    Journal(
        story_so_far="Day one by the lake.",
        tomorrow="Chop six wood.",
        scratch=["- bo owes me 3 planks"],
    ).save(planner.memory_path)

    with planner.agent.override(model=TestModel(custom_output_text="Noted.")):
        await planner.take_turn()

    start = planner_lines(trace)[0]
    assert start["event"] == "turn_start"
    sections = start["journal"]
    assert isinstance(sections, dict)
    assert list(sections) == list(ALL_SECTIONS)
    assert sections[SECTION_STORY] == "Day one by the lake."
    assert sections[SECTION_TOMORROW] == "Chop six wood."
    assert sections[SECTION_SCRATCH] == "- bo owes me 3 planks"


async def test_the_turns_reflection_and_every_tool_call_reach_the_day_log(
    bridge: RecordingBridge, tmp_path: Path
) -> None:
    planner = Planner(bridge, "ada", model_name="test", trace=AgentTrace.disabled())
    test_model = TestModel(call_tools=["shout"], custom_output_text="Said hello.")
    with planner.agent.override(model=test_model):
        await planner.take_turn()

    kinds = [entry.kind for entry in planner.day_log.entries]
    assert "call" in kinds and "result" in kinds and "reflection" in kinds
    calls = [e.text for e in planner.day_log.entries if e.kind == "call"]
    assert calls[0].startswith("shout(")
    assert planner.day_log.entries[-1].text == "Said hello."


async def test_the_day_log_records_the_bare_tool_result_without_the_footer(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "shout hello -> accepted"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("shout", {"text": "hello"})):
        result = await agent.run("go", deps=deps)

    logged = [e.text for e in deps.day_log.entries if e.kind == "result"]
    assert logged == ["shout hello -> accepted"]
    assert "tool budget" in result.output, "the footer still reaches the model"


async def test_a_wrong_kwarg_gets_a_retry_naming_the_tools_parameters(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """docs/09 section 10, item 3: a bad kwarg name must self-correct."""
    bridge.direct_result = "sleep on bed_1 -> sleep ok: asleep on bed_1"
    bridge.wake_result = "slept on bed_1 from tick 5 to tick 9 (4 ticks)"
    seen_retry_texts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last_part = messages[-1].parts[-1]
        if isinstance(last_part, RetryPromptPart):
            seen_retry_texts.append(str(last_part.content))
            return ModelResponse(
                parts=[ToolCallPart("sleep", json.dumps({"bed": "bed_1"}))]
            )
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart("sleep", json.dumps({"bed_object_id": "bed_1"}))]
            )
        return ModelResponse(parts=[TextPart(str(messages[-1].parts[0].content))])

    agent = build_planner_agent("test")
    with agent.override(model=FunctionModel(respond)):
        result = await agent.run("go", deps=deps)

    assert seen_retry_texts, "the model must have been retried once"
    assert "sleep takes: bed (optional)" in seen_retry_texts[0]
    assert "Your turn ends here" in result.output


async def test_the_sleep_tool_spends_the_whole_budget(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    deps.budget.reset(planner_module.MAX_TOOL_CALLS_PER_TURN, tick=0)
    bridge.direct_result = "sleep on the ground -> sleep ok: asleep on the ground"
    bridge.wake_result = "slept on the ground from tick 5 to tick 9 (4 ticks)"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("sleep", {"bed": ""})):
        result = await agent.run("go", deps=deps)

    assert deps.budget.left == 0
    assert "Your turn ends here" in result.output


# --- hailing (docs/09 section 9) --------------------------------------------


def _seen_model(position: tuple[int, int] = (11, 10)) -> WorldModel:
    """A model for ada who can see mira, who has said nothing at all."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("mira", position)],
        )
    )
    return model


async def test_talk_to_hails_a_settler_next_to_you(tmp_path: Path) -> None:
    bridge = RecordingBridge(_seen_model((11, 10)))
    bridge.direct_result = "hail mira -> converse ok: hail conv_1 mira"
    bridge.conversation_reports.append(
        ConversationReport(
            conversation_id="conv_1",
            start_tick=5,
            end_tick=30,
            participants=("ada", "mira"),
            end_reason="closed",
            transcript=(TranscriptLine(5, "ada", "Got a moment?"),),
            agreed="mira will cut reeds",
        )
    )
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool_once(
            "talk_to",
            '{"entity_id": "mira", "opening_line": "Got a moment?", "purpose": "ask for planks"}',
        )
    ):
        result = await agent.run("go", deps=deps)

    (intent, _description) = bridge.actions[0]
    assert intent.converse.action == "hail"
    assert intent.converse.target_entity_id == "mira"
    assert intent.converse.text == "Got a moment?"
    assert not bridge.briefs, "nobody walks anywhere when already adjacent"
    assert "CONVERSATION REPORT: conv_1" in result.output
    assert "mira will cut reeds" in result.output


async def test_talk_to_walks_to_a_settler_before_hailing(tmp_path: Path) -> None:
    bridge = RecordingBridge(_seen_model((14, 10)))
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool_once(
            "talk_to",
            '{"entity_id": "mira", "opening_line": "Got a moment?", "purpose": "ask for planks"}',
        )
    ):
        result = await agent.run("go", deps=deps)

    assert bridge.briefs, "the walk is a code-driven stint"
    assert isinstance(bridge.drivers[0], ApproachDriver)
    # The recording bridge never moves the actor, so the hail is not reached.
    assert not bridge.actions
    assert "you are not next to mira yet" in result.output
    assert "last seen at (14, 10)" in result.output


async def test_talk_to_needs_an_opening_line_to_hail(tmp_path: Path) -> None:
    bridge = RecordingBridge(_seen_model((11, 10)))
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool_once(
            "talk_to",
            '{"entity_id": "mira", "opening_line": "  ", "purpose": "ask for planks"}',
        )
    ):
        result = await agent.run("go", deps=deps)

    assert not bridge.actions
    assert "a hail needs an opening line" in result.output


async def test_talk_to_refuses_an_asleep_settler_without_walking(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("mira", (11, 10), asleep=True)],
        )
    )
    bridge = RecordingBridge(model)
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool_once(
            "talk_to",
            '{"entity_id": "mira", "opening_line": "Got a moment?", '
            '"purpose": "ask for planks"}',
        )
    ):
        result = await agent.run("go", deps=deps)

    assert "mira is asleep" in result.output
    assert not bridge.briefs, "asleep is refused before any walk"
    assert not bridge.actions


async def test_a_refused_hail_reports_the_worlds_reason(tmp_path: Path) -> None:
    bridge = RecordingBridge(_seen_model((11, 10)))
    bridge.direct_result = (
        "hail mira -> converse failed: mira was in a conversation 14 ticks ago "
        "and cannot be hailed for another 46 ticks"
    )
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool_once(
            "talk_to",
            '{"entity_id": "mira", "opening_line": "Got a moment?", "purpose": "ask for planks"}',
        )
    ):
        result = await agent.run("go", deps=deps)

    assert "cannot be hailed for another 46 ticks" in result.output
    assert "mira was last seen at (11, 10)" in result.output


def test_the_prompt_states_the_hail_physics() -> None:
    condensed = " ".join(SETTLEMENT_NARRATIVE.split())
    assert "walks you to them and says your opening line out loud" in condensed
    assert "out of a conversation for at least 60 ticks" in condensed
    assert "walks up and hails you" in condensed


def test_the_converser_prompt_says_a_hailed_seat_can_happen() -> None:
    condensed = " ".join(CONVERSER_NARRATIVE.split())
    assert "someone walked up and addressed you" in condensed


# -- body status, arrival, eating and building (2026-09-20 hamlet-run fixes) --


def _returned_text(result: object) -> list[str]:
    """Every tool return in a finished run, as text."""
    return [
        str(part.content)
        for message in result.all_messages()  # type: ignore[attr-defined]
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


async def test_every_tool_result_carries_food_health_and_fatigue(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6, make_entity("ada", (10, 10), food=34, health=12, fatigue=41)
        )
    )
    agent = build_planner_agent("test")
    deps.budget.reset(20, tick=5)
    with agent.override(model=one_tool_call("recall", {})):
        result = await agent.run("go", deps=deps)
    assert "food 34/100, health 12/20, fatigue 41/100]" in _returned_text(result)[0]


def test_low_food_raises_an_alert_with_the_physics(world_model: WorldModel) -> None:
    world_model.update(make_observation(6, make_entity("ada", (10, 10), food=20)))
    alerts = body_alerts(world_model)
    assert len(alerts) == 1
    assert alerts[0].startswith("!! FOOD LOW: food 20/100, falling 1 every 4 ticks")
    assert "at 0 you lose 1 health every 4 ticks" in alerts[0]
    assert "One berry restores 20 food." in alerts[0]


def test_no_alert_while_food_is_comfortable(world_model: WorldModel) -> None:
    world_model.update(make_observation(6, make_entity("ada", (10, 10), food=26)))
    assert body_alerts(world_model) == []


def test_starving_and_nearly_collapsing_both_get_a_line(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(6, make_entity("ada", (10, 10), food=0, health=7, fatigue=92))
    )
    alerts = body_alerts(world_model)
    assert len(alerts) == 2
    assert alerts[0].startswith("!! STARVING: food 0/100")
    assert "lose 1 health every 4 ticks until you eat" in alerts[0]
    assert alerts[1].startswith("!! FATIGUE 92/100:")
    assert "at 100 you collapse where you stand and sleep until fatigue 70" in (
        alerts[1]
    )


def test_a_sleeping_body_gets_no_alerts(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(6, make_entity("ada", (10, 10), food=0, asleep=True))
    )
    assert body_alerts(world_model) == []


async def test_the_turn_prompt_carries_the_body_alerts(
    deps: PlannerDeps, bridge: RecordingBridge, tmp_path: Path
) -> None:
    bridge.model.update(make_observation(6, make_entity("ada", (10, 10), food=4)))
    planner = Planner(
        bridge, "ada", model_name="test", trace=AgentTrace("ada", tmp_path)
    )
    prompt = await planner.build_prompt()
    assert "!! FOOD LOW: food 4/100" in prompt


# -- travel_to ---------------------------------------------------------------


def test_travel_arrival_reads_the_tile_under_and_beside_you(
    world_model: WorldModel,
) -> None:
    assert travel_arrival(world_model, (10, 10)) == "arrived"
    assert travel_arrival(world_model, (12, 12)) == ""
    # A tile nobody can stand on counts as reached from beside it.
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            tiles=make_tiles((10, 10), blocked=[(11, 10)]),
        )
    )
    assert travel_arrival(world_model, (11, 10)) == "arrived_next_to"
    assert travel_arrival(world_model, (12, 10)) == ""


async def test_travel_to_spends_no_tick_when_you_are_already_there(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("travel_to", {"x": 10, "y": 10})):
        result = await agent.run("go", deps=deps)
    assert not bridge.briefs, "no stint is started for a walk already finished"
    text = _returned_text(result)[0]
    assert "no walk needed: you are standing on (10, 10). No tick spent." in text


async def test_travel_to_stops_beside_a_destination_nobody_can_stand_on(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            tiles=make_tiles((10, 10), blocked=[(11, 10)]),
        )
    )
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("travel_to", {"x": 11, "y": 10})):
        result = await agent.run("go", deps=deps)
    assert not bridge.briefs
    assert "(11, 10) cannot be stood on; you are next to it at (10, 10)" in (
        _returned_text(result)[0]
    )


async def test_travel_to_hands_the_stint_an_arrival_end_rule(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("travel_to", {"x": 14, "y": 10})):
        await agent.run("go", deps=deps)
    end_check = bridge.end_checks[0]
    assert end_check(bridge.model) == "", "still walking where it started"
    bridge.model.update(make_observation(7, make_entity("ada", (14, 10))))
    assert end_check(bridge.model) == "arrived"


async def test_an_arrived_walk_says_where_the_body_ended_up(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.stint_end_reason = "arrived"
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("travel_to", {"x": 12, "y": 10})):
        result = await agent.run("go", deps=deps)
    assert "you are standing on (12, 10)" in _returned_text(result)[0]


# -- eat ---------------------------------------------------------------------


async def test_eat_with_a_berry_in_the_pack_just_eats_it(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    carrying(bridge.model, {"berry": 2})
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("eat", {})):
        await agent.run("go", deps=deps)
    assert [description for _, description in bridge.actions] == ["eat berry"]


async def test_eat_with_an_empty_pack_picks_the_berry_under_your_feet(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[make_object("bush_7", "bush", (10, 10), {"berry_count": "1"})],
        )
    )
    bridge.direct_results = [
        "pick a berry off bush_7 -> collect ok: collected berry",
        "eat berry -> eat ok: food 80 -> 100",
    ]
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("eat", {})):
        result = await agent.run("go", deps=deps)
    assert [description for _, description in bridge.actions] == [
        "pick a berry off bush_7",
        "eat berry",
    ]
    text = _returned_text(result)[0]
    assert "collected berry" in text and "food 80 -> 100" in text


async def test_eat_with_nothing_to_eat_names_the_nearest_berry_bushes(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("bush_7", "bush", (14, 10), {"berry_count": "1"}),
                make_object("bush_8", "bush", (12, 10), {"berry_count": "0"}),
            ],
        )
    )
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("eat", {})):
        result = await agent.run("go", deps=deps)
    assert not bridge.actions, "nothing is submitted, so no tick is spent"
    text = _returned_text(result)[0]
    assert "you carry no berry, and there is none to pick where you stand" in text
    assert "bush bush_7 at (14, 10) (d4): berry (in view)" in text
    assert "bush_8" not in text


# -- build crafts what it is short of ----------------------------------------


class CraftingBridge(RecordingBridge):
    """A bridge whose `craft` actions really change the pack, as the world does."""

    def __init__(self, world_model: WorldModel, inventory: dict[str, int]) -> None:
        super().__init__(world_model)
        self.inventory = dict(inventory)
        self._observe()

    def _observe(self) -> None:
        self.model.update(
            make_observation(
                self.model.tick + 1,
                make_entity("ada", (10, 10), inventory=self.inventory),
                objects=self.objects,
            )
        )

    objects: list[pb.WorldObject] = []

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        self.actions.append((intent, description))
        if not intent.HasField("craft"):
            return f"{description} -> ok"
        recipe = RECIPES[intent.craft.recipe]
        for kind, amount in recipe.inputs.items():
            if self.inventory.get(kind, 0) < amount:
                self._observe()
                return f"{description} -> craft failed: not enough {kind}"
            self.inventory[kind] -= amount
        self.inventory[intent.craft.recipe] = (
            self.inventory.get(intent.craft.recipe, 0) + recipe.output_count
        )
        self._observe()
        return f"{description} -> craft ok: crafted {intent.craft.recipe}"


def _build_call(kind: str) -> FunctionModel:
    return one_tool_call(
        "build",
        {
            "kind": kind,
            "shape": "line",
            "x1": 10,
            "y1": 12,
            "x2": 12,
            "y2": 12,
            "max_ticks": 60,
        },
    )


async def test_build_crafts_the_pieces_and_their_planks_before_it_starts(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 6})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("wood_wall")):
        result = await agent.run("go", deps=deps)
    assert bridge.inventory["wood_wall"] == 3
    assert bridge.briefs, "the build ran once it had pieces"
    assert "for this build, crafted 6 plank, 3 wood_wall" in _returned_text(result)[0]


async def test_build_says_what_it_could_not_craft_and_starts_nothing(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"stone": 4})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("wood_wall")):
        result = await agent.run("go", deps=deps)
    assert not bridge.briefs
    text = _returned_text(result)[0]
    assert "you carry no wood_wall" in text
    assert "nothing crafted: plank takes 1 wood and you carry 0" in text


async def test_build_will_not_craft_a_station_recipe_away_from_its_station(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"stone": 9, "clay": 9})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("stone_wall")):
        result = await agent.run("go", deps=deps)
    assert not bridge.briefs
    assert "stone_wall is made at a workshop_table, and there is none on or " in (
        _returned_text(result)[0]
    )


async def test_build_crafts_a_station_recipe_beside_its_station(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"stone": 9, "clay": 9})
    bridge.objects = [make_object("table_1", "workshop_table", (11, 10))]
    bridge._observe()
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("stone_wall")):
        await agent.run("go", deps=deps)
    assert bridge.inventory["stone_wall"] == 3
    assert bridge.briefs


async def test_a_build_that_runs_dry_crafts_again_and_gives_up_in_the_end(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 40})
    # Every stint reports the same empty pack, so the tool refills and retries
    # until its resupply rounds run out rather than looping forever.
    bridge.stint_end_reason = "build_out_of_items"
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("wood_wall")):
        await agent.run("go", deps=deps)
    assert len(bridge.briefs) == planner_module.BUILD_RESUPPLY_ROUNDS


# --- Hamlet round 2 ---------------------------------------------------------


async def test_an_interrupted_call_costs_no_budget(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """bram spent 8 of 20 calls on tools that each bounced off one conversation."""
    deps.budget.reset(20, tick=5)
    bridge.direct_result = "drop 1 wood -> " + conversation_interruption("conv_38")
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("drop", {"kind": "wood", "amount": 1})):
        result = await agent.run("go", deps=deps)
    assert deps.budget.left == 20
    assert "[tool budget: 20 of 20 calls left this turn]" in result.output


async def test_a_reflex_interruption_costs_no_budget(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    deps.budget.reset(20, tick=5)
    bridge.direct_result = f"drop 1 wood -> {INTERRUPTED_BY_REFLEX}"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("drop", {"kind": "wood", "amount": 1})):
        await agent.run("go", deps=deps)
    assert deps.budget.left == 20


async def test_an_ordinary_call_still_costs_budget(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    deps.budget.reset(20, tick=5)
    bridge.direct_result = "drop 1 wood -> drop ok: dropped"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("drop", {"kind": "wood", "amount": 1})):
        await agent.run("go", deps=deps)
    assert deps.budget.left == 19


async def test_a_failed_craft_says_where_the_missing_input_comes_from(
    deps: PlannerDeps, bridge: RecordingBridge, world_model: WorldModel
) -> None:
    """`craft bed -> bed needs 4 plank + 3 fiber` never mentioned reeds."""
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"plank": 4}),
            objects=[make_object("reeds_9", "reeds", (14, 12))],
        )
    )
    bridge.direct_result = "craft bed -> craft failed: bed needs 4 plank + 3 fiber"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "bed"})):
        result = await agent.run("go", deps=deps)
    assert "fiber comes from reeds (bare hands)" in result.output
    assert "reeds_9 at (14, 12)" in result.output


async def test_a_failed_craft_names_no_source_for_a_crafted_input(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "craft wood_wall -> craft failed: needs 2 plank"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "wood_wall"})):
        result = await agent.run("go", deps=deps)
    assert "comes from" not in result.output


def test_the_source_text_is_generic_over_the_extraction_map() -> None:
    assert items.source_text("clay").startswith(
        "clay comes from clay_deposit (bare hands, faster with a pickaxe); "
    )
    assert items.source_text("copper_ore").startswith(
        "copper_ore comes from copper_vein (needs a pickaxe in hand); "
    )
    assert items.source_text("stone").startswith("stone comes from boulder or ")
    assert items.source_text("plank") == ""


def test_the_source_text_says_where_the_source_grows() -> None:
    """finn random-walked for 190 ticks looking for reeds he had never seen."""
    assert items.source_text("fiber") == (
        "fiber comes from reeds (bare hands); reeds grow on the banks and in "
        "the shallows of fresh water (lakes, rivers and their fords), within "
        "2 tiles of the water, and never within 12 tiles of the sea"
    )
    # Four rock types, one habitat: said once.
    assert items.source_text("stone").count("rocks and boulders gather") == 1


def test_look_lists_your_own_placed_pieces(world_model: WorldModel) -> None:
    """Journals carried goals across days but never the build site."""
    world_model.update(
        make_observation(
            7,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("wood_wall_1", "wood_wall", (12, 12), {"owner": "ada"}),
                make_object("wood_wall_2", "wood_wall", (13, 12), {"owner": "ada"}),
                make_object("bed_4", "bed", (16, 16), {"owner": "ada"}),
                make_object("wood_wall_9", "wood_wall", (4, 4), {"owner": "cleo"}),
            ],
        )
    )
    text = describe_world(world_model)
    assert "your placed pieces: 2 wood_wall within (12, 12)-(13, 12)" in text
    assert "1 bed at (16, 16)" in text
    assert "wood_wall_9" not in text.split("your placed pieces:")[1].splitlines()[0]


def test_the_turn_prompt_states_being_enclosed(world_model: WorldModel) -> None:
    """esme sat in a 1-tile cell for 64 ticks and her planner never knew."""
    ring = [
        (10 + dx, 10 + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)
    ]
    world_model.update(
        make_observation(
            8,
            make_entity("ada", (10, 10)),
            objects=[
                make_object(f"wood_wall_{i}", "wood_wall", tile, {"owner": "ada"})
                for i, tile in enumerate(ring)
            ],
        )
    )
    alerts = body_alerts(world_model)
    assert any(line.startswith("!! ENCLOSED:") for line in alerts)
    assert any("dismantle removes a placed piece" in line for line in alerts)


def test_the_prompt_states_the_one_hunger_number_for_sleep() -> None:
    assert (
        f"Falling asleep needs food above {items.HUNGRY_WAKE_FOOD}"
        in SETTLEMENT_NARRATIVE
    )
    assert (
        f"when your\n  food falls to {items.HUNGRY_WAKE_FOOD}" in SETTLEMENT_NARRATIVE
    )


# -- travel_to's own tick budget --------------------------------------------


def test_travel_budget_scales_with_the_walk_and_has_a_floor(
    world_model: WorldModel,
) -> None:
    """Two ticks per remembered step plus 20, never under the floor."""
    near = travel_budget(world_model, (11, 10))
    assert near == planner_module.TRAVEL_MIN_TICKS
    far = travel_budget(world_model, (400, 400))
    assert far > near
    assert far <= planner_module.TRAVEL_MAX_TICKS


async def test_travel_to_takes_no_tick_budget_from_the_model(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("travel_to", {"x": 40, "y": 10})):
        await agent.run("go", deps=deps)
    assert bridge.briefs[0].max_ticks == travel_budget(bridge.model, (40, 10))


async def test_travel_to_with_a_max_ticks_kwarg_is_a_retry_not_a_failure(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """The friendly args validator names the real parameters instead of dying."""
    agent = build_planner_agent("test")
    model = one_tool_call("travel_to", {"x": 12, "y": 10, "max_ticks": 30})
    with agent.override(model=model):
        result = await agent.run("go", deps=deps)
    retries = [
        part.content
        for message in result.all_messages()
        for part in getattr(message, "parts", ())
        if isinstance(getattr(part, "content", None), str)
        and "travel_to takes: x, y" in part.content
    ]
    assert retries, "the retry should name travel_to's parameters"


# -- place crafts what it is short of ----------------------------------------


async def test_place_crafts_the_piece_when_the_inputs_are_in_the_pack(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6, make_entity("ada", (10, 10), inventory={"wood": 6, "stone": 4})
        )
    )
    agent = build_planner_agent("test")
    with agent.override(
        model=one_tool_call("place", {"kind": "chest", "direction": "N"})
    ):
        await agent.run("go", deps=deps)
    kinds = [
        intent.craft.recipe for intent, _ in bridge.actions if intent.HasField("craft")
    ]
    assert "chest" in kinds


async def test_place_without_the_inputs_names_the_shortfall_and_places_nothing(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(
        model=one_tool_call("place", {"kind": "chest", "direction": "N"})
    ):
        result = await agent.run("go", deps=deps)
    text = _returned_text(result)[0]
    assert "chest takes" in text
    assert not [i for i, _ in bridge.actions if i.HasField("place")]


# -- entities known to be dead ------------------------------------------------


def _watch_a_wolf_die(model: WorldModel, tick: int = 7) -> None:
    model.update(
        make_observation(
            tick - 1,
            make_entity("ada", (10, 10)),
            entities=[make_entity("wolf_6", (12, 10), entity_type="wolf")],
        )
    )
    model.update(
        make_observation(
            tick, make_entity("ada", (10, 10)), events=[died_event("wolf_6", "esme")]
        )
    )


def test_the_model_remembers_a_death_it_watched(world_model: WorldModel) -> None:
    _watch_a_wolf_die(world_model)
    death = world_model.death_of("wolf_6")
    assert death is not None
    assert death.fact() == "wolf_6 died at tick 7 (killed by esme)"
    assert [d.entity_id for d in world_model.recent_deaths()] == ["wolf_6"]


def test_a_settler_seen_alive_after_dying_is_no_longer_dead(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    world_model.update(
        make_observation(6, make_entity("ada", (10, 10)), events=[died_event("bram")])
    )
    assert world_model.death_of("bram") is not None
    world_model.update(
        make_observation(
            20,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    assert world_model.death_of("bram") is None


def test_recent_deaths_survives_forgetting_one_while_it_iterates(
    world_model: WorldModel,
) -> None:
    """`recent_deaths` raised `deque mutated during iteration` on three turns.

    `death_of` rewrites `deaths_seen` when a body has been seen alive again,
    and `recent_deaths` calls it from inside its own loop over the deque.
    """
    _watch_a_wolf_die(world_model, tick=7)
    world_model.update(
        make_observation(
            8,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    world_model.update(
        make_observation(9, make_entity("ada", (10, 10)), events=[died_event("bram")])
    )
    # bram respawns: the next `death_of("bram")` forgets his death.
    world_model.update(
        make_observation(
            20,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    assert [d.entity_id for d in world_model.recent_deaths()] == ["wolf_6"]


def test_look_lists_the_wolves_you_saw_die(world_model: WorldModel) -> None:
    _watch_a_wolf_die(world_model)
    assert "wolves you saw die: wolf_6 (tick 7)" in describe_world(world_model)


def test_look_ages_a_wolf_that_is_only_out_of_sight(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("wolf_9", (14, 10), entity_type="wolf")],
        )
    )
    world_model.update(make_observation(25, make_entity("ada", (30, 30))))
    text = describe_world(world_model)
    assert "wolves you know of but cannot see:" in text
    assert "wolf_9 last seen 20 ticks ago" in text


async def test_start_stint_naming_a_dead_wolf_is_refused_with_the_fact(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    _watch_a_wolf_die(bridge.model)
    agent = build_planner_agent("test")
    model = one_tool_call(
        "start_stint",
        {
            "instruction": "Kill wolf_6.",
            "success_condition": "wolf_6 is dead",
            "max_ticks": 30,
        },
    )
    with agent.override(model=model):
        result = await agent.run("go", deps=deps)
    assert not bridge.briefs
    assert any(
        "wolf_6 died at tick 7" in part.content
        for message in result.all_messages()
        for part in getattr(message, "parts", ())
        if isinstance(getattr(part, "content", None), str)
    )


# -- the journal writer knows the goal ---------------------------------------


def test_the_journal_writer_opens_with_the_planner_goal() -> None:
    from agents.jev_agent.journal import journal_narrative

    opening = items.island_opening(6)
    assert settlement_narrative(6).startswith(opening)
    assert journal_narrative(6).startswith(opening)


# --- Hamlet round 4 ---------------------------------------------------------


def test_the_narrative_says_where_each_raw_source_grows() -> None:
    """Nothing told a settler where a resource it had never seen grows."""
    text = settlement_narrative(6)
    assert "Where things are found" in text
    assert "reeds grow on the banks and in the shallows of fresh water" in text
    assert "clay deposits lie in tight patches 2 to 7 tiles back" in text
    assert "berry bushes grow in thickets along the edges of woodland" in text
    assert "Copper and iron veins sit in rock outcrops on high ground" in text


def test_a_failed_craft_says_where_the_missing_material_grows(
    world_model: WorldModel,
) -> None:
    lines = planner_module.missing_input_lines(world_model, RECIPES[items.BED])
    text = "\n".join(lines)
    assert "fiber comes from reeds (bare hands); reeds grow on the banks" in text
    assert "you know of none yet" in text


def _water_tiles(centre: tuple[int, int], water: tuple[int, int]) -> list[pb.Tile]:
    tiles = make_tiles(centre)
    tiles.append(
        pb.Tile(
            position=pb.Position(x=water[0], y=water[1]),
            walkable=True,
            opaque=False,
            floor_type="shallow_water",
        )
    )
    return tiles


def test_an_unseen_water_bound_source_names_the_nearest_water_you_have_seen(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            tiles=_water_tiles((10, 10), (14, 12)),
        )
    )
    assert world_model.nearest_water() == (14, 12)
    text = "\n".join(
        planner_module.missing_input_lines(world_model, RECIPES[items.BED])
    )
    assert "nearest water you have seen: (14, 12) (d4)" in text
    # A material that does not grow by water gets no such line.
    plank_text = "\n".join(
        planner_module.missing_input_lines(world_model, RECIPES[items.WOOD_WALL])
    )
    assert "nearest water" not in plank_text


async def test_a_refused_place_names_what_is_on_the_tile_and_the_free_sides(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """Three blind `place door` calls all failed "target already holds an object"."""
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"door": 1}),
            objects=[make_object("wood_wall_16", "wood_wall", (11, 10))],
        )
    )
    bridge.direct_result = (
        "place door E -> place failed: target already holds an object"
    )
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("place", {"kind": "door", "direction": "E"})):
        result = await agent.run("go", deps=deps)
    text = _returned_text(result)[0]
    assert "(11, 10) holds wood_wall_16" in text
    assert "neighbouring tiles that would take a door:" in text


def _build_call_with_doors(kind: str, door: str) -> FunctionModel:
    return one_tool_call(
        "build",
        {
            "kind": kind,
            "shape": "rect",
            "x1": 10,
            "y1": 12,
            "x2": 13,
            "y2": 15,
            "max_ticks": 120,
            "door": door,
        },
    )


async def test_build_places_a_door_on_the_tiles_it_is_given(
    world_model: WorldModel, tmp_path: Path
) -> None:
    """esme closed a doorless ring around the settlement's only workshop_table."""
    bridge = CraftingBridge(world_model, {"wood": 60, "fiber": 4})
    bridge.objects = [make_object("table_1", "workshop_table", (11, 10))]
    bridge._observe()
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call_with_doors("wood_wall", "10,13")):
        result = await agent.run("go", deps=deps)
    # 12 perimeter tiles, one of them a door, so 11 walls and 1 door.
    assert bridge.inventory["wood_wall"] == 11
    assert bridge.inventory["door"] == 1
    text = _returned_text(result)[0]
    assert "doors (1 tile(s)):" in text
    assert len(bridge.briefs) == 2


async def test_a_door_tile_off_the_shape_is_refused(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 60})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last_part = messages[-1].parts[-1]
        if isinstance(last_part, RetryPromptPart):
            seen.append(str(last_part.content))
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "build",
                    json.dumps(
                        {
                            "kind": "wood_wall",
                            "shape": "rect",
                            "x1": 10,
                            "y1": 12,
                            "x2": 13,
                            "y2": 15,
                            "door": "99,99",
                        }
                    ),
                )
            ]
        )

    agent = build_planner_agent("test")
    with agent.override(model=FunctionModel(respond)):
        await agent.run("go", deps=deps)
    assert seen and "door tiles must be tiles of the shape itself" in seen[0]
    assert not bridge.briefs


async def test_a_door_that_cannot_be_made_leaves_the_tile_a_gap(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 60})  # no fiber -> no rope -> no door
    bridge.objects = [make_object("table_1", "workshop_table", (11, 10))]
    bridge._observe()
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call_with_doors("wood_wall", "10,13")):
        result = await agent.run("go", deps=deps)
    text = _returned_text(result)[0]
    assert "you carry no door" in text
    assert "fiber comes from reeds" in text
