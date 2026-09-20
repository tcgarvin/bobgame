"""The journal tools: writing a line into today's notes and reading it back."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ...journal import append_scratch_line, read_journal
from ..toolset import PlannerDeps


def register(tools: FunctionToolset[PlannerDeps]) -> None:
    """Register these tools on the planner's toolset."""

    @tools.tool
    async def remember(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Append a line to today's notes in your journal; it lasts past this turn."""
        append_scratch_line(ctx.deps.memory_path, text, ctx.deps.bridge.model.entity_id)
        return "noted"

    @tools.tool
    async def recall(ctx: RunContext[PlannerDeps]) -> str:
        """Read back your journal."""
        return read_memory(ctx.deps.memory_path, ctx.deps.bridge.model.entity_id)


def read_memory(path: Path, entity_id: str = "") -> str:
    """The whole journal, seeded on first read (docs/12_sleep_journal.md)."""
    return read_journal(path, entity_id)
