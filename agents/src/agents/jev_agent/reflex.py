"""The reflex brief: a stint the planner pre-registers and code fires.

A planner turn lasts hundreds of ticks, so a settler whose planner is thinking
(or talking) cannot answer a wolf that walks up to it: by the time the model
has read the alert and written a brief, the bites have landed. The planner
therefore writes one brief in advance, and this module decides when it runs.

Contract: docs/09_conversation_and_reflex.md section 4.2. Nothing here says
what a good reflex is; the planner writes the instruction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import structlog

from .geometry import Coord
from .stint import Brief
from .worldmodel import TickDigest, WorldModel

logger = structlog.get_logger(__name__)

# After a reflex stint ends, the wolf-distance trigger is ignored for this
# long, so one wolf standing nearby cannot restart the stint every tick.
# Damage ignores the cooldown: being bitten is news whatever just happened.
REFLEX_COOLDOWN_TICKS = 10
# The reflex stint ends when no living wolf has been in view for this many
# consecutive ticks.
REFLEX_CLEAR_TICKS = 3

MIN_TRIGGER_DISTANCE = 1
MAX_TRIGGER_DISTANCE = 8
# How far the end condition looks for wolves; the observation window is 8.
REFLEX_VIEW_RADIUS = 8

TRIGGER_WOLF_NEAR = "wolf_near"
TRIGGER_DAMAGE = "damage"

END_THREAT_GONE = "threat_gone"

# Stint end reasons produced when a reflex pre-empts something else.
END_REFLEX = "reflex"

INTERRUPTED_PLANNING = "planning"
INTERRUPTED_CONVERSATION = "conversation"
INTERRUPTED_DRIVER_STINT = "driver_stint"

NO_REFLEX_LINE = "You have no reflex brief."


@dataclass(frozen=True)
class ReflexBrief:
    """The brief a reflex stint runs, and the distance that starts it.

    The empty brief (`EMPTY_REFLEX`) is the "no reflex registered" value:
    `registered` is False and nothing ever fires.
    """

    instruction: str
    success_condition: str
    max_ticks: int
    trigger_distance: int
    notes: str = ""
    shouts: tuple[str, ...] = ()
    # Named destinations Jev may step toward while the reflex runs.
    places: Mapping[str, Coord] = field(default_factory=dict)

    @property
    def registered(self) -> bool:
        """Whether this is a real brief rather than the empty sentinel."""
        return bool(self.instruction)

    def to_brief(self) -> Brief:
        """The ordinary `Brief` the stint machinery runs."""
        return Brief(
            instruction=self.instruction,
            success_condition=self.success_condition,
            max_ticks=self.max_ticks,
            notes=self.notes,
            shouts=self.shouts,
            places=self.places,
        )

    def prompt_line(self) -> str:
        """The line shown in every planner turn prompt."""
        if not self.registered:
            return NO_REFLEX_LINE
        parts = [
            f"Your reflex brief: {self.instruction}",
            f"until: {self.success_condition}",
            f"max_ticks {self.max_ticks}",
            f"trigger_distance {self.trigger_distance}",
        ]
        if self.notes:
            parts.append(f"notes: {self.notes}")
        if self.shouts:
            parts.append("shouts: " + "; ".join(self.shouts))
        if self.places:
            parts.append(
                "places: "
                + "; ".join(
                    f"{name} ({target[0]}, {target[1]})"
                    for name, target in self.places.items()
                )
            )
        return " | ".join(parts)

    def as_payload(self) -> dict[str, Any]:
        """JSON form, for `reflex.json` and the trace."""
        return {
            "instruction": self.instruction,
            "success_condition": self.success_condition,
            "max_ticks": self.max_ticks,
            "trigger_distance": self.trigger_distance,
            "notes": self.notes,
            "shouts": list(self.shouts),
            "places": {
                name: [target[0], target[1]] for name, target in self.places.items()
            },
        }


EMPTY_REFLEX = ReflexBrief(
    instruction="", success_condition="", max_ticks=0, trigger_distance=0
)


def clamp_trigger_distance(distance: int) -> int:
    """Pull a requested trigger distance into the legal 1..8 range."""
    return max(MIN_TRIGGER_DISTANCE, min(MAX_TRIGGER_DISTANCE, distance))


def reflex_from_payload(payload: Mapping[str, Any]) -> ReflexBrief:
    """Rebuild a brief from its JSON form; a bad record yields the empty brief."""
    instruction = str(payload.get("instruction", ""))
    if not instruction:
        return EMPTY_REFLEX
    shouts = payload.get("shouts", [])
    return ReflexBrief(
        instruction=instruction,
        success_condition=str(payload.get("success_condition", "")),
        max_ticks=int(payload.get("max_ticks", 1) or 1),
        trigger_distance=clamp_trigger_distance(
            int(payload.get("trigger_distance", MIN_TRIGGER_DISTANCE) or 1)
        ),
        notes=str(payload.get("notes", "")),
        shouts=(
            tuple(str(phrase) for phrase in shouts) if isinstance(shouts, list) else ()
        ),
        places=_places_from_payload(payload.get("places", {})),
    )


def _places_from_payload(raw: Any) -> dict[str, Coord]:
    """Named places from `reflex.json`; a file written before they existed has none."""
    if not isinstance(raw, dict):
        return {}
    places: dict[str, Coord] = {}
    for name, position in raw.items():
        if isinstance(position, (list, tuple)) and len(position) == 2:
            places[str(name)] = (int(position[0]), int(position[1]))
    return places


class ReflexStore:
    """`reflex.json` in the agent's trace directory: load once, save on change.

    A file that cannot be read or written complains and is otherwise ignored;
    losing the reflex must never take the actor down.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ReflexBrief:
        """The saved brief, or the empty brief when there is none."""
        if not self.path.exists():
            return EMPTY_REFLEX
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as error:
            logger.warning("reflex_read_failed", path=str(self.path), error=str(error))
            return EMPTY_REFLEX
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            logger.warning("reflex_parse_failed", path=str(self.path), error=str(error))
            return EMPTY_REFLEX
        if not isinstance(payload, dict):
            return EMPTY_REFLEX
        return reflex_from_payload(payload)

    def save(self, brief: ReflexBrief) -> None:
        """Write the brief, or delete the file when the brief is empty."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not brief.registered:
                self.path.unlink(missing_ok=True)
                return
            self.path.write_text(
                json.dumps(brief.as_payload(), indent=2), encoding="utf-8"
            )
        except OSError as error:
            logger.warning("reflex_write_failed", path=str(self.path), error=str(error))


class ReflexWatch:
    """Decides when the reflex fires and when its stint has done its job."""

    def __init__(self, brief: ReflexBrief = EMPTY_REFLEX) -> None:
        self.brief = brief
        # -1 means "never ran", so the cooldown is over before the first tick.
        self.last_end_tick = -1
        self._clear_ticks = 0

    def set_brief(self, brief: ReflexBrief) -> None:
        """Replace the registered brief; the cooldown is unaffected."""
        self.brief = brief

    def trigger(self, model: WorldModel, digest: TickDigest) -> str:
        """`wolf_near`, `damage`, or `""` when the reflex should not fire now."""
        if not self.brief.registered or not model.self_info.alive:
            return ""
        if digest.attackers:
            return TRIGGER_DAMAGE
        if self._cooling_down(model.tick):
            return ""
        if model.wolves_near(self.brief.trigger_distance):
            return TRIGGER_WOLF_NEAR
        return ""

    def _cooling_down(self, tick: int) -> bool:
        if self.last_end_tick < 0:
            return False
        return tick - self.last_end_tick < REFLEX_COOLDOWN_TICKS

    def begin(self) -> None:
        """Reset the end condition's counter as a reflex stint starts."""
        self._clear_ticks = 0

    def end_reason(self, model: WorldModel) -> str:
        """`threat_gone` once no wolf has been in view for enough ticks."""
        if model.wolves_near(REFLEX_VIEW_RADIUS):
            self._clear_ticks = 0
            return ""
        self._clear_ticks += 1
        if self._clear_ticks >= REFLEX_CLEAR_TICKS:
            return END_THREAT_GONE
        return ""

    def note_end(self, tick: int) -> None:
        """Start the cooldown after a reflex stint has ended."""
        self.last_end_tick = tick
        self._clear_ticks = 0


def reflex_report_line(
    start_tick: int,
    end_tick: int,
    end_reason: str,
    health_before: int,
    health_after: int,
) -> str:
    """The one-line report the planner sees after a reflex stint has run."""
    return (
        f"[reflex ran ticks {start_tick}-{end_tick}: ended because {end_reason}; "
        f"health {health_before} -> {health_after}]"
    )
