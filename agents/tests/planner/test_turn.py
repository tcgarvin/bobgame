"""One planner turn: the run loop, the history and the trace."""

from __future__ import annotations

import asyncio
from pathlib import Path
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
from pydantic_ai.models.test import TestModel
from agents import world_pb2 as pb
from agents.jev_agent.planner import turn as planner_turn
from agents.jev_agent.planner import (
    Planner,
    direction_value as direction_value,
    PlannerDeps,
    body_alerts,
    build_planner_agent,
    threat_alert,
    trim_history,
)
from agents.jev_agent.journal import (
    ALL_SECTIONS,
    SECTION_SCRATCH,
    SECTION_STORY,
    SECTION_TOMORROW,
    Journal,
)
from agents.jev_agent.stint import Brief, StintReport
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import WorldModel
from helpers import make_entity, make_observation

from conftest import (
    RecordingBridge,
    _bitten,
    _call_tool,
    _returned_text,
    _watch_a_wolf_die,
    bridge,
    deps,
    one_tool_call,
    planner_lines,
    world_model,
)


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


async def test_a_history_reset_is_traced(
    bridge: RecordingBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(planner_turn, "TURN_RETRY_SECONDS", 0.0)
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
    monkeypatch.setattr(planner_turn, "TURN_RETRY_SECONDS", 0.0)
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


async def test_a_structure_without_a_direction_is_refused(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("place", {"kind": "wood_wall"})):
        await agent.run("go", deps=deps)
    assert not bridge.actions


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


async def test_the_planner_has_no_say_tool(deps: PlannerDeps) -> None:
    agent = build_planner_agent("test")
    model = TestModel(call_tools=[])
    with agent.override(model=model):
        await agent.run("go", deps=deps)
    names = {tool.name for tool in model.last_model_request_parameters.function_tools}
    assert "say" not in names
    assert {"shout", "talk_to", "start_stint"} <= names


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


async def test_an_arrived_walk_says_where_the_body_ended_up(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.stint_end_reason = "arrived"
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("travel_to", {"x": 12, "y": 10})):
        result = await agent.run("go", deps=deps)
    assert "you are standing on (12, 10)" in _returned_text(result)[0]


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
