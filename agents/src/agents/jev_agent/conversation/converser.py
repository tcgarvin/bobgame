"""The converser: the language-model half of conversation mode.

Two pydantic-ai agents on the planner's model, neither with any tool and
neither able to touch the world. One picks this turn's move, the other writes
the note kept once the conversation ends.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from pydantic_ai import Agent

from .. import items
from ..llm import planner_model_settings, resolve_model_name
from ..pricing import CostLedger, usage_from_messages
from .protocol import (
    ACTION_PASS,
    CallResult,
    ClosingNote,
    Converser,
    ConverserMove,
    MoveCall,
    NoteCall,
)

logger = structlog.get_logger(__name__)


def converser_narrative(
    settler_count: int = items.DEFAULT_SETTLER_COUNT,
) -> str:
    """The converser's system prompt for a scenario with this many settlers."""
    return f"""\
You are one of {items.settler_count_word(settler_count)} people who woke up together on a large, wild island with
nothing but your hands. The others are real agents like you. Together, build a
civilization that lasts: a shelter of your own each, and food, safety and rest
you can count on tomorrow. You are in a
conversation with some of them right now, and this is your turn to act in it.

How a conversation works:
- This is the settlers' one channel where the others answer back: everything
  else (`shout`, a board, a sign) only ever goes one way.
- A conversation sits on an anchor tile. Up to {items.CONVERSATION_MAX_PARTICIPANTS} settlers stand on tiles next to
  it and take turns in the order they joined. Anyone within {items.SAY_RADIUS} tiles hears what
  is said, whether or not they are in it.
- While you are in the conversation your body stays on its tile and the moves
  below are the only things you can do. Walking, gathering, crafting, placing
  and fighting only happen after you `leave` or the conversation closes.
  The world keeps ticking while you talk: food drops 1 every 4 ticks and at
  food 0 you lose health.
- On your turn you may `speak` (one line of at most {items.CONVERSATION_TEXT_LIMIT} characters), `pass`,
  `leave`, `give` items to someone in the conversation or standing next to
  you, or `eat` one item from your pack (a berry restores 20 food). Speaking
  or passing ends your turn; giving and eating do not, so afterwards you are
  asked again on the next tick.
- A turn you do not use within {items.CONVERSATION_TURN_TICKS} ticks counts as a pass. The conversation
  closes when fewer than two settlers are left in it, when everyone passes in
  one full round, or after {items.CONVERSATION_MAX_UTTERANCES} lines.
- A `give` needs the item in your pack, the exact item name, and an amount.
- You may be in this conversation because someone walked up and addressed you
  rather than because you asked for one. Their line is the first one in the
  transcript and the turn is yours.

Answer with one move: the action, the text if you are speaking, the target,
item kind and amount if you are giving, and the item kind if you are eating.
"""


# The default-sized scenario's prompt, for tests and for anything that reads
# the narrative without building an agent.
CONVERSER_NARRATIVE = converser_narrative()

NOTE_INSTRUCTION = (
    "The conversation is over. Answer with two short fields, either of which "
    "may be empty: what was agreed or learned in the conversation, and "
    "separately what you yourself said you would do. No preamble, just the "
    "two fields."
)


async def run_converser(
    converser: Converser, prompt: str, timeout: float
) -> CallResult:
    """Ask the converser for a move and time the call from inside.

    Timing here rather than at collection time is the point: the tick loop may
    only look at the task several ticks later, so a latency measured then would
    be the wait, not the call. A failed call becomes a `pass` with the error
    recorded, because one bad answer must not end the conversation.
    """
    started = time.monotonic()
    try:
        call = await asyncio.wait_for(converser.move(prompt), timeout)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - a failed converser is not fatal
        elapsed = int((time.monotonic() - started) * 1000)
        logger.warning("converser_call_failed", error=str(error))
        return CallResult(ConverserMove(action=ACTION_PASS), elapsed, str(error))
    return CallResult(
        call.move, int((time.monotonic() - started) * 1000), usage=call.usage
    )


class ModelConverser:
    """A `Converser` backed by two pydantic-ai agents on the planner's model.

    One has the structured `ConverserMove` output and no tools; the other
    writes the note kept after the conversation. Neither has any tool and
    neither can touch the world.
    """

    def __init__(
        self,
        model_name: str = "",
        ledger: CostLedger = CostLedger(),
        settler_count: int = items.DEFAULT_SETTLER_COUNT,
    ) -> None:
        self.model_name = resolve_model_name(model_name)
        # As in `Planner`: the agent passes its own ledger, the default is a
        # sink for tests.
        self.ledger = ledger
        self.settler_count = settler_count
        narrative = converser_narrative(settler_count)
        settings = planner_model_settings(self.model_name)
        self.move_agent: Agent[None, ConverserMove] = Agent(
            self.model_name,
            output_type=ConverserMove,
            system_prompt=narrative,
            model_settings=settings,
            retries=2,
        )
        self.note_agent: Agent[None, ClosingNote] = Agent(
            self.model_name,
            output_type=ClosingNote,
            system_prompt=narrative,
            model_settings=settings,
            retries=1,
        )

    async def move(self, prompt: str) -> MoveCall:
        """Ask the model for one move."""
        result = await self.move_agent.run(prompt)
        usage = usage_from_messages(result.new_messages())
        self.ledger.add_converser(usage)
        return MoveCall(result.output, usage)

    async def note(self, prompt: str) -> NoteCall:
        """Ask the model what to keep from the conversation."""
        result = await self.note_agent.run(prompt)
        usage = usage_from_messages(result.new_messages())
        self.ledger.add_converser(usage)
        output = result.output
        return NoteCall(
            output.agreed_or_learned.strip(), output.you_said_you_would.strip(), usage
        )
