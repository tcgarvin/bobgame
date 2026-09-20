"""The body tools: eat, sleep, wake and travel_to."""

from __future__ import annotations

from pathlib import Path
from pydantic_ai.models.test import TestModel
from agents.jev_agent import items
from agents.jev_agent.planner import toolset as planner_toolset
from agents.jev_agent.planner import Planner, PlannerDeps, build_planner_agent
from agents.jev_agent.tracelog import AgentTrace
from helpers import make_entity, make_object, make_observation, make_tiles

from conftest import (
    RecordingBridge,
    _call_tool,
    _make_tired,
    _returned_text,
    bridge,
    carrying,
    deps,
    one_tool_call,
    planner_lines,
)


async def test_sleep_submits_the_intent_and_returns_the_wake(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    _make_tired(bridge.model)
    for reason in ("rested", "damaged", "hungry", "bed removed", "asked"):
        bridge.actions.clear()
        # `sleep` spends the budget, so each pass needs a fresh turn.
        deps.budget.reset(planner_toolset.MAX_TOOL_CALLS_PER_TURN, 0)
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
    _make_tired(bridge.model)
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
    _make_tired(bridge.model)
    bridge.direct_result = "sleep on bed_1 -> sleep failed: bed_1 is taken"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("sleep", {"bed": "bed_1"})):
        result = await agent.run("go", deps=deps)

    assert not bridge.wake_calls
    assert "is taken" in result.output


async def test_sleep_refuses_a_body_that_is_not_tired_enough(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """The world refuses it anyway; the tool says so without spending a tick."""
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("sleep", {"bed": ""})):
        result = await agent.run("go", deps=deps)

    assert not bridge.actions
    assert not bridge.wake_calls
    assert "not tired enough" in result.output
    assert str(items.MIN_SLEEP_FATIGUE) in result.output


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
