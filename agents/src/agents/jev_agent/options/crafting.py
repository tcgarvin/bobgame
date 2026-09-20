"""Options that make something: crafting, wielding and placing what is carried."""

from __future__ import annotations

from typing import Mapping

from ... import world_pb2 as pb
from .. import items
from ..actions import place_attempt
from ..briefs import Option
from ..geometry import NO_DIRECTION, ORDERED_DIRECTIONS, direction_name
from ..recipes import craft_description, craftable_now
from ..worldmodel import WorldModel
from .common import (
    CRAFT_OPTION_LIMIT,
    JEV_PLACEABLE_KINDS,
    PLACE_OPTION_LIMIT,
    WIELDABLE_KINDS,
)


def _crafting_options(model: WorldModel, inventory: Mapping[str, int]) -> list[Option]:
    options: list[Option] = []
    for recipe_name in craftable_now(model, inventory)[:CRAFT_OPTION_LIMIT]:
        options.append(
            Option(
                key=f"craft:{recipe_name}",
                description=craft_description(model, recipe_name),
                intent=pb.Intent(craft=pb.CraftIntent(recipe=recipe_name)),
            )
        )

    wielded = model.self_info.wielded
    for kind in sorted(WIELDABLE_KINDS & set(inventory)):
        if kind == wielded:
            continue
        options.append(
            Option(
                key=f"equip:{kind}",
                description=f"wield the {kind} you are carrying",
                intent=pb.Intent(equip=pb.EquipIntent(kind=kind)),
            )
        )

    options.extend(_place_options(model, inventory))
    return options


def _place_options(model: WorldModel, inventory: Mapping[str, int]) -> list[Option]:
    """One placement per carried building item: own tile for ground, one free
    neighbour for structures."""
    options: list[Option] = []
    for kind in sorted(JEV_PLACEABLE_KINDS & set(inventory)):
        if len(options) >= PLACE_OPTION_LIMIT:
            break
        if items.is_ground_kind(kind):
            here = place_attempt(model, kind, NO_DIRECTION)
            if not here.allowed:
                continue
            options.append(
                Option(
                    key=f"place:{kind}:here",
                    description=f"lay {kind} down on the tile you are standing on",
                    intent=here.intent,
                )
            )
            continue
        for direction in ORDERED_DIRECTIONS:
            attempt = place_attempt(model, kind, direction)
            if not attempt.allowed:
                continue
            name = direction_name(direction)
            options.append(
                Option(
                    key=f"place:{kind}:{name}",
                    description=f"set down the {kind} on the empty tile to the {name}",
                    intent=attempt.intent,
                )
            )
            break  # one placement direction per kind is enough choice for Jev
    return options
