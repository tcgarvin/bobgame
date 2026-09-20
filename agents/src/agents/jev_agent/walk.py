"""One set of walking primitives, and the one code driver that walks.

Five places used to answer the same three questions for themselves — where can
I stand next to this, which way do I step, and am I there yet — each with its
own A* call and its own idea of what "there" means: the planner's `travel_to`,
its walks to a settler and to a conversation anchor, the `step_towards:` option
builders, the `build` executor and the conversation approach. They all call in
here now.

Nothing in this module submits anything or knows about briefs beyond the
`StintDriver` protocol it implements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .. import world_pb2 as pb
from .briefs import DriverChoice, Option
from .geometry import (
    NO_DIRECTION,
    ORDERED_DIRECTIONS,
    Coord,
    chebyshev,
    direction_between,
    direction_name,
    offset,
)
from .pathfinding import NO_PATH, find_path, legal_directions, path_length
from .worldmodel import WorldModel

# What `arrival` says. `arrived_next_to` is for a destination nobody can stand
# on: next to it is as close as a body gets.
ARRIVED = "arrived"
ARRIVED_NEXT_TO = "arrived_next_to"

# A walk has no tick budget of its own: it runs until it arrives, gives up
# (`no_path`), or is stopped by danger or hunger. The numbers below are only
# the backstop that keeps a hopeless walk from running forever. It is two ticks
# per step of the remembered path (detours, blocked tiles and fights all cost
# ticks) plus a fixed allowance, and it is never below `MIN_TICKS`. In the
# 2026-09-20 hamlet run one settler spent a 20-call budget and ~220 ticks on 16
# `travel_to` calls inside a 10-tile radius, each one re-called the moment its
# model-chosen `max_ticks` ran out.
TICKS_PER_STEP = 2
TICK_ALLOWANCE = 20
MIN_TICKS = 30
MAX_TICKS = 600


@dataclass(frozen=True)
class Step:
    """The first step of a route, and how much of the route is left.

    `direction` is `NO_DIRECTION` when there is no route at all, which is what
    `found` reports; `steps_left` counts the tiles still to walk, including
    this one.
    """

    direction: pb.Direction
    steps_left: int
    stop_adjacent: bool

    @property
    def found(self) -> bool:
        """Whether there is a step to take."""
        return self.direction != NO_DIRECTION


NO_STEP = Step(direction=NO_DIRECTION, steps_left=0, stop_adjacent=False)


def plan_step(
    model: WorldModel,
    start: Coord,
    target: Coord,
    *,
    stop_adjacent: bool = False,
    retry_adjacent: bool = False,
) -> Step:
    """The first step of the A* route from `start` to `target`.

    With `retry_adjacent`, a failed route to a tile the actor knows it cannot
    stand on is retried against the tiles beside it, and the returned `Step`
    says so. Without it, a failure is a failure: the caller decides.
    """
    path = find_path(model, start, target, stop_adjacent=stop_adjacent)
    if path:
        return Step(
            direction=direction_between(start, path[0]),
            steps_left=len(path),
            stop_adjacent=stop_adjacent,
        )
    if not retry_adjacent or stop_adjacent or model.is_walkable(target):
        return NO_STEP
    # The target tile cannot be stood on - a tree, a wall, a rock, another
    # settler - so the journey is to the tile beside it. Without this, 32 of
    # the 41 `no_path` walks in the 2026-09-20 run gave up 4 tiles short of a
    # destination with free neighbours all around it.
    path = find_path(model, start, target, stop_adjacent=True)
    if not path:
        return NO_STEP
    return Step(
        direction=direction_between(start, path[0]),
        steps_left=len(path),
        stop_adjacent=True,
    )


def greedy_step(model: WorldModel, position: Coord, target: Coord) -> pb.Direction:
    """The legal step that gets closest to `target`, or `NO_DIRECTION`.

    Used only when A* has failed: the actor may be standing at the edge of what
    it remembers, and walking hopefully into unknown ground is what turns a
    coordinate the planner named into a place the actor can reach.
    """
    best = NO_DIRECTION
    best_gap = chebyshev(position, target)
    for direction in legal_directions(model, position):
        gap = chebyshev(offset(position, direction), target)
        if gap < best_gap:
            best_gap = gap
            best = direction
    return best


def stand_candidates(
    model: WorldModel, target: Coord, *, known_only: bool = False
) -> list[Coord]:
    """Walkable tiles next to `target`, nearest to the actor first.

    `target` itself is excluded: these are the tiles you stand on to reach it,
    which is also what the world requires of a conversation seat. `known_only`
    keeps out tiles the actor has never observed, which is what the builder
    wants — it is about to place something and needs to know what is there.
    """
    tiles = [
        offset(target, direction)
        for direction in ORDERED_DIRECTIONS
        if model.is_walkable(offset(target, direction))
        and (not known_only or model.is_known(offset(target, direction)))
    ]
    tiles.sort(key=lambda tile: (chebyshev(tile, model.position), tile))
    return tiles


def arrival(model: WorldModel, target: Coord) -> str:
    """`arrived`, `arrived_next_to`, or `""` while the walk is still going.

    Standing on the destination is arrival. Standing next to it counts only
    when the tile itself cannot be stood on (water, a wall, a bush, another
    settler): walking onto it is then impossible and next to it is as close
    as the body gets.
    """
    position = model.position
    if position == target:
        return ARRIVED
    if chebyshev(position, target) <= 1 and not model.is_walkable(target):
        return ARRIVED_NEXT_TO
    return ""


def arrival_text(arrival_reason: str, position: Coord, target: Coord) -> str:
    """What a finished (or never-started) walk says about where the body is."""
    if arrival_reason == ARRIVED:
        return f"you are standing on {target}"
    return (
        f"{target} cannot be stood on; you are next to it at {position}, "
        "which is as close as a walk gets"
    )


def tick_budget(model: WorldModel, target: Coord) -> int:
    """Backstop tick budget for a walk from `model.position` to `target`.

    Uses the remembered path when there is one, and the straight-line distance
    when the map is not known well enough to find one.
    """
    steps = path_length(model, model.position, target)
    if steps == NO_PATH:
        steps = chebyshev(model.position, target)
    budget = steps * TICKS_PER_STEP + TICK_ALLOWANCE
    return min(MAX_TICKS, max(MIN_TICKS, budget))


class WalkDriver:
    """A `StintDriver` that walks the actor onto one of a set of target tiles.

    Choosing a free seat next to an anchor, or a tile beside a settler, is
    arithmetic, so code does the walking and Jev is not involved.
    """

    name = "approach"

    # Stint end reasons this driver produces.
    ARRIVED = "arrived"
    NO_PATH = "no_path"

    def __init__(self, targets: Sequence[Coord], label: str) -> None:
        self.targets = list(targets)
        self.label = label
        self._path: list[Coord] = []

    def stop_reason(self, model: WorldModel) -> str:
        """Arrived, blocked, or `""` to keep walking."""
        if model.position in self.targets:
            return self.ARRIVED
        self._path = self._best_path(model)
        if not self._path:
            return self.NO_PATH
        return ""

    def choose(self, model: WorldModel) -> DriverChoice:
        """Take the next step along the path found by `stop_reason`."""
        direction = direction_between(model.position, self._path[0])
        return DriverChoice(
            option=Option(
                key=f"approach_step:{direction_name(direction)}",
                description=f"walk toward {self.label}",
                intent=pb.Intent(move=pb.MoveIntent(direction=direction)),
            ),
            note=f"{len(self._path)} steps to {self.label}",
        )

    def summary(self) -> str:
        """One line of progress for the stint report."""
        return f"APPROACH {self.label}: {len(self._path)} steps left"

    def _best_path(self, model: WorldModel) -> list[Coord]:
        best: list[Coord] = []
        for target in self.targets:
            path = find_path(model, model.position, target)
            if path and (not best or len(path) < len(best)):
                best = path
        return best
