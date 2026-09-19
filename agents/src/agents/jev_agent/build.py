"""Deterministic construction: walk a shape and place a piece on every tile.

Jev picks one action per tick out of a closed list and cannot hold a coordinate
in its head, so "build a wall from here to there" is not something it can do.
This module is the code that can: a pure geometry half that turns a shape into
an ordered list of tiles, and a `StintDriver` half that walks the entity with
the ordinary path finder and places one piece per tick.

The planner drives it through the `build` tool; the stint machinery around it
is the same one Jev uses, so danger, death, tick budgets, the report and the
JSONL trace all keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

import structlog

from .. import world_pb2 as pb
from . import items
from .geometry import (
    Coord,
    NO_DIRECTION,
    chebyshev,
    direction_between,
    direction_name,
    offset,
)
from .options import Option, can_place_ground
from .pathfinding import find_path, legal_directions
from .stint import DANGER_HEALTH_FLOOR, DriverChoice
from .worldmodel import WorldModel

logger = structlog.get_logger(__name__)

SHAPE_LINE = "line"
SHAPE_RECT = "rect"
SHAPE_RECT_FILLED = "rect_filled"
SHAPE_TILES = "tiles"
SHAPES: frozenset[str] = frozenset(
    {SHAPE_LINE, SHAPE_RECT, SHAPE_RECT_FILLED, SHAPE_TILES}
)

# Stint end reasons this driver produces. They land in `end_reason` in the
# trace, so they are short and greppable.
BUILD_DONE = "build_done"
BUILD_DANGER = "build_danger"
BUILD_OUT_OF_ITEMS = "build_out_of_items"
BUILD_BLOCKED = "build_blocked"
BUILD_WOULD_SEAL = "build_would_seal_you_in"

# A wolf this close stops the build; a builder is stationary and defenceless.
BUILD_DANGER_RADIUS = 4

# Per-tick search caps. Everything here runs inside the tick deadline, so the
# driver looks at a handful of candidates rather than the whole plan.
MAX_TARGET_SCAN = 6
MAX_STAND_SCAN = 4
# The seal check counts free tiles reachable from where the builder would
# stand; anything below the cap is a pocket it should not shut itself into.
SEAL_MIN_FREE_TILES = 64
SEAL_MAX_FREE_TILES = 200

MAX_PLAN_TILES = 400


class BuildPlanError(ValueError):
    """A shape that cannot be turned into a list of tiles."""


class BuildStateError(RuntimeError):
    """The executor was asked for an action when it had already finished."""


# --- geometry (pure, no world model) ---------------------------------------


def line_tiles(start: Coord, end: Coord) -> list[Coord]:
    """The 8-connected straight line from `start` to `end`, both included."""
    x0, y0 = start
    x1, y1 = end
    steps = max(abs(x1 - x0), abs(y1 - y0))
    if steps == 0:
        return [start]
    tiles: list[Coord] = []
    for index in range(steps + 1):
        # Round-half-up interpolation keeps the line symmetric end to end.
        x = x0 + round((x1 - x0) * index / steps)
        y = y0 + round((y1 - y0) * index / steps)
        tiles.append((int(x), int(y)))
    return _dedupe(tiles)


def rect_outline_tiles(corner: Coord, opposite: Coord) -> list[Coord]:
    """The perimeter of the rectangle spanned by two corners, walked clockwise.

    Clockwise order means the builder shuffles along the wall instead of
    criss-crossing the site.
    """
    left, right = sorted((corner[0], opposite[0]))
    top, bottom = sorted((corner[1], opposite[1]))
    tiles: list[Coord] = []
    for x in range(left, right + 1):
        tiles.append((x, top))
    for y in range(top + 1, bottom + 1):
        tiles.append((right, y))
    for x in range(right - 1, left - 1, -1):
        tiles.append((x, bottom))
    for y in range(bottom - 1, top, -1):
        tiles.append((left, y))
    return _dedupe(tiles)


def rect_filled_tiles(corner: Coord, opposite: Coord) -> list[Coord]:
    """Every tile of the rectangle, in boustrophedon order (rows alternate)."""
    left, right = sorted((corner[0], opposite[0]))
    top, bottom = sorted((corner[1], opposite[1]))
    tiles: list[Coord] = []
    for index, y in enumerate(range(top, bottom + 1)):
        columns = (
            range(left, right + 1) if index % 2 == 0 else range(right, left - 1, -1)
        )
        tiles.extend((x, y) for x in columns)
    return tiles


def plan_tiles(
    shape: str,
    start: Coord,
    end: Coord,
    explicit: Sequence[Coord] = (),
    skip: Iterable[Coord] = (),
) -> list[Coord]:
    """The ordered tiles a shape covers, minus `skip` (door gaps and the like).

    Args:
        shape: one of `line`, `rect`, `rect_filled`, `tiles`.
        start: first corner or line end.
        end: second corner or line end.
        explicit: the tile list, used only by the `tiles` shape.
        skip: tiles to leave out, such as the gap a door will go in.

    Raises:
        BuildPlanError: on an unknown shape, an empty `tiles` list, or a plan
            bigger than `MAX_PLAN_TILES`.
    """
    if shape not in SHAPES:
        raise BuildPlanError(f"unknown shape {shape!r}; use one of {sorted(SHAPES)}")
    if shape == SHAPE_TILES:
        if not explicit:
            raise BuildPlanError("shape 'tiles' needs a non-empty tile list")
        tiles = _dedupe(list(explicit))
    elif shape == SHAPE_LINE:
        tiles = line_tiles(start, end)
    elif shape == SHAPE_RECT:
        tiles = rect_outline_tiles(start, end)
    else:
        tiles = rect_filled_tiles(start, end)

    excluded = set(skip)
    tiles = [tile for tile in tiles if tile not in excluded]
    if not tiles:
        raise BuildPlanError("the shape is empty once the skipped tiles are removed")
    if len(tiles) > MAX_PLAN_TILES:
        raise BuildPlanError(
            f"{len(tiles)} tiles is more than one build can do "
            f"(limit {MAX_PLAN_TILES}); split it up"
        )
    return tiles


def _dedupe(tiles: Sequence[Coord]) -> list[Coord]:
    seen: set[Coord] = set()
    unique: list[Coord] = []
    for tile in tiles:
        if tile in seen:
            continue
        seen.add(tile)
        unique.append(tile)
    return unique


@dataclass(frozen=True)
class BuildPlan:
    """What to build and where, once the shape has been resolved to tiles."""

    kind: str
    tiles: tuple[Coord, ...]
    shape: str = SHAPE_TILES

    @property
    def is_ground(self) -> bool:
        """Whether the piece goes under the builder's own feet."""
        return items.is_ground_kind(self.kind)

    @property
    def blocks(self) -> bool:
        """Whether a finished piece makes its tile impassable."""
        return self.kind in items.BLOCKING_OBJECT_TYPES

    def describe(self) -> str:
        """One line naming the job, for the brief and the report."""
        first = self.tiles[0]
        last = self.tiles[-1]
        return (
            f"{self.kind} {self.shape} over {len(self.tiles)} tiles "
            f"from {first} to {last}"
        )

    def bounds(self) -> tuple[Coord, Coord]:
        """The inclusive bounding box of the plan, as (top-left, bottom-right)."""
        xs = [tile[0] for tile in self.tiles]
        ys = [tile[1] for tile in self.tiles]
        return ((min(xs), min(ys)), (max(xs), max(ys)))


def make_plan(
    kind: str,
    shape: str,
    start: Coord,
    end: Coord,
    explicit: Sequence[Coord] = (),
    skip: Iterable[Coord] = (),
) -> BuildPlan:
    """Validate the kind and resolve the shape into an ordered `BuildPlan`."""
    if kind not in items.BUILDING_KINDS:
        raise BuildPlanError(
            f"{kind!r} is not a building item; "
            f"use one of {sorted(items.BUILDING_KINDS)}"
        )
    tiles = plan_tiles(shape, start, end, explicit, skip)
    return BuildPlan(kind=kind, tiles=tuple(tiles), shape=shape)


# --- the driver -------------------------------------------------------------


@dataclass(frozen=True)
class _Step:
    """The tile to work on this tick, where to stand, and how to get there."""

    target: Coord
    stand: Coord
    path: tuple[Coord, ...]


class _BlockedView:
    """A terrain view with extra tiles pretended impassable (the seal check)."""

    def __init__(self, model: WorldModel, blocked: frozenset[Coord]) -> None:
        self._model = model
        self._blocked = blocked

    def is_walkable(self, position: Coord) -> bool:
        """Walkable in the real model and not one of the pretend walls."""
        if position in self._blocked:
            return False
        return self._model.is_walkable(position)

    def is_known(self, position: Coord) -> bool:
        """Whether the underlying model has ever seen this tile."""
        return self._model.is_known(position)


def _reachable_within(view: _BlockedView, start: Coord, cap: int) -> int:
    """How many tiles are reachable from `start`, counting no further than `cap`."""
    if not view.is_walkable(start):
        return 0
    seen: set[Coord] = {start}
    frontier: list[Coord] = [start]
    while frontier and len(seen) < cap:
        current = frontier.pop()
        for direction in legal_directions(view, current):
            neighbour = offset(current, direction)
            if neighbour in seen:
                continue
            seen.add(neighbour)
            if len(seen) >= cap:
                break
            frontier.append(neighbour)
    return len(seen)


@dataclass
class BuildProgress:
    """What the build has achieved so far."""

    placed: list[Coord] = field(default_factory=list)
    already_there: list[Coord] = field(default_factory=list)
    skipped: list[tuple[Coord, str]] = field(default_factory=list)
    remaining: list[Coord] = field(default_factory=list)


class BuildExecutor:
    """Runs one `BuildPlan`: walk, place, walk, place, and stop with a reason."""

    name = "build"

    def __init__(self, plan: BuildPlan) -> None:
        self.plan = plan
        self._remaining: list[Coord] = list(plan.tiles)
        self._placed: list[Coord] = []
        self._already: list[Coord] = []
        self._skipped: list[tuple[Coord, str]] = []
        self._sealing: set[Coord] = set()
        self._first_refresh = True
        self._stop_reason = ""
        self._step_tick = -1
        self._step: _Step | None = None
        self._carried = -1

    # -- StintDriver --------------------------------------------------------

    def stop_reason(self, model: WorldModel) -> str:
        """Why the build should end now, or `""` to keep going."""
        self._refresh(model)
        if not self._remaining:
            return BUILD_DONE
        if self._in_danger(model):
            return BUILD_DANGER
        self._carried = model.self_info.inventory.get(self.plan.kind, 0)
        if self._carried <= 0:
            return BUILD_OUT_OF_ITEMS
        if self._current_step(model) is None:
            if self._sealing:
                return BUILD_WOULD_SEAL
            return BUILD_BLOCKED
        return ""

    def choose(self, model: WorldModel) -> DriverChoice:
        """Walk one step toward the next piece, or place it."""
        step = self._current_step(model)
        if step is None:
            # `stop_reason` is asked first and would have ended the stint, so
            # this only happens if a caller drives the executor by hand.
            raise BuildStateError("no workable tile; the build should have stopped")
        if step.path:
            direction = direction_between(model.position, step.path[0])
            return DriverChoice(
                option=Option(
                    key=f"build_step:{direction_name(direction)}",
                    description=f"walk toward {step.target} to place {self.plan.kind}",
                    intent=pb.Intent(move=pb.MoveIntent(direction=direction)),
                ),
                note=f"{len(step.path)} steps to {step.target}",
            )
        return DriverChoice(option=self._place_option(step), note="placing")

    def summary(self) -> str:
        """The build's own report lines, appended to the stint report."""
        progress = self.progress()
        lines = [
            f"BUILD {self.plan.describe()}",
            f"  placed this build: {len(progress.placed)}",
            f"  already there: {len(progress.already_there)}",
            f"  left to do: {len(progress.remaining)}",
        ]
        if progress.remaining:
            preview = ", ".join(str(tile) for tile in progress.remaining[:6])
            lines.append(f"  next tiles: {preview}")
            lines.extend(self._supply_lines(len(progress.remaining)))
        if progress.skipped:
            lines.append("  skipped:")
            lines.extend(
                f"    {tile}: {reason}" for tile, reason in progress.skipped[:6]
            )
        return "\n".join(lines)

    def progress(self) -> BuildProgress:
        """A snapshot of placed, pre-existing, skipped and remaining tiles."""
        return BuildProgress(
            placed=list(self._placed),
            already_there=list(self._already),
            skipped=list(self._skipped),
            remaining=list(self._remaining),
        )

    # -- internals ----------------------------------------------------------

    def _supply_lines(self, remaining: int) -> list[str]:
        """What the builder carries against what is left, and how to make more.

        The report is the planner's only view of why a build stopped; a build
        that ends on an empty pack must say so in pieces and in recipe, or the
        planner calls it again with the same pack.
        """
        if self._carried < 0:
            return []
        lines = [f"  carrying now: {self._carried} {self.plan.kind}"]
        short = remaining - self._carried
        if short > 0:
            lines.append(f"  short by {short}: {supply_text(self.plan.kind, short)}")
        return lines

    def _refresh(self, model: WorldModel) -> None:
        """Drop tiles that are done or impossible, based on what we can now see."""
        keep: list[Coord] = []
        for tile in self._remaining:
            verdict, reason = self._inspect(model, tile)
            if verdict == "todo":
                keep.append(tile)
            elif verdict == "done":
                if self._first_refresh:
                    self._already.append(tile)
                else:
                    self._placed.append(tile)
            else:
                self._skipped.append((tile, reason))
        self._remaining = keep
        self._first_refresh = False
        self._step_tick = -1

    def _inspect(self, model: WorldModel, tile: Coord) -> tuple[str, str]:
        """Classify one planned tile as `todo`, `done` or `skip` (with a reason)."""
        if not model.is_known(tile):
            return ("todo", "")
        if self.plan.is_ground:
            for obj in model.ground_objects_at(tile):
                if obj.object_type == self.plan.kind:
                    return ("done", "")
                return ("skip", f"{obj.object_type} already covers it")
            for obj in model.object_at(tile):
                if obj.object_type in items.NATURAL_OBJECT_TYPES:
                    return ("skip", f"{obj.object_type} must be cleared first")
            if not can_place_ground(model, tile):
                return ("skip", "not buildable ground")
            return ("todo", "")

        for obj in model.structure_objects_at(tile):
            if obj.object_type == self.plan.kind:
                return ("done", "")
            return ("skip", f"{obj.object_type} is in the way")
        tile_info = model.tiles.get(tile)
        if tile_info is not None and not tile_info.walkable:
            return ("skip", "terrain will not take a structure")
        return ("todo", "")

    def _in_danger(self, model: WorldModel) -> bool:
        """The same rule the stint uses: a wolf close by, or too little health."""
        if model.self_info.health < DANGER_HEALTH_FLOOR:
            return True
        wolf = model.nearest_wolf()
        if wolf is None:
            return False
        return chebyshev(wolf.position, model.position) <= BUILD_DANGER_RADIUS

    def _current_step(self, model: WorldModel) -> _Step | None:
        """This tick's step, computed once and reused by `choose`."""
        if self._step_tick == model.tick:
            return self._step
        self._step_tick = model.tick
        self._step = self._plan_step(model)
        return self._step

    def _plan_step(self, model: WorldModel) -> _Step | None:
        scanned = 0
        for target in self._remaining:
            if target in self._sealing:
                continue
            if scanned >= MAX_TARGET_SCAN:
                break
            scanned += 1
            step = self._step_for(model, target)
            if step is not None:
                return step
        return None

    def _step_for(self, model: WorldModel, target: Coord) -> _Step | None:
        """Where to stand to place on `target`, and the path to get there."""
        if self.plan.is_ground:
            if model.position == target:
                return _Step(target=target, stand=target, path=())
            path = find_path(model, model.position, target)
            if not path:
                return None
            return _Step(target=target, stand=target, path=tuple(path))

        checked = 0
        for stand in self._stand_candidates(model, target):
            if checked >= MAX_STAND_SCAN:
                break
            checked += 1
            if self.plan.blocks and self._would_seal(model, stand, target):
                continue
            if stand == model.position:
                return _Step(target=target, stand=stand, path=())
            path = find_path(model, model.position, stand)
            if not path:
                continue
            return _Step(target=target, stand=stand, path=tuple(path))
        if self.plan.blocks and checked > 0:
            # Every way of reaching this tile would shut us in; leave it for the
            # planner to turn into a door.
            self._sealing.add(target)
        return None

    def _stand_candidates(self, model: WorldModel, target: Coord) -> list[Coord]:
        """Walkable neighbours of `target`, nearest to the builder first."""
        neighbours = [
            (target[0] + dx, target[1] + dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if (dx, dy) != (0, 0)
        ]
        usable = [
            tile
            for tile in neighbours
            if model.is_known(tile) and model.is_walkable(tile)
        ]
        # Standing where we already are costs no ticks at all.
        usable.sort(key=lambda tile: chebyshev(tile, model.position))
        return usable

    def _would_seal(self, model: WorldModel, stand: Coord, target: Coord) -> bool:
        """Whether placing a blocking piece on `target` would shut the builder in.

        No anchor, no map knowledge: flood fill from where the builder would be
        standing, with the new wall in place, and stop as soon as enough free
        tiles have been counted. A fill that runs out early means the builder
        is in a pocket.
        """
        view = _BlockedView(model, frozenset({target}))
        cap = self._free_tile_cap()
        return _reachable_within(view, stand, cap) < cap

    def _free_tile_cap(self) -> int:
        """How much open ground counts as "not shut in" for this plan."""
        wanted = max(SEAL_MIN_FREE_TILES, 2 * len(self.plan.tiles))
        return min(wanted, SEAL_MAX_FREE_TILES)

    def _place_option(self, step: _Step) -> Option:
        kind = self.plan.kind
        if self.plan.is_ground:
            intent = pb.Intent(place=pb.PlaceIntent(kind=kind, direction=NO_DIRECTION))
            description = f"lay {kind} on {step.target}"
        else:
            direction = direction_between(step.stand, step.target)
            intent = pb.Intent(place=pb.PlaceIntent(kind=kind, direction=direction))
            description = f"place {kind} on {step.target}"
        return Option(
            key=f"build_place:{kind}:{step.target[0]},{step.target[1]}",
            description=description,
            intent=intent,
        )


def build_brief_text(plan: BuildPlan) -> tuple[str, str]:
    """The instruction and success condition recorded in the stint trace."""
    instruction = f"Build {plan.describe()}."
    success = f"every planned tile carries a {plan.kind}"
    return (instruction, success)


def inventory_shortfall(plan: BuildPlan, inventory: Mapping[str, int]) -> int:
    """How many more pieces of `plan.kind` the builder needs for the whole plan."""
    return max(0, len(plan.tiles) - inventory.get(plan.kind, 0))


def supply_text(kind: str, count: int) -> str:
    """How to come by `count` more pieces of `kind`: the recipe, scaled.

    `"craft wood_wall 8 times (2 plank each, by hand): 16 plank in all"`.
    """
    recipe = items.RECIPES.get(kind)
    if recipe is None:
        return f"{kind} cannot be crafted"
    crafts = -(-count // recipe.output_count)
    totals = " + ".join(
        f"{amount * crafts} {name}" for name, amount in recipe.inputs.items()
    )
    where = f"at a {recipe.station}" if recipe.station else "by hand"
    each = f"{recipe.cost_text()} each" + (
        f", yields {recipe.output_count}" if recipe.output_count > 1 else ""
    )
    return f"craft {kind} {crafts} time{'s' if crafts != 1 else ''} ({each}, {where}): {totals} in all"


def missing_pieces_text(plan: BuildPlan, inventory: Mapping[str, int]) -> str:
    """The refusal for a build called with an empty pack: pieces and recipe."""
    needed = len(plan.tiles)
    return (
        f"build not started: you carry no {plan.kind} and the shape needs "
        f"{needed}. {supply_text(plan.kind, needed)}. Craft them, then call "
        f"build again."
    )
