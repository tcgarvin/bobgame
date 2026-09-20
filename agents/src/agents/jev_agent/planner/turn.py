"""Running planner turns back to back for as long as the agent is alive.

One turn is: wait for the body to be awake and alive, build the prompt, run the
pydantic-ai agent under the tool budget, keep the reflection, and trim the
history on a turn boundary.

`MAX_TOOL_CALLS_PER_TURN` and `HARD_LIMIT_MARGIN` are read off `toolset` rather
than imported, so one patch of that module changes both the budget the turn
resets to and the ceiling it runs under.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterable, Mapping, Sequence

import structlog
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from .. import items
from ..bridge import AgentBridge
from ..journal import DayLog, Journal, KIND_REFLECTION
from ..llm import resolve_model_name
from ..pricing import CostLedger, usage_from_messages
from ..stint import StintReport
from ..tracelog import AgentTrace
from . import toolset
from .describe import describe_world
from .factory import build_planner_agent
from .status import alert_window_start, body_alerts, threat_alert
from .toolset import PlannerDeps

logger = structlog.get_logger(__name__)

# Whole turns are kept while they fit; the newest turn is always kept whole.
HISTORY_MESSAGE_LIMIT = 80
STINT_REPORTS_KEPT = 10
TURN_RETRY_SECONDS = 5.0


def _tool_args(part: ToolCallPart) -> dict[str, Any]:
    """The tool call's arguments as a dict, even when the model sent bad JSON."""
    try:
        return part.args_as_dict()
    except (ValueError, TypeError) as error:
        return {"_unparsed": part.args_as_json_str(), "_error": str(error)}


def _starts_turn(message: ModelMessage) -> bool:
    """True for the request that opens a planner turn (it carries the prompt)."""
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )


def trim_history(
    messages: Sequence[ModelMessage], limit: int = HISTORY_MESSAGE_LIMIT
) -> list[ModelMessage]:
    """Keep the first message plus as many whole recent turns as fit in `limit`.

    The cut always falls on a turn boundary: a tool result whose tool call was
    trimmed away is rejected by most providers. The newest turn is kept whole
    even when it alone is longer than `limit`.
    """
    if len(messages) <= limit:
        return list(messages)
    turn_starts = [
        index
        for index, message in enumerate(messages)
        if index > 0 and _starts_turn(message)
    ]
    if not turn_starts:
        return list(messages)
    fitting = [index for index in turn_starts if len(messages) - index < limit]
    cut = fitting[0] if fitting else turn_starts[-1]
    return list(messages[:1]) + list(messages[cut:])


class Planner:
    """Runs planner turns back to back for as long as the agent is alive."""

    def __init__(
        self,
        bridge: AgentBridge,
        entity_id: str,
        *,
        model_name: str = "",
        trace: AgentTrace,
        ledger: CostLedger = CostLedger(),
        settler_count: int = items.DEFAULT_SETTLER_COUNT,
    ) -> None:
        self.model_name = resolve_model_name(model_name)
        self.settler_count = settler_count
        # The agent always passes its own ledger; the default is a sink for
        # tests and scripts that build a planner on its own.
        self.ledger = ledger
        self.bridge = bridge
        self.entity_id = entity_id
        self.trace = trace
        self.memory_path = trace.memory_path
        self.agent = build_planner_agent(self.model_name, settler_count)
        self.day_log = DayLog()
        self.deps = PlannerDeps(
            bridge=bridge, memory_path=self.memory_path, day_log=self.day_log
        )
        self.history: list[ModelMessage] = []
        # "woke" or "respawned" when the body has been through one of those
        # since the current turn started; both drop the history at turn end.
        self._history_reset_reason = ""
        self.reports: list[StintReport] = []
        # The last report's rendered text when it came from a snapshot rather
        # than from a stint this process ran (docs/14 section 4).
        self._restored_report_text = ""
        self.last_thought = ""
        # The journal as the last prompt saw it, traced with `turn_start`.
        self.journal_sections: dict[str, str] = {}
        self.turn = 0
        self._tool_calls_this_turn = 0
        self._turns_without_tools = 0

    def note_report(self, report: StintReport) -> None:
        """Remember a finished stint so the next turn's prompt can mention it."""
        self.reports.append(report)
        del self.reports[:-STINT_REPORTS_KEPT]
        self._restored_report_text = ""

    def last_report_text(self) -> str:
        """The most recent stint report as the prompt shows it, or `""`.

        After a resume there is no `StintReport` object to render, only the
        text the snapshot carried, so both sources answer here.
        """
        if self.reports:
            return self.reports[-1].to_text()
        return self._restored_report_text

    def to_payload(self) -> dict[str, Any]:
        """What a resumed planner needs to carry on (docs/14 section 3).

        No message history: a drained planner's turn is over, and the next turn
        after a wake starts from the journal with the history dropped anyway.
        """
        return {
            "turn": self.turn,
            "last_thought": self.last_thought,
            "last_report_text": self.last_report_text(),
            "journal_sections": dict(self.journal_sections),
            "history_reset_reason": self._history_reset_reason,
            "day_log": self.day_log.to_payload(),
        }

    def load_payload(self, payload: Mapping[str, Any]) -> None:
        """Continue from what `to_payload` recorded."""
        self.turn = int(payload["turn"])
        self.last_thought = str(payload["last_thought"])
        self.reports = []
        self._restored_report_text = str(payload["last_report_text"])
        self.journal_sections = {
            str(name): str(body) for name, body in payload["journal_sections"].items()
        }
        self._history_reset_reason = str(payload["history_reset_reason"])
        self.day_log.load_payload(payload["day_log"])
        self.history = []

    async def build_prompt(self) -> str:
        """The user message for the next planner turn.

        It waits first for a journal rewrite that is still running, so a turn
        that follows a sleep reads the journal that sleep produced.
        """
        await self.bridge.await_journal()
        model = self.bridge.model
        parts = [f"Your name is {self.entity_id}."]
        # A wolf on top of the actor goes first: the look below is long.
        alert = threat_alert(model, alert_window_start(model.tick, 0))
        if alert:
            parts.append(alert)
        # The same body lines every tool result carries, so a turn opens on
        # them rather than on a `look` the model has to read first.
        parts.extend(body_alerts(model))
        parts.append(describe_world(model))
        last_report = self.last_report_text()
        if last_report:
            parts.append("Most recent stint:\n" + last_report)
        parts.extend(self.bridge.drain_notes(for_prompt=True))
        parts.append(self.bridge.reflex.prompt_line())
        # Read once: the prompt gets the rendered journal, the trace its
        # sections, and the file is not worth two reads.
        journal = Journal.load(self.memory_path, self.entity_id)
        self.journal_sections = journal.all_sections()
        parts.append("Your journal:\n" + journal.render().strip())
        parts.append(
            "Decide what to do next. Use start_stint for anything that takes "
            "more than one tick. Actions only happen through tool calls; text "
            "that merely describes a call does nothing. Finish with one short "
            "paragraph of reflection."
        )
        return "\n\n".join(parts)

    async def run(self) -> None:
        """Take planner turns forever; never let one bad turn stop the agent."""
        while True:
            try:
                await self.take_turn()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the planner must keep going
                logger.warning("planner_turn_failed", error=str(error))
                self._trace("turn_failed", error=str(error))
                await asyncio.sleep(TURN_RETRY_SECONDS)

    def _trace(self, event: str, **fields: object) -> None:
        """Write one line to `planner.jsonl.gz`, stamped with the current tick."""
        payload: dict[str, object] = {
            "event": event,
            "entity_id": self.entity_id,
            "tick": self.bridge.model.tick,
            "turn": self.turn,
        }
        payload.update(fields)
        self.trace.planner.write(payload)

    async def _log_events(self, events: AsyncIterable[AgentStreamEvent]) -> None:
        """Log every tool call and result so a live run can be followed."""
        async for event in events:
            if isinstance(event, FunctionToolCallEvent):
                self._tool_calls_this_turn += 1
                logger.info(
                    "planner_tool_call",
                    entity_id=self.entity_id,
                    tool=event.part.tool_name,
                    args=event.part.args_as_json_str()[:300],
                )
                self._trace(
                    "tool_call",
                    tool=event.part.tool_name,
                    args=_tool_args(event.part),
                )
            elif isinstance(event, FunctionToolResultEvent):
                tool_name = event.part.tool_name or ""
                logger.info(
                    "planner_tool_result",
                    entity_id=self.entity_id,
                    tool=tool_name,
                    result=str(event.part.content)[:200],
                )
                self._trace(
                    "tool_result", tool=tool_name, result=str(event.part.content)
                )

    async def take_turn(self) -> str:
        """Run one planner turn and publish its reflection."""
        # No turn while the body is asleep, collapsed or dead: the wait ends on
        # the tick it is awake and alive, and the wake or respawn it lived
        # through is what resets the history below.
        await self.bridge.await_active()
        logger.info("planner_turn_started", entity_id=self.entity_id)
        self.turn += 1
        # A wake or respawn that landed between turns is stale before this one
        # starts; one inside the turn is handled at its end.
        self._reset_history_after_a_new_life()
        self._tool_calls_this_turn = 0
        self.deps.budget.reset(toolset.MAX_TOOL_CALLS_PER_TURN, self.bridge.model.tick)
        prompt = await self.build_prompt()
        self._trace("turn_start", prompt=prompt, journal=self.journal_sections)
        started = time.monotonic()
        hard_limit = toolset.MAX_TOOL_CALLS_PER_TURN + toolset.HARD_LIMIT_MARGIN
        with capture_run_messages() as run_messages:
            try:
                result = await self.agent.run(
                    prompt,
                    deps=self.deps,
                    message_history=self.history,
                    usage_limits=UsageLimits(tool_calls_limit=hard_limit),
                    event_stream_handler=lambda _ctx, events: self._log_events(events),
                )
            except UsageLimitExceeded:
                text = self._end_turn_at_hard_limit(run_messages)
                self._reset_history_after_a_new_life()
                return text
        if self.deps.budget.left == 0:
            self._trace("tool_budget_spent", tool_calls=self._tool_calls_this_turn)
        self.history = trim_history(result.all_messages())
        self.last_thought = result.output.strip()
        if self.last_thought:
            self.bridge.set_thought(self.last_thought)
        logger.info("planner_thought", entity_id=self.entity_id, text=self.last_thought)
        usage = usage_from_messages(result.new_messages())
        self.ledger.add_planner(usage)
        self._trace(
            "turn_end",
            thought=self.last_thought,
            tool_calls=self._tool_calls_this_turn,
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
        )
        if self.last_thought:
            self.day_log.add(self.bridge.model.tick, KIND_REFLECTION, self.last_thought)
        self._reset_history_after_a_new_life()
        await self._recover_from_text_only_turn()
        return self.last_thought

    def _end_turn_at_hard_limit(self, run_messages: Sequence[ModelMessage]) -> str:
        """The model kept calling tools after the budget refusals: stop the turn.

        What it did still happened in the world, so the messages are kept;
        pydantic-ai repairs the unanswered tool calls at the end of the history
        on the next run.
        """
        logger.info("planner_tool_budget_reached", entity_id=self.entity_id)
        # The turn is ending before `self.history` is replaced, so its current
        # length is still what went into the run: everything past it is what
        # this turn added, and what this turn's requests cost.
        added = run_messages[len(self.history) :]
        usage = usage_from_messages(added)
        self.ledger.add_planner(usage)
        self._trace(
            "tool_budget_reached",
            tool_calls=self._tool_calls_this_turn,
            usage=usage,
        )
        self.history = trim_history(run_messages)
        self.last_thought = "Ran out of tool calls this turn; continuing."
        self.bridge.set_thought(self.last_thought)
        return self.last_thought

    def note_life_event(self, reason: str) -> None:
        """Record that the body woke or respawned; the turn's history is stale.

        Args:
            reason: "woke" or "respawned".
        """
        self._history_reset_reason = reason

    def end_turn_now(self) -> None:
        """Spend the tool budget, so the model writes its reflection and stops."""
        self.deps.budget.spend()

    def _reset_history_after_a_new_life(self) -> None:
        """Drop the history when the turn just lived through a sleep or a death.

        The journal has been rewritten from this turn's day log, so keeping the
        messages would show the model both, and the older one at greater
        length (docs/12_sleep_journal.md).
        """
        reason = self._history_reset_reason
        if not reason:
            return
        self._history_reset_reason = ""
        self.history = []
        logger.info("planner_history_reset", entity_id=self.entity_id, reason=reason)
        self._trace("history_reset", reason=reason)

    async def _recover_from_text_only_turn(self) -> None:
        """Break the loop where the model narrates tool calls instead of making them.

        A turn with no tool calls does nothing in the world. Small models
        sometimes drift into writing `start_stint(...)` as prose; once that is
        in the history they repeat it forever. After two such turns the
        history is dropped so the next turn starts clean.
        """
        if self._tool_calls_this_turn > 0:
            self._turns_without_tools = 0
            return
        self._turns_without_tools += 1
        logger.warning(
            "planner_turn_without_tools",
            entity_id=self.entity_id,
            streak=self._turns_without_tools,
        )
        if self._turns_without_tools >= 2:
            logger.warning("planner_history_reset", entity_id=self.entity_id)
            self._trace("history_reset", reason="text_only")
            self.history = []
            self._turns_without_tools = 0
        await asyncio.sleep(TURN_RETRY_SECONDS)
