"""Building the pydantic-ai agent with every planner tool registered."""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.toolsets import FunctionToolset

from .. import items
from ..llm import planner_model_settings
from .prompt import settlement_narrative
from .toolset import PLANNER_TOOL_RETRIES, BudgetedToolset, PlannerDeps
from .tools import (
    body,
    building,
    core,
    items_tools,
    memory,
    reflex_tools,
    signs,
    talking,
)


def build_planner_agent(
    model_name: str, settler_count: int = items.DEFAULT_SETTLER_COUNT
) -> Agent[PlannerDeps, str]:
    """Create the pydantic-ai agent with every planner tool registered.

    The registration order is the order the model is shown the tools in, so it
    is spelled out here rather than left to the module layout.
    """
    tools: FunctionToolset[PlannerDeps] = FunctionToolset()
    agent: Agent[PlannerDeps, str] = Agent(
        model_name,
        deps_type=PlannerDeps,
        output_type=str,
        system_prompt=settlement_narrative(settler_count),
        retries=PLANNER_TOOL_RETRIES,
        model_settings=planner_model_settings(model_name),
        toolsets=[BudgetedToolset(tools)],
    )
    core.register_head(tools)
    building.register_build(tools)
    body.register_eat(tools)
    items_tools.register_containers(tools)
    building.register_craft(tools)
    body.register_sleep(tools)
    building.register_place(tools)
    signs.register_signs(tools)
    building.register_upkeep(tools)
    core.register_wait(tools)
    talking.register_shout(tools)
    signs.register_boards(tools)
    talking.register_conversations(tools)
    items_tools.register_give(tools)
    reflex_tools.register(tools)
    memory.register(tools)
    return agent
