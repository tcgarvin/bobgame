"""Assembling one tick's option list out of the sections, best-first."""

from __future__ import annotations

from typing import Mapping, Sequence

from ..briefs import EMPTY_PLACES, BriefHail, Option, TravelState
from ..geometry import Coord
from ..worldmodel import WorldModel
from .common import MAX_OPTIONS, OPTION_SECTIONS, _wait_option
from .crafting import _crafting_options
from .interaction import _interaction_options
from .social import _conversation_options, _hail_options
from .steps import (
    _brief_step_options,
    _move_options,
    _quota_step_options,
    _travel_control_options,
)
from .survival import _survival_options


def enumerate_options(
    model: WorldModel,
    travel: TravelState | None = None,
    *,
    shouts: Sequence[str] = (),
    hails: Sequence[BriefHail] = (),
    places: Mapping[str, Coord] = EMPTY_PLACES,
    brief_text: str = "",
    max_options: int = MAX_OPTIONS,
) -> list[Option]:
    """Every action that is legal for this actor on this tick, best-first.

    `shouts` are the phrases the planner put in the brief; Jev may shout those
    and nothing else. `hails` are the settlers the brief lets Jev address, with
    the line to say, the same way. `places` are the brief's named destinations,
    and `brief_text` is the instruction and notes, scanned for object ids so
    that whatever the brief names is always walkable-to.
    """
    position = model.position
    inventory = dict(model.self_info.inventory)

    sections: dict[str, list[Option]] = {
        "wait": [_wait_option()],
        "brief_steps": _brief_step_options(model, position, places, brief_text),
        "survival": _survival_options(model, inventory, position, travel, shouts),
        "hail": _hail_options(model, position, hails),
        "conversation": _conversation_options(model, position, travel),
        "travel_control": _travel_control_options(model, travel),
        "interaction": _interaction_options(model, inventory, position),
        "craft": _crafting_options(model, inventory),
        "move": _move_options(model, position),
        "quota_steps": _quota_step_options(model, position, travel),
    }

    seen: set[str] = set()
    unique: list[Option] = []
    for section in OPTION_SECTIONS:
        for option in sections[section]:
            if option.key in seen:
                continue
            seen.add(option.key)
            unique.append(option)
    return unique[:max_options]


def options_to_criteria(options: Sequence[Option]) -> dict[str, str]:
    """The `criteria` mapping for a Jev `Choice` question."""
    return {option.key: option.description for option in options}
