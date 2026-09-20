"""Making and placing things: build, craft, place, equip, rest and dismantle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.toolsets import FunctionToolset

from .... import world_pb2 as pb
from ... import items
from ...actions import dismantle_attempt, place_attempt, place_failure_lines
from ...briefs import Brief
from ...build import (
    BUILD_OUT_OF_ITEMS,
    BuildExecutor,
    BuildPlan,
    BuildPlanError,
    SHAPE_TILES,
    SHAPES,
    build_brief_text,
    make_plan,
    missing_pieces_text,
    plan_tiles,
)
from ...geometry import NO_DIRECTION, Coord
from ...recipes import CraftTally, carried, craft_chain, craft_once, stock_one
from ...stint import StintReport
from ..toolset import PlannerDeps
from ..validation import direction_value, parse_tile_list
from .common import run_attempt, attempt_text

# How many pieces one `build` resupply crafts at most, and how often a build
# stops to make more. Each craft action is a tick, so an unbounded resupply
# would eat the tool's whole tick budget before laying a single wall.
BUILD_CRAFT_LIMIT = 20
BUILD_RESUPPLY_ROUNDS = 3


async def _stock_for_build(
    ctx: RunContext[PlannerDeps], plan: BuildPlan, shortfall: int
) -> CraftTally:
    """Craft up to `BUILD_CRAFT_LIMIT` more of the plan's piece, from the pack."""
    tally = CraftTally()
    wanted = min(shortfall, BUILD_CRAFT_LIMIT)
    if wanted > 0:
        await craft_chain(ctx.deps.bridge, plan.kind, wanted, tally)
    return tally


@dataclass(frozen=True)
class _BuildPhase:
    """What one shape's worth of crafting and building produced."""

    lines: list[str]
    executor: BuildExecutor | None
    ticks_used: int


async def _build_phase(
    ctx: RunContext[PlannerDeps], plan: BuildPlan, max_ticks: int
) -> _BuildPhase:
    """Craft what the shape needs, build it, and craft again when it runs dry.

    Crafting and building share one tick budget: every craft action and every
    step or placement is a tick, so the ticks the crafts took come off what
    the walk has left.
    """
    bridge = ctx.deps.bridge
    made = CraftTally()
    ticks_left = max_ticks

    def spend(started_at: int) -> None:
        nonlocal ticks_left
        ticks_left -= max(0, bridge.model.tick - started_at)

    started = bridge.model.tick
    made.absorb(
        await _stock_for_build(
            ctx, plan, len(plan.tiles) - carried(ctx.deps.bridge, plan.kind)
        )
    )
    spend(started)
    if carried(ctx.deps.bridge, plan.kind) <= 0:
        refusal = [missing_pieces_text(plan, bridge.model.self_info.inventory)]
        if made.stopped:
            refusal.append(f"nothing crafted: {made.stopped}")
        return _BuildPhase(refusal, None, max_ticks - ticks_left)

    executor = BuildExecutor(plan)
    instruction, success = build_brief_text(plan)
    report: StintReport | None = None
    for _ in range(BUILD_RESUPPLY_ROUNDS):
        if ticks_left <= 0:
            break
        brief = Brief(
            instruction=instruction,
            success_condition=success,
            max_ticks=ticks_left,
            notes=f"shapes: {sorted(SHAPES)}",
        )
        report = await bridge.run_stint(brief, executor)
        ticks_left -= report.ticks_used
        if report.end_reason != BUILD_OUT_OF_ITEMS or ticks_left <= 0:
            break
        started = bridge.model.tick
        refill = await _stock_for_build(ctx, plan, len(executor.progress().remaining))
        spend(started)
        made.absorb(refill)
        if refill.total <= 0:
            break

    lines: list[str] = []
    if made.text():
        lines.append(f"for this build, {made.text()}")
    if report is None:
        lines.append(
            f"no ticks left to build: crafting used the whole "
            f"{max_ticks}-tick budget"
        )
        return _BuildPhase(lines, executor, max_ticks - ticks_left)
    lines.append(report.to_text())
    lines.append(executor.summary())
    if report.end_reason == BUILD_OUT_OF_ITEMS and made.stopped:
        lines.append(f"  could not craft more {plan.kind}: {made.stopped}")
    return _BuildPhase(lines, executor, max_ticks - ticks_left)


def _door_plan(
    kind: str,
    shape: str,
    start: Coord,
    end: Coord,
    tiles: str,
    doors: Sequence[Coord],
) -> BuildPlan | None:
    """The door pass for a shape, or None when no door tile was asked for.

    Raises:
        BuildPlanError: when `kind` is already a door, or a door tile is not
            one of the shape's own tiles.
    """
    if not doors:
        return None
    if kind == items.DOOR:
        raise BuildPlanError("the whole shape is already doors; drop the door argument")
    on_shape = set(plan_tiles(shape, start, end, parse_tile_list(tiles)))
    stray = [tile for tile in doors if tile not in on_shape]
    if stray:
        named = "; ".join(f"{x},{y}" for x, y in stray)
        raise BuildPlanError(
            f"door tiles must be tiles of the shape itself; these are not: {named}"
        )
    return make_plan(items.DOOR, SHAPE_TILES, start, end, explicit=list(doors))


async def _run_build(
    ctx: RunContext[PlannerDeps],
    plan: BuildPlan,
    max_ticks: int,
    door_plan: BuildPlan | None = None,
) -> str:
    """Build the shape, then the doors in it, then say what the pieces form.

    The door tiles are kept out of the main shape, so the wall run leaves them
    open and the door pass fills them. A door pass that cannot craft a door
    leaves the tile as a gap and says what it was short of.
    """
    model = ctx.deps.bridge.model
    main = await _build_phase(ctx, plan, max_ticks)
    lines = list(main.lines)
    door_tiles: list[Coord] = []

    if door_plan is not None:
        door_ticks = max_ticks - main.ticks_used
        if door_ticks <= 0:
            lines.append(
                f"doors: no ticks left for the {len(door_plan.tiles)} door "
                f"tile(s); they are still gaps"
            )
        else:
            doors = await _build_phase(ctx, door_plan, door_ticks)
            lines.append(f"doors ({len(door_plan.tiles)} tile(s)):")
            lines.extend(f"  {line}" for line in doors.lines)
            door_tiles.extend(door_plan.tiles)

    if main.executor is not None:
        lines.extend(main.executor.geometry_lines(model, extra_tiles=door_tiles))
    return "\n".join(lines)


def register_build(tools: FunctionToolset[PlannerDeps]) -> None:
    """build: one shape of one piece, laid tile by tile."""

    @tools.tool
    async def build(
        ctx: RunContext[PlannerDeps],
        kind: str,
        shape: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        max_ticks: int = 60,
        skip: str = "",
        tiles: str = "",
        door: str = "",
    ) -> str:
        """Build a shape out of one kind of piece, placing it tile by tile.

        Code does the walking and the coordinates; Jev is not involved.

        You do not have to carry the pieces. Before it starts, and again
        whenever it runs out mid-shape, this crafts as many pieces as the
        shape still needs out of what is in your pack, including the
        intermediate steps (wood into planks into walls), up to 20 at a time
        and three refills per call. Each craft action costs a tick out of
        `max_ticks`. It never walks to a station: a recipe made at a
        workshop_table, furnace or anvil is only crafted when that station is
        already on or next to the tile you stand on. If it cannot make the
        first piece, nothing happens and the result names the shortfall and
        the recipe.

        Args:
            kind: the building item to place, for example wood_wall, door, road,
                wood_floor, stone_wall, stone_floor, bed, workshop_table.
            shape: "line" from (x1,y1) to (x2,y2), "rect" for the outline of the
                rectangle with those two corners, "rect_filled" for every tile
                inside it, or "tiles" to use the `tiles` argument instead.
            x1: first corner or line start, map x.
            y1: first corner or line start, map y.
            x2: second corner or line end, map x.
            y2: second corner or line end, map y.
            max_ticks: tick budget; roughly two ticks per tile plus the walk.
            skip: tiles to leave out entirely, as "x,y; x,y". Nothing is placed
                there and nothing is crafted for them.
            tiles: the explicit tile list for shape "tiles", as "x,y; x,y".
            door: tiles of this shape, as "x,y; x,y", that get a door instead
                of `kind`. Each must be one of the shape's own tiles. The
                doors are placed after the rest of the shape, out of the same
                tick budget, and are crafted the same way `kind` is. A door
                that cannot be made leaves its tile empty.

        Returns:
            What was crafted, the stint report, and what was placed, what was
            skipped and why the build stopped: build_done, build_out_of_items,
            build_blocked, build_danger, build_would_seal_you_in or
            ticks_exhausted. A build that runs out of pieces it cannot make
            says how many more it needs and the recipe for them. Then, for a
            shape of walls or doors, what the standing pieces now enclose: the
            interior, its doors and gaps, and the objects inside it.
        """
        doors = parse_tile_list(door)
        try:
            plan = make_plan(
                kind=kind,
                shape=shape,
                start=(x1, y1),
                end=(x2, y2),
                explicit=parse_tile_list(tiles),
                skip=[*parse_tile_list(skip), *doors],
            )
            door_plan = _door_plan(kind, shape, (x1, y1), (x2, y2), tiles, doors)
        except BuildPlanError as error:
            raise ModelRetry(str(error)) from error
        return await _run_build(ctx, plan, max(1, max_ticks), door_plan)


def register_craft(tools: FunctionToolset[PlannerDeps]) -> None:
    """craft: one recipe, repeated until the item is made."""

    @tools.tool
    async def craft(ctx: RunContext[PlannerDeps], recipe: str) -> str:
        """Craft one recipe, repeating the craft action until the item is made.

        A station recipe needs that station on or next to your tile. A recipe of
        N actions costs N ticks here, and the call stops early on the first
        action that fails.

        Args:
            recipe: one of the names in the recipe table in your instructions.
        """
        known = items.RECIPES.get(recipe)
        if known is None:
            return f"no such recipe {recipe!r}; known: {sorted(items.RECIPES)}"
        return await craft_once(ctx.deps.bridge, recipe, known)


def register_place(tools: FunctionToolset[PlannerDeps]) -> None:
    """equip and place: what to hold, and what to put down."""

    @tools.tool
    async def equip(ctx: RunContext[PlannerDeps], kind: str = "") -> str:
        """Wield an item from your pack, or pass an empty string to unequip."""
        outcome = await ctx.deps.bridge.direct_action(
            pb.Intent(equip=pb.EquipIntent(kind=kind)), f"equip {kind or '(nothing)'}"
        )
        return outcome.text()

    @tools.tool
    async def place(
        ctx: RunContext[PlannerDeps], kind: str, direction: str = ""
    ) -> str:
        """Put one item down as an object, crafting it first if you need to.

        If you are not carrying one but are carrying the inputs, this call
        crafts it and then places it: one call for a bed when the planks and
        the fiber are in your pack. A station recipe is only crafted where
        that station already is, on or next to your tile; nothing walks. If
        it cannot be made, nothing is placed and the result names the
        shortfall and where the raw material comes from.

        For walls, floors and roads use `build` instead: one call places a
        whole line or rectangle.

        Args:
            kind: what to place: chest, message_board, or any building item.
            direction: N, NE, E, SE, S, SW, W or NW for structures, which are
                placed on the neighbouring tile. Leave it empty for road,
                wood_floor and stone_floor: those go on your own tile.
        """
        if kind == items.SIGN:
            return (
                "use place_sign to put a sign up: it places the sign and writes "
                "its line in one call, and a blank sign says nothing to anybody"
            )
        value = direction_value(direction) if direction else NO_DIRECTION
        model = ctx.deps.bridge.model
        attempt = place_attempt(model, kind, value)
        if not attempt.allowed:
            return attempt.refusal
        lines: list[str] = []
        if carried(ctx.deps.bridge, kind) <= 0 and kind in items.RECIPES:
            lines.extend(await stock_one(ctx.deps.bridge, kind))
            if carried(ctx.deps.bridge, kind) <= 0:
                return "\n".join(lines)
        outcome = await run_attempt(ctx, attempt)
        lines.append(outcome.text())
        if not outcome.ok:
            lines.append(place_failure_lines(model, kind, value))
        return "\n".join(lines)


def register_upkeep(tools: FunctionToolset[PlannerDeps]) -> None:
    """rest and dismantle: healing on a bed, and taking a piece apart."""

    @tools.tool
    async def rest(ctx: RunContext[PlannerDeps], object_id: str) -> str:
        """Rest on a bed you are standing on or next to, to heal a little.

        One settler per bed per tick; if someone beat you to it the world says
        the bed is taken.
        """
        outcome = await ctx.deps.bridge.direct_action(
            pb.Intent(rest=pb.RestIntent(object_id=object_id)), f"rest on {object_id}"
        )
        return outcome.text()

    @tools.tool
    async def dismantle(ctx: RunContext[PlannerDeps], object_id: str) -> str:
        """Take apart one placed building object on your tile or next to it.

        It takes three of these in a row to finish, and returns one item. Use it
        to fix your own mistakes, not to undo other settlers' work without
        saying so on the message board first.
        """
        return await attempt_text(
            ctx, dismantle_attempt(ctx.deps.bridge.model, object_id)
        )
