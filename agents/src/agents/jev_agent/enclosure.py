"""What the walls around a settler enclose, and what that leaves reachable.

Three callers share this arithmetic:

- `build.py` refuses a placement that would shut the builder into a pocket.
- `options.py` refuses the same placement when Jev picks it (a settler once
  walled the six free neighbours of her own tile and starved in the cell).
- `planner.py` and `jevstate.py` state, as a fact, that the body can only
  reach a handful of tiles, and what the standing pieces around it are.

The world moves entities 8-connected but refuses a diagonal step unless both
its orthogonal components are passable, so reachability under the game's rule
is exactly 4-connected reachability. The room fill here is therefore
4-connected and a ring touching only at the corners still leaks - correctly,
because a settler cannot slip through that corner either. The *seal* check
keeps using `legal_directions`, which applies the same rule step by step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from . import items
from .geometry import Coord, ORDERED_DIRECTIONS, direction_name, offset
from .pathfinding import legal_directions
from .worldmodel import ObjectInfo, WorldModel

# The seal check counts free tiles reachable from where the builder would
# stand; anything below the cap is a pocket it should not shut itself into.
SEAL_MIN_FREE_TILES = 64
SEAL_MAX_FREE_TILES = 200

# Reaching fewer than this many tiles is stated as a fact to the planner and to
# Jev. A real settlement pocket is one to a few tiles; a doorway or a jetty is
# not, because the open ground beyond it counts.
ENCLOSED_REACH_LIMIT = 12

# A 4-connected fill larger than this is the outdoors, not a room.
MAX_ROOM_TILES = 400
# Wall pieces walked over when growing the cluster a build belongs to.
MAX_CLUSTER_TILES = 400
# How many gap or refused tiles a line names before it stops.
TILES_NAMED = 4
# How many of the actor's own clusters `look` lists.
OWN_PIECE_CLUSTERS_SHOWN = 6

WALL_KINDS: frozenset[str] = items.BLOCKING_OBJECT_TYPES
# A ring is closed by walls and doors alike; a door is an entrance, not a gap.
DOOR_KINDS: frozenset[str] = frozenset({items.DOOR})


# --- the seal check ---------------------------------------------------------


class BlockedView:
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


def reachable_within(view: BlockedView, start: Coord, cap: int) -> int:
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


def free_tile_cap(plan_size: int = 0) -> int:
    """How much open ground counts as "not shut in" for a plan of this size."""
    wanted = max(SEAL_MIN_FREE_TILES, 2 * plan_size)
    return min(wanted, SEAL_MAX_FREE_TILES)


def would_seal(
    model: WorldModel, stand: Coord, target: Coord, cap: int = SEAL_MIN_FREE_TILES
) -> bool:
    """Whether a blocking piece on `target` would shut an actor on `stand` in.

    No anchor, no map knowledge: flood fill from where the actor would be,
    with the new wall in place, and stop as soon as enough free tiles have
    been counted. A fill that runs out early means a pocket.
    """
    view = BlockedView(model, frozenset({target}))
    return reachable_within(view, stand, cap) < cap


def blocks_movement(kind: str) -> bool:
    """Whether a placed `kind` makes its tile impassable to settlers.

    A door is passable to settlers (it only stops wolves), so placing one is
    never a seal.
    """
    return kind in WALL_KINDS


# --- the enclosed fact ------------------------------------------------------


def reach_count(model: WorldModel, cap: int = ENCLOSED_REACH_LIMIT) -> int:
    """How many tiles the actor can reach from where it stands, capped."""
    return reachable_within(BlockedView(model, frozenset()), model.position, cap)


def adjacent_piece_labels(model: WorldModel) -> list[str]:
    """`"wood_wall_31 (N)"` for every placed piece on a neighbouring tile."""
    labels: list[str] = []
    position = model.position
    for direction in ORDERED_DIRECTIONS:
        tile = offset(position, direction)
        for obj in model.structure_objects_at(tile):
            labels.append(f"{obj.object_id} ({direction_name(direction)})")
    return labels


def enclosed_fact(model: WorldModel, cap: int = ENCLOSED_REACH_LIMIT) -> str:
    """One `!!` line when the body is shut into a pocket, else `""`.

    Physics and inventory of the situation only: how many tiles are reachable,
    which pieces are next to the body, and what `dismantle` does.
    """
    info = model.self_info
    if not info.alive or info.asleep:
        return ""
    reach = reach_count(model, cap)
    if reach >= cap:
        return ""
    pieces = ", ".join(adjacent_piece_labels(model)[:8]) or "none"
    return (
        f"!! ENCLOSED: you can reach only {reach} tile(s); the pieces around "
        f"you: {pieces}. dismantle removes a placed piece "
        f"({items.DISMANTLE_WORK} extract actions, one item back)."
    )


# --- rooms ------------------------------------------------------------------


@dataclass(frozen=True)
class Room:
    """A region of free tiles fully enclosed by standing walls and doors."""

    tiles: frozenset[Coord]
    doors: int

    @property
    def bounds(self) -> tuple[Coord, Coord]:
        """The inclusive bounding box of the interior."""
        xs = [tile[0] for tile in self.tiles]
        ys = [tile[1] for tile in self.tiles]
        return ((min(xs), min(ys)), (max(xs), max(ys)))

    @property
    def span(self) -> str:
        """`"3x2"`: the interior's width and height."""
        (left, top), (right, bottom) = self.bounds
        return f"{right - left + 1}x{bottom - top + 1}"


def boundary_tiles(model: WorldModel) -> tuple[set[Coord], set[Coord]]:
    """Every wall tile and every door tile the actor has ever seen."""
    walls: set[Coord] = set()
    doors: set[Coord] = set()
    for obj in model.objects.values():
        if obj.object_type in WALL_KINDS:
            walls.add(obj.position)
        elif obj.object_type in DOOR_KINDS:
            doors.add(obj.position)
    return (walls, doors)


def _neighbours4(tile: Coord) -> tuple[Coord, ...]:
    x, y = tile
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def _neighbours8(tile: Coord) -> tuple[Coord, ...]:
    x, y = tile
    return tuple(
        (x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)
    )


def _bbox(tiles: Iterable[Coord]) -> tuple[int, int, int, int]:
    xs = [tile[0] for tile in tiles]
    ys = [tile[1] for tile in tiles]
    return (min(xs), min(ys), max(xs), max(ys))


def _cluster_of(boundary: set[Coord], seeds: Iterable[Coord]) -> set[Coord]:
    """The 8-connected boundary cluster(s) the seed tiles belong to, capped."""
    cluster: set[Coord] = set()
    stack = [seed for seed in seeds if seed in boundary]
    while stack and len(cluster) < MAX_CLUSTER_TILES:
        tile = stack.pop()
        if tile in cluster:
            continue
        cluster.add(tile)
        stack.extend(n for n in _neighbours8(tile) if n in boundary)
    return cluster


def _fill(
    start: Coord, boundary: set[Coord], box: tuple[int, int, int, int]
) -> tuple[set[Coord], bool]:
    """4-connected fill from `start`; `escaped` means "not a room"."""
    min_x, min_y, max_x, max_y = box
    region: set[Coord] = {start}
    stack = [start]
    escaped = False
    while stack:
        x, y = stack.pop()
        if x <= min_x or x >= max_x or y <= min_y or y >= max_y:
            escaped = True
        if len(region) > MAX_ROOM_TILES:
            return (region, True)
        for neighbour in _neighbours4((x, y)):
            nx, ny = neighbour
            if not (min_x <= nx <= max_x and min_y <= ny <= max_y):
                escaped = True
                continue
            if neighbour in boundary or neighbour in region:
                continue
            region.add(neighbour)
            stack.append(neighbour)
    return (region, escaped)


def rooms_around(model: WorldModel, seeds: Sequence[Coord]) -> list[Room]:
    """Every enclosed region bounded by the wall cluster the seeds belong to.

    The seeds are the tiles a build worked on: the cluster is grown from the
    ones that now carry a wall or a door, so a line that closes somebody
    else's ring still finds that ring's interior.
    """
    walls, doors = boundary_tiles(model)
    boundary = walls | doors
    cluster = _cluster_of(boundary, seeds)
    if not cluster:
        return []
    min_x, min_y, max_x, max_y = _bbox(cluster)
    box = (min_x - 1, min_y - 1, max_x + 1, max_y + 1)

    seen: set[Coord] = set()
    rooms: list[Room] = []
    for y in range(box[1], box[3] + 1):
        for x in range(box[0], box[2] + 1):
            tile = (x, y)
            if tile in boundary or tile in seen:
                continue
            region, escaped = _fill(tile, boundary, box)
            seen |= region
            if escaped:
                continue
            touching = {n for t in region for n in _neighbours4(t)} & boundary
            rooms.append(Room(tiles=frozenset(region), doors=len(touching & doors)))
    return rooms


def room_containing(rooms: Sequence[Room], tile: Coord) -> Room | None:
    """The room whose interior holds `tile`, or None."""
    for room in rooms:
        if tile in room.tiles:
            return room
    return None


# --- what a build now stands as ---------------------------------------------


def open_plan_tiles(model: WorldModel, plan_tiles: Sequence[Coord]) -> list[Coord]:
    """Planned tiles that carry no wall and no door: the gaps in the shape."""
    walls, doors = boundary_tiles(model)
    boundary = walls | doors
    return [tile for tile in plan_tiles if tile not in boundary]


def _tile_list(tiles: Sequence[Coord]) -> str:
    named = ", ".join(f"({x}, {y})" for x, y in tiles[:TILES_NAMED])
    if len(tiles) > TILES_NAMED:
        return f"{named}, ..."
    return named


def build_geometry_lines(
    model: WorldModel,
    plan_tiles: Sequence[Coord],
    *,
    refused: Sequence[Coord] = (),
    stood_outside: bool = False,
) -> list[str]:
    """What the standing pieces around a finished build now form.

    Facts computed from the world model: the interior the walls enclose, how
    many doors and gaps it has, and which side of it the body is on. Nothing
    about what to do next.
    """
    lines: list[str] = []
    gaps = open_plan_tiles(model, plan_tiles)
    rooms = rooms_around(model, plan_tiles)
    if not rooms:
        if gaps:
            lines.append(
                f"  these walls enclose nothing yet: {len(gaps)} gap(s) remain "
                f"at {_tile_list(gaps)}"
            )
        else:
            lines.append("  these walls enclose nothing: the shape is not a ring")
    else:
        room = max(rooms, key=lambda r: len(r.tiles))
        inside = model.position in room.tiles
        lines.append(
            f"  the walls here now enclose {len(room.tiles)} interior tile(s) "
            f"spanning {room.span}; doors: {room.doors}; gaps: {len(gaps)}; "
            f"you are {'inside' if inside else 'outside'}"
        )
        if room.doors == 0 and not gaps:
            entrance = "the interior has no entrance: no door and no gap"
            if stood_outside and not inside:
                entrance += (
                    "; the last piece was placed from outside, because placing "
                    "it from inside would have shut you in"
                )
            lines.append(f"  {entrance}")
    if refused:
        lines.append(
            f"  refused as sealing you in: {_tile_list(list(refused))}; placing "
            f"there would have left you fewer than {SEAL_MIN_FREE_TILES} "
            "reachable tiles"
        )
    return lines


# --- the actor's own standing pieces ----------------------------------------


def own_pieces(model: WorldModel) -> list[ObjectInfo]:
    """Every placed building object whose owner is this actor."""
    return [
        obj
        for obj in model.objects.values()
        if obj.owner == model.entity_id and obj.object_type in items.BUILDING_KINDS
    ]


def _cluster_objects(pieces: Sequence[ObjectInfo]) -> list[list[ObjectInfo]]:
    """Group pieces into 8-connected clusters, largest first."""
    by_tile: dict[Coord, list[ObjectInfo]] = {}
    for piece in pieces:
        by_tile.setdefault(piece.position, []).append(piece)
    unvisited = set(by_tile)
    clusters: list[list[ObjectInfo]] = []
    while unvisited:
        start = unvisited.pop()
        tiles = {start}
        stack = [start]
        while stack:
            tile = stack.pop()
            for neighbour in _neighbours8(tile):
                if neighbour in unvisited:
                    unvisited.remove(neighbour)
                    tiles.add(neighbour)
                    stack.append(neighbour)
        clusters.append([obj for tile in tiles for obj in by_tile[tile]])
    clusters.sort(key=len, reverse=True)
    return clusters


def _cluster_text(cluster: Sequence[ObjectInfo]) -> str:
    counts: dict[str, int] = {}
    for piece in cluster:
        counts[piece.object_type] = counts.get(piece.object_type, 0) + 1
    what = " + ".join(
        f"{count} {kind}" for kind, count in sorted(counts.items(), key=lambda p: -p[1])
    )
    tiles = [piece.position for piece in cluster]
    left, top, right, bottom = _bbox(tiles)
    if (left, top) == (right, bottom):
        return f"{what} at ({left}, {top})"
    return f"{what} within ({left}, {top})-({right}, {bottom})"


def own_pieces_line(model: WorldModel, limit: int = OWN_PIECE_CLUSTERS_SHOWN) -> str:
    """`"your placed pieces: 8 wood_wall within (…)-(…); 1 bed at (…)"`.

    The world model's last sighting of each piece, so it survives the nightly
    history reset through the journal's copy of `look`.
    """
    pieces = own_pieces(model)
    if not pieces:
        return ""
    clusters = _cluster_objects(pieces)
    shown = [_cluster_text(cluster) for cluster in clusters[:limit]]
    rest = len(clusters) - len(shown)
    if rest > 0:
        shown.append(f"and {rest} more group(s)")
    return "your placed pieces: " + "; ".join(shown)
