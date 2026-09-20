"""Options for whatever is underfoot or next to the actor: harvest, pick up, chests."""

from __future__ import annotations

from typing import Mapping

from .. import items
from ..actions import (
    collect_here_attempt,
    deposit_attempt,
    extract_attempt,
    pickup_attempt,
    withdraw_attempt,
)
from ..briefs import Option
from ..geometry import Coord, same_or_adjacent
from ..worldmodel import EXTRACTABLE_TYPES, ObjectInfo, WorldModel
from .common import _object_label


def _interaction_options(
    model: WorldModel, inventory: Mapping[str, int], position: Coord
) -> list[Option]:
    options: list[Option] = []
    wielded = model.self_info.wielded

    for obj in model.objects_near(1):
        if obj.object_type in EXTRACTABLE_TYPES and same_or_adjacent(
            position, obj.position
        ):
            option = _extract_option(model, obj, position, wielded)
            if option is not None:
                options.append(option)
        elif obj.object_type == "bush" and obj.position == position and obj.has_berry:
            collect = collect_here_attempt(model)
            if collect.allowed:
                options.append(
                    Option(
                        key=f"collect:{obj.object_id}",
                        description="pick the berry off the bush on this tile",
                        intent=collect.intent,
                    )
                )
        elif obj.object_type == "item_pile" and obj.position == position:
            for kind, count in sorted(obj.contents().items()):
                options.append(
                    Option(
                        key=f"pickup:{kind}",
                        description=f"pick up {kind} from the pile here ({count} available)",
                        intent=pickup_attempt(model, kind, min(count, 5)).intent,
                    )
                )
        elif obj.object_type == "chest" and same_or_adjacent(position, obj.position):
            options.extend(_chest_options(model, obj, inventory))

    return options


def _chest_options(
    model: WorldModel, obj: ObjectInfo, inventory: Mapping[str, int]
) -> list[Option]:
    options: list[Option] = []
    contents = obj.contents()
    for kind in sorted(inventory):
        if inventory[kind] <= 0:
            continue
        options.append(
            Option(
                key=f"deposit:{obj.object_id}:{kind}",
                description=f"put {kind} from your pack into chest {obj.object_id}",
                intent=deposit_attempt(
                    model, obj.object_id, kind, inventory[kind]
                ).intent,
            )
        )
    for kind, count in sorted(contents.items()):
        options.append(
            Option(
                key=f"withdraw:{obj.object_id}:{kind}",
                description=f"take {kind} out of chest {obj.object_id} ({count} inside)",
                intent=withdraw_attempt(
                    model, obj.object_id, kind, min(count, 5)
                ).intent,
            )
        )
    return options


def _extract_option(
    model: WorldModel, obj: ObjectInfo, position: Coord, wielded: str
) -> Option | None:
    """Harvesting one object, or None when what is in hand cannot work it.

    A vein needs a pickaxe of the right tier; everything else yields to bare
    hands, only slower.
    """
    attempt = extract_attempt(model, obj.object_id)
    if not attempt.allowed:
        return None
    yields = items.EXTRACT_YIELD.get(obj.object_type, "materials")
    work = items.extract_work(wielded, obj.object_type)
    holding = f"with the {wielded}" if wielded else "bare-handed"
    return Option(
        key=f"extract:{obj.object_id}",
        description=(
            f"harvest {yields} from the {_object_label(obj, position)}, "
            f"{obj.remaining} units left ({work} work per action {holding}, "
            f"{items.EXTRACT_THRESHOLD} work per unit)"
        ),
        intent=attempt.intent,
    )
