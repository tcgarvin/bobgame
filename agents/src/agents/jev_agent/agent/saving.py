"""Where a snapshot goes, how long the save waits, and what "drained" means.

Deciding whether this settler is drained is `core.JevAgent`'s: it is a reading
of every queue, stint, seat and background task the agent owns.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# --- saving (docs/14_new_moon_and_saves.md section 3) ------------------------

# Where a run's saves live, under the run directory the world made.
SAVES_DIR_NAME = "saves"
# How long to wait for the settler to drain before giving up on the save. The
# world waits `save_wait_seconds` (180 by default) for the file, so this has to
# be a little under that: an agent that gives up first can say why in its log,
# where an agent the world gave up on cannot.
SAVE_WAIT_ENV = "BOBGAME_SAVE_WAIT_SECONDS"
DEFAULT_SAVE_WAIT_SECONDS = 170.0
SAVE_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class DrainState:
    """Whether the settler is drained, and if not, what is still outstanding.

    Drained is the precondition for a snapshot (docs/14 section 2): everything
    the agent was doing has finished, so there is nothing left that a file
    could only half describe.
    """

    drained: bool
    reason: str = ""

    @classmethod
    def busy(cls, reason: str) -> "DrainState":
        """Not drained, because of `reason`."""
        return cls(drained=False, reason=reason)


DRAINED = DrainState(drained=True)


def save_wait_seconds() -> float:
    """How long to wait for the settler to drain, from the environment.

    `BOBGAME_SAVE_WAIT_SECONDS` is what the world was configured with, minus
    its own margin; a value that is not a positive number is ignored and said
    so, rather than silently turning the wait off.
    """
    raw = os.environ.get(SAVE_WAIT_ENV, "")
    if not raw:
        return DEFAULT_SAVE_WAIT_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        logger.warning("save_wait_not_a_number", value=raw)
        return DEFAULT_SAVE_WAIT_SECONDS
    if seconds <= 0:
        logger.warning("save_wait_not_positive", value=raw)
        return DEFAULT_SAVE_WAIT_SECONDS
    return seconds
