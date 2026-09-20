"""Shared fixtures and doubles for the planner tests."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Callable, Mapping
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from agents import world_pb2 as pb
from agents.jev_agent import items
from agents.jev_agent.planner import PlannerDeps
from agents.jev_agent.conversation import ConversationReport
from agents.jev_agent.outcomes import ActionOutcome
from agents.jev_agent.reflex import EMPTY_REFLEX, ReflexBrief
from agents.jev_agent.stint import Brief, StintReport, never_ends
from agents.jev_agent.tracelog import AgentTrace
from agents.jev_agent.worldmodel import WorldModel
from helpers import (
    RecordingBridge,
    bridge,
    canned_outcome,
    damaged_event,
    deps,
    died_event,
    make_entity,
    make_object,
    make_observation,
    world_model,
)

__all__ = ["RecordingBridge", "bridge", "deps", "world_model"]


def planner_lines(trace: AgentTrace) -> list[dict[str, object]]:
    """Every record written to `planner.jsonl.gz`."""
    trace.planner.close()
    with gzip.open(trace.directory / "planner.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


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


def _call_tool(tool_name: str, args: Mapping[str, object]) -> FunctionModel:
    """A model that calls `tool_name` once with `args`, then writes a line."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name, json.dumps(args))])
        return ModelResponse(parts=[TextPart(str(messages[-1].parts[0].content))])

    return FunctionModel(respond)


def _make_tired(model: WorldModel) -> WorldModel:
    """Push the body's fatigue to where the world will accept a `sleep`.

    Below `items.MIN_SLEEP_FATIGUE` the world refuses one, and the `sleep` tool
    now says so itself rather than spending a tick finding out.
    """
    model.update(
        make_observation(
            model.tick,
            make_entity("ada", model.position, fatigue=items.MIN_SLEEP_FATIGUE),
        )
    )
    return model


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


def _returned_text(result: object) -> list[str]:
    """Every tool return in a finished run, as text."""
    return [
        str(part.content)
        for message in result.all_messages()  # type: ignore[attr-defined]
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


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
