"""The talking tools: conversations, hails, shouts, giving and the reflex."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from agents import world_pb2 as pb
from agents.jev_agent import actions as actions_module, planner as planner_module
from agents.jev_agent.planner import (
    SETTLEMENT_NARRATIVE,
    PlannerDeps,
    build_planner_agent,
    validated_hails as validated_hails,
)
from agents.jev_agent.conversation import ApproachDriver, ConversationReport
from agents.jev_agent.options import BriefHail
from agents.jev_agent.reflex import EMPTY_REFLEX
from agents.jev_agent.worldmodel import TranscriptLine, WorldModel
from helpers import (
    converse_object,
    make_entity,
    make_object,
    make_observation,
    utterance_event,
)

from conftest import (
    RecordingBridge,
    _call_tool,
    _call_tool_once,
    _returned_text,
    bridge,
    deps,
    world_model,
)


async def test_shout_uses_the_shout_channel(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["shout"])):
        await agent.run("go", deps=deps)
    ((intent, _description),) = bridge.actions
    assert intent.say.channel == "shout"


async def test_shout_obeys_the_same_cooldown_jev_does(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """One cooldown for both layers: the planner used to ignore it entirely."""
    bridge.model.update(
        make_observation(
            bridge.model.tick + 1,
            make_entity("ada", bridge.model.position),
            events=[utterance_event("ada", "here!", (10, 10), channel="shout")],
        )
    )
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("shout", {"text": "again!"})):
        result = await agent.run("go", deps=deps)

    assert not bridge.actions
    assert "No tick spent." in result.output


async def test_a_shout_is_cut_to_the_one_shout_length(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("shout", {"text": "x" * 500})):
        await agent.run("go", deps=deps)
    ((intent, _description),) = bridge.actions
    assert len(intent.say.text) == actions_module.MAX_SHOUT_LENGTH


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
        planner_module.validated_shouts(["a", "b", "c", "d", "e"])
    with pytest.raises(ModelRetry, match="characters"):
        planner_module.validated_shouts(["x" * 121])


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
    bridge.model.update(
        make_observation(6, make_entity("ada", (10, 10), inventory={"plank": 4}))
    )
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


async def test_give_what_you_do_not_carry_is_refused_without_a_tick(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(
        model=_call_tool("give", {"entity_id": "mira", "kind": "plank", "amount": 3})
    ):
        result = await agent.run("go", deps=deps)

    assert "you carry no plank to give" in _returned_text(result)[0]
    assert bridge.actions == []


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


def test_the_brief_example_says_when_to_use_the_hail() -> None:
    assert (
        "If dov comes\n      into view, walk over and hail him." in SETTLEMENT_NARRATIVE
    )


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
