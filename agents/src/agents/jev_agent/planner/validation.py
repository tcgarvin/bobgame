"""Checking what the model put in a tool call, and saying what is wrong.

Everything here raises `ModelRetry` on a bad argument, so the model gets one
sentence naming the fact rather than a failed turn.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from pydantic_ai import ModelRetry

from ... import world_pb2 as pb
from .. import items
from ..briefs import (
    MAX_BRIEF_HAILS,
    MAX_BRIEF_PLACES,
    OBJECT_ID_PATTERN,
    PLACE_NAME_PATTERN,
    BriefHail,
)
from ..geometry import NAME_TO_DIRECTION, Coord
from ..options import MAX_BRIEF_SHOUTS, MAX_SHOUT_LENGTH
from ..worldmodel import WorldModel
from .common import WOLF_ENTITY_TYPE


def validated_shouts(shouts: Sequence[str]) -> tuple[str, ...]:
    """The brief's shout phrases, trimmed; too many or too long is a retry."""
    phrases = tuple(phrase.strip() for phrase in shouts if phrase.strip())
    if len(phrases) > MAX_BRIEF_SHOUTS:
        raise ModelRetry(f"at most {MAX_BRIEF_SHOUTS} shout phrases per stint")
    too_long = [phrase for phrase in phrases if len(phrase) > MAX_SHOUT_LENGTH]
    if too_long:
        raise ModelRetry(
            f"a shout phrase is at most {MAX_SHOUT_LENGTH} characters: {too_long[0]!r}"
        )
    return phrases


def refuse_dead_targets(model: WorldModel, *texts: str) -> None:
    """Retry any brief text naming an entity this actor watched die.

    Only ids the actor saw die and has not seen alive since; a settler that
    respawned is forgotten by `WorldModel.death_of`.
    """
    facts: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for token in OBJECT_ID_PATTERN.findall(text):
            if token in seen:
                continue
            seen.add(token)
            death = model.death_of(token)
            if death is not None:
                facts.append(death.fact())
    if facts:
        raise ModelRetry("; ".join(facts))


def settlers_met(model: WorldModel) -> list[str]:
    """Every settler this actor has ever seen, by id, sorted."""
    return sorted(
        entity.entity_id
        for entity in model.entities.values()
        if entity.entity_id != model.entity_id
        and entity.entity_type != WOLF_ENTITY_TYPE
    )


def validated_hails(
    hails: Sequence[Mapping[str, str]], model: WorldModel
) -> tuple[BriefHail, ...]:
    """The brief's hails, checked against who this actor has actually met.

    Jev is given the settler's name and the line and nothing else, so a name it
    has never seen would be an option code could never build.
    """
    if len(hails) > MAX_BRIEF_HAILS:
        raise ModelRetry(f"at most {MAX_BRIEF_HAILS} hails per stint")
    met = settlers_met(model)
    checked: list[BriefHail] = []
    for entry in hails:
        settler = str(entry.get("settler", "")).strip()
        line = str(entry.get("line", "")).strip()
        if not settler or not line:
            raise ModelRetry(
                "each hail needs a `settler` and a `line`, for example "
                '{"settler": "dov", "line": "Dov, can we split the wall work?"}'
            )
        if settler == model.entity_id:
            raise ModelRetry("you cannot hail yourself")
        if settler not in met:
            known = ", ".join(met) or "nobody yet"
            raise ModelRetry(
                f"you have never seen a settler called {settler}; "
                f"settlers you have met: {known}"
            )
        if len(line) > items.CONVERSATION_TEXT_LIMIT:
            raise ModelRetry(
                f"a hail line is at most {items.CONVERSATION_TEXT_LIMIT} "
                f"characters: {line!r}"
            )
        purpose = str(entry.get("purpose", "")).strip()
        checked.append(BriefHail(settler=settler, line=line, purpose=purpose))
    return tuple(checked)


def validated_places(
    places: Mapping[str, Sequence[int]], model: WorldModel
) -> dict[str, Coord]:
    """The brief's named places, checked; anything wrong is a retry.

    Jev is given the offset to each place and never the coordinate, so a name
    that is also an object or settler id would be two different things in one
    option list.
    """
    if len(places) > MAX_BRIEF_PLACES:
        raise ModelRetry(f"at most {MAX_BRIEF_PLACES} places per brief")
    checked: dict[str, Coord] = {}
    for name, position in places.items():
        if not PLACE_NAME_PATTERN.match(name):
            raise ModelRetry(
                f"a place name is 1 to 24 characters of lowercase letters, "
                f"digits and underscores: {name!r}"
            )
        if name in model.objects or name in model.entities:
            raise ModelRetry(
                f"the place name {name!r} is already the id of a known object "
                "or settler; pick another name"
            )
        pair = list(position)
        if len(pair) != 2:
            raise ModelRetry(f"the place {name!r} needs an [x, y] pair")
        checked[name] = (int(pair[0]), int(pair[1]))
    return checked


def direction_value(name: str) -> pb.Direction:
    key = name.strip().upper()
    if key not in NAME_TO_DIRECTION:
        raise ModelRetry(
            f"unknown direction {name!r}; use one of {sorted(NAME_TO_DIRECTION)}"
        )
    return NAME_TO_DIRECTION[key]


def parse_tile_list(text: str) -> list[Coord]:
    """Parse `"12,30; 13,30"` into coordinates; empty text yields no tiles.

    Raises:
        ModelRetry: when a pair is not two integers, so the model can fix it.
    """
    tiles: list[Coord] = []
    for chunk in text.replace("(", " ").replace(")", " ").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) != 2:
            raise ModelRetry(
                f"{chunk!r} is not a tile; write tiles as 'x,y' separated by ';'"
            )
        try:
            tiles.append((int(parts[0].strip()), int(parts[1].strip())))
        except ValueError as error:
            raise ModelRetry(f"{chunk!r} is not a pair of integers: {error}") from error
    return tiles
