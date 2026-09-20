"""The tool budget, the dependencies every tool gets, and the toolset wrapper.

The per-turn tool budget is soft: every tool result tells the model how many
calls are left, and a call past the budget is refused with a message instead
of being run. The turn then ends normally, with its reflection and history
intact. (The old hard limit of 12 ended 70% of turns by exception, and every
one of those turns was forgotten.) Measured on a 30-call budget, 29 of 50
turns spent it and the sink was micro-movement: `move` alone was 312 of 1260
calls. With walking now only reachable through Jev, `travel_to` and `build`,
20 calls is enough for a turn's worth of decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset
from pydantic_ai.toolsets.abstract import SchemaValidatorProt
from pydantic_core import SchemaValidator

from ..bridge import AgentBridge
from ..briefs import was_interrupted
from ..journal import DayLog, KIND_CALL, KIND_RESULT, compact_args
from .common import MAX_TOOL_CALLS_PER_TURN as MAX_TOOL_CALLS_PER_TURN
from .status import alert_window_start, body_alerts, threat_alert, turn_clock_line

# Warn the model to wrap up when this few calls are left.
TOOL_BUDGET_WARNING_AT = 5
# pydantic-ai's hard stop, a backstop for a model that ignores the refusals.
HARD_LIMIT_MARGIN = 6
# pydantic-ai's default retry count per tool call, raised from 2: a validation
# retry (wrong kwarg name) should not burn the model's only chances to fix a
# real logic error too (docs/09 section 10, item 3).
PLANNER_TOOL_RETRIES = 3


@dataclass
class ToolBudget:
    """How many tool calls the current planner turn has made.

    Mutable on purpose: the planner resets it at the start of each turn and the
    toolset wrapper counts calls into it. One planner, one event loop.
    """

    limit: int = MAX_TOOL_CALLS_PER_TURN
    used: int = 0
    turn_start_tick: int = 0

    @property
    def left(self) -> int:
        """Calls still allowed this turn."""
        return max(0, self.limit - self.used)

    def reset(self, limit: int, tick: int) -> None:
        """Start a new turn at world tick `tick` with `limit` calls to spend."""
        self.limit = limit
        self.used = 0
        self.turn_start_tick = tick

    def spend(self) -> None:
        """Spend the whole budget, so every later call this turn is refused."""
        self.used = self.limit

    def footer(self) -> str:
        """The line appended to every tool result."""
        line = f"[tool budget: {self.left} of {self.limit} calls left this turn]"
        if self.left == 0:
            return f"{line} That was your last call: write your reflection now."
        if self.left <= TOOL_BUDGET_WARNING_AT:
            return (
                f"{line} Nearly spent: hand the work to Jev with one start_stint "
                "or write your reflection."
            )
        return line


BUDGET_SPENT_MESSAGE = (
    "NOT EXECUTED: your tool budget for this turn is spent. Make no more tool "
    "calls. Write your one-paragraph reflection now; your next turn starts "
    "with a full budget."
)


@dataclass
class PlannerDeps:
    """Dependencies handed to every tool call."""

    bridge: AgentBridge
    memory_path: Path
    budget: ToolBudget = field(default_factory=ToolBudget)
    # Everything this day has held, for the next journal rewrite.
    day_log: DayLog = field(default_factory=DayLog)


def _tool_signature_line(tool_def: ToolDefinition) -> str:
    """`sleep takes: bed (optional), other_arg` from a tool's JSON schema.

    pydantic-ai's own validation-error text names only the field that was
    wrong (e.g. "Extra inputs are not permitted"), never what the tool
    actually accepts, so a model that guessed a kwarg name gets no way to
    self-correct. This is appended to every validation retry.
    """
    schema = tool_def.parameters_json_schema or {}
    properties = schema.get("properties", {})
    if not properties:
        return f"{tool_def.name} takes no arguments"
    required = set(schema.get("required", ()))
    names = ", ".join(
        name if name in required else f"{name} (optional)" for name in properties
    )
    return f"{tool_def.name} takes: {names}"


class _FriendlyArgsValidator:
    """Wraps a tool's args validator to name its parameters on failure.

    `ToolManager._validate_tool_args` calls `validate_json`/`validate_python`
    directly on `ToolsetTool.args_validator`; wrapping it here is the one place
    that reaches every planner tool's validation without touching pydantic-ai
    itself (docs/09 section 10, item 3).
    """

    def __init__(
        self, inner: SchemaValidator | SchemaValidatorProt, tool_def: ToolDefinition
    ) -> None:
        self._inner = inner
        self._tool_def = tool_def

    def _retry(self, error: Exception) -> None:
        raise ModelRetry(f"{error}\n{_tool_signature_line(self._tool_def)}") from error

    def validate_json(self, *args: Any, **kwargs: Any) -> Any:
        """`SchemaValidator.validate_json`, with a friendlier failure."""
        try:
            return self._inner.validate_json(*args, **kwargs)
        except ValidationError as error:
            self._retry(error)

    def validate_python(self, *args: Any, **kwargs: Any) -> Any:
        """`SchemaValidator.validate_python`, with a friendlier failure."""
        try:
            return self._inner.validate_python(*args, **kwargs)
        except ValidationError as error:
            self._retry(error)


@dataclass
class BudgetedToolset(WrapperToolset[PlannerDeps]):
    """Counts tool calls into `deps.budget` and tells the model what is left.

    A call past the budget is refused with a message rather than an exception,
    so the turn ends with its reflection and its history instead of vanishing.
    """

    async def get_tools(
        self, ctx: RunContext[PlannerDeps]
    ) -> dict[str, ToolsetTool[PlannerDeps]]:
        tools = await self.wrapped.get_tools(ctx)
        return {
            name: replace(
                tool,
                args_validator=_FriendlyArgsValidator(
                    tool.args_validator, tool.tool_def
                ),
            )
            for name, tool in tools.items()
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[PlannerDeps],
        tool: ToolsetTool[PlannerDeps],
    ) -> Any:
        budget = ctx.deps.budget
        if budget.left == 0:
            return BUDGET_SPENT_MESSAGE
        budget.used += 1
        model = ctx.deps.bridge.model
        day_log = ctx.deps.day_log
        day_log.add(model.tick, KIND_CALL, f"{name}({compact_args(tool_args)})")
        result = await self.wrapped.call_tool(name, tool_args, ctx, tool)
        if was_interrupted(str(result)):
            # A reflex or a conversation took the body before the action ran,
            # so nothing happened and nothing is charged. One settler spent 8
            # of its 20 calls on tools that each bounced off one conversation.
            budget.used = max(0, budget.used - 1)
        # Recorded before the footer lines below: the journal wants what the
        # tool said, not the clock and the budget.
        day_log.add(model.tick, KIND_RESULT, str(result), tool=name)
        lines = [str(result)]
        # A reflex may have run inside the tool call, or a conversation may
        # have started and ended (or while the model was writing it); the
        # planner is told as soon as it asks anything.
        lines.extend(ctx.deps.bridge.drain_notes())
        lines.append(turn_clock_line(model, budget.turn_start_tick))
        since_tick = alert_window_start(model.tick, budget.turn_start_tick)
        alert = threat_alert(model, since_tick)
        if alert:
            lines.append(alert)
        lines.extend(body_alerts(model))
        lines.append(budget.footer())
        return "\n".join(lines)
