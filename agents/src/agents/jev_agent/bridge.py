"""What the planner needs from the tick loop, as a protocol.

`planner.py` never touches `agent.py`: it talks to an `AgentBridge`, which
`JevAgent` implements. The protocol lives in its own module so the planner's
tools and the tick loop can both name it without either importing the other,
and so a test can hand the planner a small stand-in.
"""

from __future__ import annotations

from typing import Callable, Protocol

from .. import world_pb2 as pb
from .briefs import Brief, StintDriver
from .conversation import ConversationReport
from .reflex import ReflexBrief
from .stint import StintReport, never_ends
from .worldmodel import WorldModel


class AgentBridge(Protocol):
    """What the planner needs from the tick loop."""

    @property
    def model(self) -> WorldModel:
        """The shared world model, updated every tick."""

    async def run_stint(
        self,
        brief: Brief,
        driver: StintDriver | None = None,
        end_check: Callable[[WorldModel], str] = never_ends,
    ) -> StintReport:
        """Hand control to Jev (or to `driver`) until the stint has ended.

        `end_check` is an extra code-owned end rule, asked before every tick.
        """

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        """Submit one intent on the next tick and report what happened."""

    async def wait_ticks(self, ticks: int) -> str:
        """Do nothing for `ticks` ticks."""

    async def await_conversation(self) -> ConversationReport | None:
        """Block until the conversation just opened or joined has ended."""

    async def await_wake(self, since_tick: int) -> str:
        """Block until the actor, asleep since `since_tick`, has woken."""

    async def await_active(self) -> None:
        """Block until the actor is awake and alive."""

    @property
    def reflex(self) -> ReflexBrief:
        """The brief code runs when a wolf is close or the actor is bitten."""

    def set_reflex(self, brief: ReflexBrief) -> None:
        """Register (or replace) the reflex brief."""

    def clear_reflex(self) -> None:
        """Forget the reflex brief."""

    def set_conversation_purpose(self, purpose: str) -> None:
        """Remember why the conversation this actor is about to open or start
        by hailing was started, for the next `open`/`hail` that lands
        (docs/09 section 10, item 4)."""

    async def await_journal(self) -> None:
        """Wait, briefly, for a journal rewrite that is still running.

        The journal is rewritten in the background when the actor falls asleep
        or dies; a turn that started before it finished must read the new file,
        not the old one (docs/12_sleep_journal.md).
        """

    def drain_notes(self, *, for_prompt: bool = False) -> list[str]:
        """Reflex lines and conversation reports not yet shown, emptied as taken."""

    def set_thought(self, thought: str) -> None:
        """Publish the planner's latest reflection to the viewer."""
