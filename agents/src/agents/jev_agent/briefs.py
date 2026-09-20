"""The vocabulary the option layer and the stint layer share.

`options.py` enumerates what Jev may choose, `stint.py` runs a brief tick by
tick, `planner.py` writes the briefs and `agent.py` sequences the whole thing.
Each of those needs the same handful of names — an `Option`, a `TravelState`, a
`Brief`, the driver protocol and the strings a tool result carries when
something else took the body — so they live here, below all four, and nothing
here imports any of them.

Everything in this module is frozen data or a protocol: no behaviour that needs
a world model, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .. import world_pb2 as pb
from .geometry import Coord
from .worldmodel import WorldModel

# --------------------------------------------------------------------------
# Walking and options
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TravelState:
    """A code-owned journey: a destination and how to finish it."""

    target: Coord
    label: str
    stop_adjacent: bool = False


@dataclass(frozen=True)
class Option:
    """One choosable action, with the intent it becomes."""

    key: str
    description: str
    intent: pb.Intent
    travel_target: TravelState | None = None
    clears_travel: bool = False


# --------------------------------------------------------------------------
# What a brief may contain
# --------------------------------------------------------------------------

# Hails (docs/09 section 9): addressing a settler next to you starts a
# conversation with the two of you, without either of you having asked first.
# The planner grants them one at a time, in the brief; Jev never invents one.
MAX_BRIEF_HAILS = 3
# How many times the world may refuse one brief hail before the stint stops
# offering it: two refusals are enough to show the reason is not going away.
HAIL_REFUSAL_LIMIT = 2

# World object ids look like `bush_17247`; this is how one is spotted in the
# free text of a brief so the named object always gets a walk option.
OBJECT_ID_PATTERN = re.compile(r"\b[a-z][a-z_]*_\d+\b")

# Place names the planner may attach to a brief (docs/05, "named places").
PLACE_NAME_PATTERN = re.compile(r"^[a-z0-9_]{1,24}$")
MAX_BRIEF_PLACES = 6

EMPTY_PLACES: Mapping[str, Coord] = {}


@dataclass(frozen=True)
class BriefHail:
    """One settler the planner told Jev it may address, and what to say."""

    settler: str
    line: str
    # What the actor wants out of the conversation the hail starts, shown only
    # to it once seated (docs/09 section 10, item 4). Empty when the planner
    # gave none.
    purpose: str = ""

    def as_payload(self) -> dict[str, str]:
        """The pair (and purpose, when given) as the brief payload and trace."""
        payload = {"settler": self.settler, "line": self.line}
        if self.purpose:
            payload["purpose"] = self.purpose
        return payload


@dataclass(frozen=True)
class Brief:
    """What the planner told Jev to do, and the limits it set."""

    instruction: str
    success_condition: str
    max_ticks: int
    notes: str = ""
    check_every: int = 1
    travel: TravelState | None = None
    # The only phrases Jev may shout during this stint; empty means it cannot.
    shouts: tuple[str, ...] = ()
    # The only settlers Jev may hail, each with the line to say; empty means it
    # cannot start a conversation (docs/09 section 9.3).
    hails: tuple[BriefHail, ...] = ()
    # Named destinations Jev may step toward, so it never sees a coordinate.
    places: Mapping[str, Coord] = field(default_factory=dict)

    def summary(self) -> str:
        """One-line form for status reports and the viewer."""
        return f"{self.instruction} (until: {self.success_condition})"

    @property
    def text(self) -> str:
        """Instruction and notes together, for scanning out the ids it names."""
        return f"{self.instruction}\n{self.notes}"

    def as_payload(self) -> dict[str, Any]:
        """The brief as the replay contract serialises it."""
        travel = self.travel
        return {
            "instruction": self.instruction,
            "success_condition": self.success_condition,
            "max_ticks": self.max_ticks,
            "notes": self.notes,
            "check_every": self.check_every,
            "shouts": list(self.shouts),
            "hails": [hail.as_payload() for hail in self.hails],
            "places": {
                name: [target[0], target[1]] for name, target in self.places.items()
            },
            "travel": (
                None
                if travel is None
                else {"target": list(travel.target), "label": travel.label}
            ),
        }


# --------------------------------------------------------------------------
# Code drivers: a stint that runs without Jev
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DriverChoice:
    """What a code driver wants to do on one tick."""

    option: Option
    note: str = ""


class StintDriver(Protocol):
    """Code that replaces Jev for a stint, one deterministic decision per tick.

    Used by the planner's `build` tool: laying out walls and roads is arithmetic,
    not judgement, and Jev cannot reason about coordinates tick by tick.
    """

    name: str

    def stop_reason(self, model: WorldModel) -> str:
        """Why the stint should end now, or `""` to carry on."""

    def choose(self, model: WorldModel) -> DriverChoice:
        """The action for this tick. Only called when `stop_reason` was empty."""

    def summary(self) -> str:
        """A few lines of progress for the planner's report."""


# --------------------------------------------------------------------------
# Interruptions
# --------------------------------------------------------------------------

# What a planner single-tick action is answered with when something else took
# the body before it could run. Nothing happened, so the planner's tool budget
# is not charged for it (`BudgetedToolset.call_tool`).
INTERRUPTED_BY_REFLEX = "interrupted: reflex stint started"
# A conversation can start while the planner is mid-turn, because someone
# hailed this settler (docs/09 sections 8.3 and 9). The conversation owns the
# body from that tick, so single-tick actions are answered with this instead.
INTERRUPTED_BY_CONVERSATION = "interrupted: conversation {conversation_id} started"
_INTERRUPTION_PREFIX = "interrupted: "


def conversation_interruption(conversation_id: str) -> str:
    """The answer a single-tick action gets when a conversation took the body."""
    return INTERRUPTED_BY_CONVERSATION.format(conversation_id=conversation_id)


def was_interrupted(result: str) -> bool:
    """Whether a tool result says the action never ran because of an interruption.

    Both forms are rendered as `"<what> -> interrupted: ..."`, so one substring
    covers the reflex and the conversation alike.
    """
    return f"-> {_INTERRUPTION_PREFIX}" in result
