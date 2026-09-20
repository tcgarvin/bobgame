"""What the planner reads once a conversation has ended."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..worldmodel import TranscriptLine, WorldModel
from .protocol import END_ASLEEP, END_NEW_MOON, SLEEP_END_TEXT


def sleep_end_reason(model: WorldModel) -> str:
    """Why a seat ended when the body fell asleep holding it.

    The world closes every conversation on the tick a new moon puts everyone
    to sleep, so that night has its own reason (docs/14 section 1).
    """
    return END_NEW_MOON if model.clock.new_moon_tonight else END_ASLEEP


@dataclass
class ConversationReport:
    """What the planner reads once the conversation has ended."""

    conversation_id: str
    start_tick: int
    end_tick: int
    participants: tuple[str, ...]
    end_reason: str
    transcript: tuple[TranscriptLine, ...]
    given: tuple[str, ...] = ()
    received: Mapping[str, int] = field(default_factory=dict)
    # What this settler said it would do, and what was agreed or learned
    # (docs/09 section 10, item 5); either may be empty.
    commitment: str = ""
    agreed: str = ""

    def to_text(self) -> str:
        """Render the report for the planner's tool result.

        Leads with the two closing-note fields, before the transcript, so the
        planner sees what to act on without reading the whole exchange.
        """
        lines = [
            f"CONVERSATION REPORT: {self.conversation_id}",
            f"  ticks: {self.start_tick}-{self.end_tick}",
            f"  participants: {', '.join(self.participants) or 'nobody else'}",
            f"  ended because: {self.end_reason}",
        ]
        explanation = SLEEP_END_TEXT.get(self.end_reason, "")
        if explanation:
            lines.append(f"  {explanation}")
        lines += [
            f"  you said you would: {self.commitment or '(none)'}",
            f"  agreed or learned: {self.agreed or '(none)'}",
        ]
        if self.transcript:
            lines.append("  transcript:")
            lines.extend(f"    {line.as_line()}" for line in self.transcript)
        else:
            lines.append("  transcript: nothing was said")
        if self.given:
            lines.append("  you gave: " + "; ".join(self.given))
        if self.received:
            received = ", ".join(
                f"{kind} +{count}" for kind, count in sorted(self.received.items())
            )
            lines.append(f"  items you were given: {received}")
        return "\n".join(lines)
