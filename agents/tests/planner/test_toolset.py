"""The tool budget, the clock line and the alerts on every tool result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncIterator
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)
from pydantic_ai.models.test import TestModel
from agents.jev_agent import planner as planner_module
from agents.jev_agent.planner import toolset as planner_toolset
from agents.jev_agent.planner import (
    Planner,
    PlannerDeps,
    body_alerts,
    build_planner_agent,
    threat_alert,
    travel_budget,
)
from agents.jev_agent.stint import INTERRUPTED_BY_REFLEX, conversation_interruption
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import WorldModel
from helpers import make_entity, make_observation

from conftest import (
    RecordingBridge,
    _bitten,
    _call_tool,
    _make_tired,
    _returned_text,
    bridge,
    deps,
    one_tool_call,
    planner_lines,
    world_model,
)


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


def test_no_alert_in_peace(world_model: WorldModel) -> None:
    assert threat_alert(world_model, since_tick=0) == ""


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
    monkeypatch.setattr(planner_toolset, "MAX_TOOL_CALLS_PER_TURN", 2)
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
    monkeypatch.setattr(planner_toolset, "MAX_TOOL_CALLS_PER_TURN", 1)
    monkeypatch.setattr(planner_toolset, "HARD_LIMIT_MARGIN", 2)
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


async def test_a_wrong_kwarg_gets_a_retry_naming_the_tools_parameters(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """docs/09 section 10, item 3: a bad kwarg name must self-correct."""
    _make_tired(bridge.model)
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
    _make_tired(bridge.model)
    deps.budget.reset(planner_toolset.MAX_TOOL_CALLS_PER_TURN, tick=0)
    bridge.direct_result = "sleep on the ground -> sleep ok: asleep on the ground"
    bridge.wake_result = "slept on the ground from tick 5 to tick 9 (4 ticks)"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("sleep", {"bed": ""})):
        result = await agent.run("go", deps=deps)

    assert deps.budget.left == 0
    assert "Your turn ends here" in result.output


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


def test_a_sleeping_body_gets_no_alerts(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(6, make_entity("ada", (10, 10), food=0, asleep=True))
    )
    assert body_alerts(world_model) == []


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
