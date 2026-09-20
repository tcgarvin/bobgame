"""Crafting: what can be made here, what it would say, and making a chain of it.

`items.py` is the recipe table; this is everything both layers do with it. The
option layer asks what is craftable right now and how to describe it; the
planner's `craft`, `build`, `place` and `place_sign` tools run the same chain —
craft a kind, crafting its missing inputs first — and the same "where does this
raw material come from" lines when a craft fails.

The chain needs a body to act with, but not the whole `AgentBridge`: `Crafter`
below is the two members it uses, so this module sits well under the planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol

from .. import world_pb2 as pb
from . import items
from .geometry import chebyshev
from .outcomes import CRAFTED_DETAIL, action_succeeded
from .worldmodel import WorldModel

# How deep `build` follows a recipe's inputs: wood -> plank -> wood_wall.
CRAFT_CHAIN_DEPTH = 2
# `craft` repeats the action until the recipe completes; the margin covers a
# tick whose event the world did not report back.
CRAFT_ACTION_MARGIN = 2
# How many known objects of a raw material's source type a failed craft names.
SOURCES_SHOWN = 3

# Craft options are offered in this order and then truncated, so the things a
# settler usually needs first survive the cap.
CRAFT_PRIORITY: tuple[str, ...] = (
    items.AXE,
    items.PICKAXE,
    items.SWORD,
    items.IRON_SWORD,
    items.COPPER_AXE,
    items.COPPER_PICKAXE,
    items.IRON_AXE,
    items.IRON_PICKAXE,
    items.CHARCOAL,
    items.COPPER_INGOT,
    items.IRON_INGOT,
    items.PLANK,
    items.WORKSHOP_TABLE,
    items.FURNACE,
    items.ANVIL,
    items.ROPE,
    items.WOOD_WALL,
    items.DOOR,
    items.BED,
    items.ROAD,
    items.WOOD_FLOOR,
    items.STONE_WALL,
    items.STONE_FLOOR,
    items.CHEST,
    items.MESSAGE_BOARD,
    items.SIGN,
    items.TABLE,
    items.CHAIR,
)

# One of each of these in the pack is plenty; a second is never urgent enough
# to spend an option slot on.
CRAFT_ONCE_KINDS: frozenset[str] = (
    items.WIELDABLE_KINDS
    | items.STATION_KINDS
    | frozenset({items.CHEST, items.MESSAGE_BOARD})
)


class Crafter(Protocol):
    """The body a craft chain acts with: the world model, and one action."""

    @property
    def model(self) -> WorldModel:
        """The shared world model, updated every tick."""

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        """Submit one intent on the next tick and report what happened."""


# --------------------------------------------------------------------------
# What can be made here
# --------------------------------------------------------------------------


def craftable_now(model: WorldModel, inventory: Mapping[str, int]) -> list[str]:
    """Recipe names that would succeed on this tick, in priority order.

    A recipe is craftable when the inputs are in the pack and, for a station
    recipe, a placed station of that type is on or next to the actor's tile.
    """
    ready: list[str] = []
    for name, recipe in items.RECIPES.items():
        if recipe.station and model.station_near(recipe.station) is None:
            continue
        if any(
            inventory.get(kind, 0) < amount for kind, amount in recipe.inputs.items()
        ):
            continue
        if name in CRAFT_ONCE_KINDS and inventory.get(name, 0) > 0:
            continue
        if name in items.WIELDABLE_KINDS and model.self_info.wielded == name:
            continue
        ready.append(name)
    ready.sort(key=_craft_rank)
    return ready


def _craft_rank(recipe: str) -> tuple[int, str]:
    if recipe in CRAFT_PRIORITY:
        return (CRAFT_PRIORITY.index(recipe), recipe)
    return (len(CRAFT_PRIORITY), recipe)


def craft_description(model: WorldModel, recipe_name: str) -> str:
    """One craft option's text: cost, yield, station, and work still to do."""
    recipe = items.RECIPES[recipe_name]
    yields = "" if recipe.output_count == 1 else f", {recipe.output_count} of them"
    text = f"craft {recipe_name} using {recipe.cost_text()}{yields}"
    if not recipe.station:
        return text
    text += f" at the {recipe.station} within reach"
    if recipe.work <= 1:
        return text
    station = model.station_near(recipe.station)
    done = 0
    if station is not None:
        started, actions = station.craft_progress(model.entity_id)
        if started == recipe_name:
            done = actions
    return f"{text}; {recipe.work} craft actions, {done} done so far"


# --------------------------------------------------------------------------
# Where a raw material comes from
# --------------------------------------------------------------------------


def source_lines(model: WorldModel, kind: str, limit: int = SOURCES_SHOWN) -> list[str]:
    """Where `kind` comes from, and the nearest few such objects known."""
    where = items.source_text(kind)
    if not where:
        return []
    lines = [where]
    position = model.self_info.position
    for obj in model.objects_by_type(items.source_object_types(kind))[:limit]:
        lines.append(
            f"  {obj.object_id} at {obj.position} "
            f"(d{chebyshev(obj.position, position)})"
        )
    if len(lines) == 1:
        lines.append("  you know of none yet")
        lines.extend(_water_hint_lines(model, kind))
    return lines


def _water_hint_lines(model: WorldModel, kind: str) -> list[str]:
    """For a water-bound material nothing known yields, where the water is.

    The habitat sentence says the stuff grows by fresh water; this says which
    water this settler has actually seen. The observation does not label water
    fresh or salt, so the line says "water" and nothing more.
    """
    if not items.is_water_bound(kind):
        return []
    water = model.nearest_water()
    if water is None:
        return []
    distance = chebyshev(water, model.self_info.position)
    return [f"  nearest water you have seen: {water} (d{distance})"]


def missing_input_lines(model: WorldModel, recipe: items.Recipe) -> list[str]:
    """For every input the pack is short of, where that input comes from."""
    inventory = model.self_info.inventory
    lines: list[str] = []
    for name, amount in sorted(recipe.inputs.items()):
        if inventory.get(name, 0) >= amount:
            continue
        lines.extend(source_lines(model, name))
    return lines


# --------------------------------------------------------------------------
# Making things
# --------------------------------------------------------------------------


@dataclass
class CraftTally:
    """What a chain of crafts produced, and the first thing that stopped it."""

    made: dict[str, int] = field(default_factory=dict)
    stopped: str = ""

    @property
    def total(self) -> int:
        """How many items of all kinds the chain produced."""
        return sum(self.made.values())

    def record(self, kind: str, count: int) -> None:
        """Add `count` of `kind` to what this chain has made."""
        self.made[kind] = self.made.get(kind, 0) + count

    def absorb(self, other: "CraftTally") -> None:
        """Fold another chain's output in; its stop reason is the latest one."""
        for kind, count in other.made.items():
            self.record(kind, count)
        self.stopped = other.stopped

    def text(self) -> str:
        """`"crafted 8 plank, 4 wood_wall"`, or `""` when nothing was made."""
        if not self.made:
            return ""
        parts = ", ".join(
            f"{count} {kind}" for kind, count in sorted(self.made.items())
        )
        return f"crafted {parts}"


def carried(crafter: Crafter, kind: str) -> int:
    """How many of `kind` the body holds right now."""
    return crafter.model.self_info.inventory.get(kind, 0)


async def craft_once(crafter: Crafter, recipe: str, known: items.Recipe) -> str:
    """Repeat the craft action for `recipe` until it finishes or an action fails.

    A failure caused by a missing raw input ends with where that input comes
    from and the nearest such objects the actor knows of: `"craft failed: bed
    needs 4 plank + 3 fiber"` on its own never told anybody that fiber is cut
    from reeds, and in a whole six-settler run one settler gathered reeds.
    """
    lines: list[str] = []
    failed = False
    for _ in range(known.work + CRAFT_ACTION_MARGIN):
        outcome = await crafter.direct_action(
            pb.Intent(craft=pb.CraftIntent(recipe=recipe)), f"craft {recipe}"
        )
        lines.append(outcome)
        if not action_succeeded(outcome):
            failed = True
            break
        if CRAFTED_DETAIL in outcome:
            break
    if failed:
        lines.extend(missing_input_lines(crafter.model, known))
    return "\n".join(lines)


async def _craft_inputs(
    crafter: Crafter,
    kind: str,
    recipe: items.Recipe,
    tally: CraftTally,
    depth: int,
) -> bool:
    """Make sure one craft of `kind` can run right now.

    Missing inputs are crafted in turn while `depth` allows it, which is what
    turns 2 wood into the plank a wood_wall eats. False means it cannot run,
    and `tally.stopped` says why.
    """
    for name, amount in recipe.inputs.items():
        if carried(crafter, name) >= amount:
            continue
        if depth <= 0 or name not in items.RECIPES:
            shortfall = (
                f"{kind} takes {amount} {name} and you carry {carried(crafter, name)}"
            )
            where = source_lines(crafter.model, name)
            tally.stopped = "\n".join([shortfall, *where]) if where else shortfall
            return False
        await craft_chain(
            crafter, name, amount - carried(crafter, name), tally, depth - 1
        )
        if carried(crafter, name) < amount:
            return False
    return True


async def craft_chain(
    crafter: Crafter,
    kind: str,
    wanted: int,
    tally: CraftTally,
    depth: int = CRAFT_CHAIN_DEPTH,
) -> None:
    """Craft `wanted` more `kind`, making missing inputs first, into `tally`.

    Each craft costs its recipe's actions in ticks. Nothing walks anywhere: a
    station recipe is only attempted where the station already is.
    """
    recipe = items.RECIPES.get(kind)
    if recipe is None:
        tally.stopped = f"{kind} cannot be crafted"
        return
    if recipe.station and crafter.model.station_near(recipe.station) is None:
        tally.stopped = (
            f"{kind} is made at a {recipe.station}, and there is none on or "
            "next to your tile"
        )
        return
    start = carried(crafter, kind)
    while carried(crafter, kind) - start < wanted:
        before = carried(crafter, kind)
        if not await _craft_inputs(crafter, kind, recipe, tally, depth):
            return
        outcome = await craft_once(crafter, kind, recipe)
        made = carried(crafter, kind) - before
        if made <= 0:
            tally.stopped = f"crafting {kind} made none: {outcome.splitlines()[-1]}"
            return
        tally.record(kind, made)


async def stock_one(crafter: Crafter, kind: str) -> list[str]:
    """Craft one `kind` for `place` to put down, and say what happened.

    The lines are what the model is shown: what the chain made, and, when the
    pack is still empty afterwards, why it stopped and where the raw material
    it lacked comes from.
    """
    tally = CraftTally()
    await craft_chain(crafter, kind, 1, tally)
    lines = [tally.text()] if tally.text() else []
    if carried(crafter, kind) <= 0:
        lines.append(tally.stopped or f"you carry no {kind} and made none")
    return lines
