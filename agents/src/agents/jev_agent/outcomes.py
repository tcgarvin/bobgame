"""What the world made of one submitted intent, as data rather than a sentence.

The tick loop folds the world's own `entity_acted` event into an
`ActionOutcome` exactly once, at the point the event arrives. Everything
downstream — the planner's tools, the craft chain — reads its fields. Before
this, every caller re-read the rendered sentence: one asked whether `" ok:"`
was in it, one ran a regular expression over it for a sign id, one searched it
for `"say ok: heard: "` and split the tail on commas.

The rendering is still exactly what the model is shown, and `text()` is the one
place that builds it.

The world's detail strings are the other half. They are the world's wording,
not ours, so each shape is parsed in exactly one function here and nowhere
else. This module has no dependencies at all, inside the package or out, which
is what lets both `worldmodel.py` and the planner use it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The world's wording for a finished craft, in the action event's details.
CRAFTED_DETAIL = "crafted "

# What a result says when the intent went out but no event came back for it.
SUBMITTED = "submitted"
# ... and what it says in place of a detail the world left empty.
NO_DETAIL = "(no detail)"

# The prefix every "it never ran" reason shares (`briefs.INTERRUPTED_BY_*`).
INTERRUPTION_PREFIX = "interrupted: "


@dataclass(frozen=True)
class ActionOutcome:
    """The world's answer to one intent this actor submitted.

    Exactly one of three things happened: the action never ran (`not_run` says
    why), it ran and the world reported an event (`action`, `ok`, `detail`), or
    it was submitted and no event came back (neither).
    """

    # What the actor was trying to do, in the words the planner asked for it.
    description: str
    # The world's action type (`place`, `craft`, `converse`, ...), or `""`.
    action: str = ""
    # The world's own detail string for that event.
    detail: str = ""
    ok: bool = False
    # Why the action never reached the world; `""` when it did.
    not_run: str = ""

    @classmethod
    def from_event(
        cls, description: str, action: str, success: bool, detail: str
    ) -> "ActionOutcome":
        """The outcome of an intent the world reported an action event for."""
        return cls(
            description=description, action=action, detail=detail, ok=bool(success)
        )

    @classmethod
    def never_ran(cls, description: str, reason: str) -> "ActionOutcome":
        """An action nothing carried out: an interruption, or a stopping agent."""
        return cls(description=description, not_run=reason)

    @classmethod
    def submitted_only(cls, description: str) -> "ActionOutcome":
        """The intent went out and the world reported no event for it."""
        return cls(description=description)

    @property
    def interrupted(self) -> bool:
        """Whether something else took the body before the action could run."""
        return self.not_run.startswith(INTERRUPTION_PREFIX)

    @property
    def placed_object_id(self) -> str:
        """The object a successful `place` created, or `""`."""
        return placed_object_id(self.detail) if self.ok else ""

    @property
    def heard(self) -> tuple[str, ...]:
        """Who heard a successful `say`, in the order the world listed them."""
        return heard_ids(self.detail) if self.ok else ()

    @property
    def given(self) -> "Given":
        """What a successful `give` handed over."""
        return parse_gave(self.detail) if self.ok else Given()

    def tail(self) -> str:
        """Everything after the arrow in `text()`."""
        if self.not_run:
            return self.not_run
        if not self.action:
            return SUBMITTED
        status = "ok" if self.ok else "failed"
        return f"{self.action} {status}: {self.detail or NO_DETAIL}"

    def text(self) -> str:
        """The one rendering, which is what the planner model is shown."""
        return f"{self.description} -> {self.tail()}"

    def __str__(self) -> str:
        return self.text()


# --------------------------------------------------------------------------
# The world's detail strings, one parser per shape
# --------------------------------------------------------------------------

# `containers.py`: `placed {object_id} at {position}`.
_PLACED_RE = re.compile(r"^placed (\S+) at\b")

# `speech.py`: `heard: {', '.join(hearer_ids)}` (and no `heard:` at all on a
# channel nobody was in earshot of).
_HEARD_PREFIX = "heard:"

# `containers.py`: `gave {amount} {kind} to {entity_id}`.
_GAVE_RE = re.compile(r"^gave (?P<amount>\d+) (?P<kind>\S+) to (?P<target>\S+)$")


def placed_object_id(detail: str) -> str:
    """The id in `"placed sign_12 at (3, 4)"`, or `""` for any other detail."""
    match = _PLACED_RE.match(detail.strip())
    return match.group(1) if match else ""


def heard_ids(detail: str) -> tuple[str, ...]:
    """The entity ids in `"heard: ada, bram"`; empty when nobody was in earshot."""
    text = detail.strip()
    if not text.startswith(_HEARD_PREFIX):
        return ()
    tail = text[len(_HEARD_PREFIX) :]
    return tuple(part.strip() for part in tail.split(",") if part.strip())


@dataclass(frozen=True)
class Given:
    """What one `give` handed over; `kind` is empty when nothing was parsed."""

    amount: int = 0
    kind: str = ""
    target: str = ""

    @property
    def happened(self) -> bool:
        """Whether this is a real handover rather than the empty answer."""
        return bool(self.kind)


def parse_gave(detail: str) -> Given:
    """`"gave 3 stone to mira"` as data; an empty `Given` for anything else."""
    match = _GAVE_RE.match(detail.strip())
    if match is None:
        return Given()
    return Given(
        amount=int(match.group("amount")),
        kind=match.group("kind"),
        target=match.group("target"),
    )


@dataclass(frozen=True)
class CraftProgress:
    """Craft actions one settler has banked at one station."""

    recipe: str = ""
    actions: int = 0


def parse_craft_progress(raw: str) -> CraftProgress:
    """`"bed:2"` as data; the empty answer when the state value is not that."""
    recipe, _, done = raw.partition(":")
    if not recipe or not done.isdigit():
        return CraftProgress()
    return CraftProgress(recipe=recipe, actions=int(done))


@dataclass(frozen=True)
class Seat:
    """A seat a `converse` action event reports this actor taking.

    The world writes `open conv_12`, `join conv_12`, `hail conv_12 mira` and
    `hailed conv_12 ivo`. Which of those count as a seat is the caller's
    business (`conversation.JOIN_ACTIONS`); this only splits the detail.
    """

    action: str = ""
    conversation_id: str = ""
    target: str = ""


def parse_seat(detail: str) -> Seat:
    """A `converse` detail as data; the empty `Seat` when it names no id."""
    parts = detail.split()
    if len(parts) < 2:
        return Seat()
    return Seat(
        action=parts[0],
        conversation_id=parts[1],
        target=parts[2] if len(parts) >= 3 else "",
    )
