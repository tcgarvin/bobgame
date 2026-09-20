"""What a planner tool hands the tick loop, and what the loop hands back.

Each of these is a promise the tick loop settles: a stint request, a single
action, a seat to wait out, or a report held back until the thing that
interrupted it is over.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, TypeVar

from ... import world_pb2 as pb
from ..briefs import Brief, StintDriver
from ..conversation import ConversationReport
from ..outcomes import ActionOutcome
from ..stint import StintReport, never_ends
from ..worldmodel import WorldModel

_T = TypeVar("_T")

ASLEEP_REJECTION = "failed: asleep"


@dataclass
class _StintRequest:
    """A planner `start_stint` waiting for the tick loop to pick it up."""

    brief: Brief
    future: asyncio.Future[StintReport]
    driver: StintDriver | None = None
    # An extra end rule the caller owns, asked before every tick's action;
    # `travel_to` uses it to end the stint the tick the body arrives.
    end_check: Callable[[WorldModel], str] = never_ends


@dataclass
class _DirectRequest:
    """A planner single-tick action waiting to be submitted and resolved."""

    intent: pb.Intent
    description: str
    future: asyncio.Future[ActionOutcome]
    remaining_ticks: int = 1


@dataclass
class _ConversationWaiter:
    """A planner tool parked until the conversation it started has ended."""

    future: asyncio.Future[ConversationReport | None]
    deadline_tick: int


@dataclass
class _HeldStint:
    """A stint report kept back until the thing that interrupted it is over."""

    request: _StintRequest
    report: StintReport


def _drain(queue: "asyncio.Queue[_T]") -> list[_T]:
    items: list[_T] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items
