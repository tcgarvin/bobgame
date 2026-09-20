"""Read-tracking for the two pushed channels: signs and message boards.

A sign's line and a board note announce themselves to whoever walks past, once
per version, and a board note additionally carries a "(new)" marker in `look`
until the actor has read the board. Both are plain bookkeeping over dicts the
model owns, so they live here as functions rather than as model methods: the
state is `{object id: version}` and nothing else.
"""

from __future__ import annotations

from typing import Iterable, Mapping, MutableMapping

from .. import items
from .types import ObjectInfo, sign_note_line


def note_tick(note: Mapping[str, object]) -> int:
    """A note's `tick` field, defensively parsed; 0 for anything odd."""
    raw = note.get("tick", 0)
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def sign_notes_in_view(
    objects: Iterable[ObjectInfo],
    tick: int,
    entity_id: str,
    texts_read: MutableMapping[str, str],
) -> list[str]:
    """One note per sign in view whose line is new to this actor.

    A sign is the one channel nobody has to ask for: walking past a written
    sign delivers its line once, and again whenever the text changes. The
    actor's own signs are recorded as read without a note, so a settler is
    never told what it just wrote itself.
    """
    notes: list[str] = []
    for obj in objects:
        if obj.object_type != items.SIGN or obj.last_seen != tick:
            continue
        text = obj.sign_text
        already = texts_read.get(obj.object_id)
        texts_read[obj.object_id] = text
        if not text or text == already:
            continue
        if obj.sign_author == entity_id:
            continue
        notes.append(sign_note_line(obj))
    return notes


def board_notes_in_view(
    objects: Iterable[ObjectInfo],
    tick: int,
    entity_id: str,
    notified: MutableMapping[str, dict[int, int]],
) -> list[str]:
    """One note per board note by someone else seen for the first time.

    This runs on the same push-notification path as a sign's text
    (docs/08_building.md, "Signs"; docs/09 section 6), and is independent of
    `read_board`/`board_notes_read`, which only drive the `look` "(new)"
    marker: a note announces itself just by coming into view, whether or not it
    has been read.
    """
    notes: list[str] = []
    for obj in objects:
        if obj.object_type != items.MESSAGE_BOARD or obj.last_seen != tick:
            continue
        seen = notified.setdefault(obj.object_id, {})
        for slot, note in obj.notes_by_slot().items():
            title = str(note.get("title", ""))
            author = str(note.get("author", ""))
            if not title or author == entity_id:
                continue
            version = note_tick(note)
            if seen.get(slot) == version:
                continue
            seen[slot] = version
            notes.append(f"[{obj.object_id}: new note by {author}: {title!r}]")
    return notes


def mark_board_read(board: ObjectInfo, read: MutableMapping[int, int]) -> None:
    """Record every current note on `board` as read by this actor."""
    for slot, note in board.notes_by_slot().items():
        if str(note.get("title", "")):
            read[slot] = note_tick(note)


def unread_note_count(board: ObjectInfo, read: Mapping[int, int]) -> int:
    """How many of `board`'s current notes this actor has not read."""
    return sum(
        1
        for slot, note in board.notes_by_slot().items()
        if str(note.get("title", "")) and read.get(slot) != note_tick(note)
    )


def is_note_unread(
    read: Mapping[int, int], slot: int, note: Mapping[str, object]
) -> bool:
    """Whether this actor has not read this exact version of a note."""
    return read.get(slot) != note_tick(note)
