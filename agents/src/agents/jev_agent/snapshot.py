"""One settler's state, written at a save tick and read back on a resume.

Contract: [docs/14_new_moon_and_saves.md](../../../../docs/14_new_moon_and_saves.md),
"Agent snapshot contents". A snapshot is taken only while the settler is
*drained* (`JevAgent.drained`): asleep or dead, with no stint, conversation,
journal rewrite or queued request outstanding. That is what makes the file a
whole truth rather than a photograph of something half-done, and it is why
nothing here has to serialise a running stint, a pending future or the
planner's message history.

Every component serialises itself (`WorldModel.to_payload`,
`Planner.to_payload`, `CostLedger.to_payload`, `ReflexWatch.to_payload`,
`DayLog.to_payload`); this module only assembles, versions and stores the
result. `memory.md` and `reflex.json` are copied by `dev.sh`, not carried here.
"""

from __future__ import annotations

import gzip
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    from .agent import JevAgent

# Bumped whenever the shape below changes in a way an older file cannot
# satisfy. A file written under another version is refused, never guessed at.
SNAPSHOT_FORMAT_VERSION = 1

# The suffix a snapshot is written under before it is renamed into place, so a
# reader never sees a half-written file.
PARTIAL_SUFFIX = ".partial"


class SnapshotError(RuntimeError):
    """A snapshot file could not be read as this agent's state."""


class SleepSnapshot(BaseModel):
    """The tick loop's sleep bookkeeping (`JevAgent._note_sleep_transitions`)."""

    # -1 means awake; otherwise the tick the current sleep began.
    asleep_since: int = -1
    fatigue_before: int = 0
    where: str = ""
    # The last completed sleep, and whether there has been one at all.
    last_sleep_recorded: bool = False
    last_start_tick: int = 0
    last_end_tick: int = 0
    last_reason: str = ""
    last_fatigue_before: int = 0
    last_fatigue_after: int = 0
    last_where: str = ""
    last_food_after: int = 0


class AgentSnapshot(BaseModel):
    """Everything one drained settler needs to carry on in another process."""

    format_version: int = SNAPSHOT_FORMAT_VERSION
    entity_id: str
    tick: int
    world_model: dict[str, Any]
    planner: dict[str, Any]
    cost_ledger: dict[str, Any]
    reflex_watch: dict[str, Any]
    sleep: SleepSnapshot
    notes_for_tools: list[str]
    notes_for_prompt: list[str]


def capture(agent: "JevAgent") -> AgentSnapshot:
    """Build the snapshot of a drained agent.

    The caller checks `agent.drained()` first: this function does not, because
    a refusal belongs where the save is decided, not where it is written.
    """
    return AgentSnapshot(
        entity_id=agent.entity_id,
        tick=agent.model.tick,
        world_model=agent.model.to_payload(),
        planner=agent.planner.to_payload(),
        cost_ledger=agent.ledger.to_payload(),
        reflex_watch=agent.reflex_watch.to_payload(),
        sleep=agent.sleep_snapshot(),
        # Read, not drained: the run carries on after the save, and the notes
        # are still owed to the planner in this process too.
        notes_for_tools=agent.pending_notes(),
        notes_for_prompt=agent.pending_notes(for_prompt=True),
    )


def restore(agent: "JevAgent", snapshot: AgentSnapshot) -> None:
    """Put a snapshot back into a freshly constructed agent."""
    if snapshot.entity_id != agent.entity_id:
        raise SnapshotError(
            f"snapshot is for {snapshot.entity_id!r}, not {agent.entity_id!r}"
        )
    agent.load_snapshot(snapshot)


def write_snapshot(path: Path, snapshot: AgentSnapshot) -> None:
    """Write `snapshot` to `path` as gzip JSON, through a `.partial` rename.

    The world watches the directory for the finished name, so the file must
    never appear before it is complete.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    with gzip.open(partial, "wt", encoding="utf-8") as handle:
        handle.write(snapshot.model_dump_json())
    os.replace(partial, path)


def read_snapshot(path: Path, entity_id: str = "") -> AgentSnapshot:
    """Read a snapshot back, refusing anything that is not this agent's.

    Args:
        path: the `.json.gz` file to read.
        entity_id: the settler it must belong to; `""` accepts any.

    Raises:
        SnapshotError: the file is unreadable, malformed, written under a
            different format version, or belongs to another settler.
    """
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as error:
        raise SnapshotError(f"cannot read snapshot {path}: {error}") from error
    try:
        snapshot = AgentSnapshot.model_validate_json(raw)
    except ValidationError as error:
        raise SnapshotError(f"snapshot {path} is not readable: {error}") from error
    if snapshot.format_version != SNAPSHOT_FORMAT_VERSION:
        raise SnapshotError(
            f"snapshot {path} is format version {snapshot.format_version}, "
            f"this agent reads {SNAPSHOT_FORMAT_VERSION}"
        )
    if entity_id and snapshot.entity_id != entity_id:
        raise SnapshotError(
            f"snapshot {path} is for {snapshot.entity_id!r}, not {entity_id!r}"
        )
    return snapshot


def snapshot_path(save_directory: Path, entity_id: str) -> Path:
    """`<save dir>/agents/<entity_id>.json.gz`, the one name the world waits for."""
    return save_directory / "agents" / f"{entity_id}.json.gz"
