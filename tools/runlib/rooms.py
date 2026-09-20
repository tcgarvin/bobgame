"""Enclosed rooms and unclosed wall clusters from a run's standing objects.

Connectivity: the world moves entities 8-connected but refuses a diagonal
step unless both of its orthogonal components are passable
(world/movement.py, "diagonal blocking rule"). Whenever a diagonal step is
legal the two orthogonal steps are legal too, so reachability under the
game's rule is exactly 4-connected reachability. The flood fill is therefore
4-connected, and a ring of walls touching only at the corners still leaks --
correctly, because a settler cannot slip through that corner either.

This is the same idea as ``agents/src/agents/jev_agent/enclosure.py``, which
the settlers themselves use; the tools package cannot import the agents
package, so the fill is written out a second time here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .worldscan import (
    DOOR_KINDS,
    FIXTURE_KINDS,
    FURNITURE_KINDS,
    GROUND_KINDS,
    BUILD_STATION_KINDS,
    WALL_KINDS,
    Standing,
)

# A flood fill larger than this is the outdoors, not a room.
MAX_ROOM_TILES = 100
# A wall cluster smaller than this is not an attempt at a room.
MIN_NEAR_ROOM_WALLS = 6


def bbox_of(tiles: Iterable[tuple[int, int]]) -> tuple[int, int, int, int]:
    """(min_x, min_y, max_x, max_y) of a tile collection, zeros when empty."""
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    if not xs:
        return (0, 0, 0, 0)
    return (min(xs), min(ys), max(xs), max(ys))


def neighbours4(tile: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    x, y = tile
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))


def neighbours8(tile: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    x, y = tile
    return tuple(
        (x + dx, y + dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if not (dx == 0 and dy == 0)
    )


@dataclass
class Room:
    """An enclosed region bounded entirely by standing walls and doors."""

    index: int
    tiles: list[tuple[int, int]]
    door_tiles: list[tuple[int, int]]
    wall_tiles: list[tuple[int, int]]
    beds: list[Standing]
    furniture: Counter[str]
    floor_tiles: int
    sleeper: str
    sleeper_ticks: int
    builder: str
    builder_walls: int

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return bbox_of(self.tiles)

    @property
    def sealed(self) -> bool:
        return not self.door_tiles

    def as_dict(self) -> dict:
        min_x, min_y, max_x, max_y = self.bbox
        return {
            "index": self.index,
            "bbox": {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
            "interior_tiles": len(self.tiles),
            "doors": len(self.door_tiles),
            "sealed": self.sealed,
            "wall_pieces": len(self.wall_tiles),
            "beds": [bed.object_id for bed in self.beds],
            "bed_owners": sorted({bed.owner for bed in self.beds if bed.owner}),
            "furniture": dict(self.furniture),
            "floor_tiles": self.floor_tiles,
            "floor_coverage": round(self.floor_tiles / max(1, len(self.tiles)), 2),
            "sleeper": self.sleeper,
            "sleeper_ticks": self.sleeper_ticks,
            "builder": self.builder,
            "builder_walls": self.builder_walls,
        }


@dataclass
class NearRoom:
    """A wall cluster big enough to be an attempt at a room, but not closed."""

    index: int
    tiles: list[tuple[int, int]]
    single_tile_gaps: int

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return bbox_of(self.tiles)

    def as_dict(self) -> dict:
        min_x, min_y, max_x, max_y = self.bbox
        return {
            "index": self.index,
            "bbox": {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
            "pieces": len(self.tiles),
            "single_tile_gaps": self.single_tile_gaps,
        }


def find_rooms(
    standing: dict[str, Standing], sleep_on_bed: Counter[tuple[str, str]]
) -> tuple[list[Room], list[NearRoom]]:
    """Enclosed regions and unclosed wall clusters from the standing set."""
    walls = {o.tile for o in standing.values() if o.kind in WALL_KINDS}
    doors = {o.tile for o in standing.values() if o.kind in DOOR_KINDS}
    boundary = walls | doors
    if not boundary:
        return ([], [])

    rooms = _flood_rooms(boundary, walls, doors, standing, sleep_on_bed)
    enclosing = {tile for room in rooms for tile in room.wall_tiles}
    near = _near_rooms(walls, boundary, enclosing)
    return (rooms, near)


def _flood_rooms(
    boundary: set[tuple[int, int]],
    walls: set[tuple[int, int]],
    doors: set[tuple[int, int]],
    standing: dict[str, Standing],
    sleep_on_bed: Counter[tuple[str, str]],
) -> list[Room]:
    """Flood-fill every free tile inside the built area's bounding box."""
    min_x, min_y, max_x, max_y = bbox_of(boundary)
    min_x, min_y, max_x, max_y = min_x - 1, min_y - 1, max_x + 1, max_y + 1
    seen: set[tuple[int, int]] = set()
    rooms: list[Room] = []

    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            tile = (x, y)
            if tile in boundary or tile in seen:
                continue
            region, escaped = _fill(tile, boundary, (min_x, min_y, max_x, max_y))
            seen |= region
            if escaped:
                continue
            rooms.append(
                _describe_room(
                    len(rooms) + 1, region, walls, doors, standing, sleep_on_bed
                )
            )
    return rooms


def _fill(
    start: tuple[int, int],
    boundary: set[tuple[int, int]],
    box: tuple[int, int, int, int],
) -> tuple[set[tuple[int, int]], bool]:
    """4-connected fill from ``start``; ``escaped`` means "not a room".

    A fill escapes when it reaches the padded bounding box's edge (it is the
    outdoors) or when it grows past ``MAX_ROOM_TILES`` (too big to be a room).
    """
    min_x, min_y, max_x, max_y = box
    region: set[tuple[int, int]] = {start}
    stack = [start]
    escaped = False
    while stack:
        tile = stack.pop()
        x, y = tile
        if x <= min_x or x >= max_x or y <= min_y or y >= max_y:
            escaped = True
        if len(region) > MAX_ROOM_TILES:
            escaped = True
        for neighbour in neighbours4(tile):
            nx, ny = neighbour
            if not (min_x <= nx <= max_x and min_y <= ny <= max_y):
                escaped = True
                continue
            if neighbour in boundary or neighbour in region:
                continue
            region.add(neighbour)
            stack.append(neighbour)
    return (region, escaped)


def _describe_room(
    index: int,
    region: set[tuple[int, int]],
    walls: set[tuple[int, int]],
    doors: set[tuple[int, int]],
    standing: dict[str, Standing],
    sleep_on_bed: Counter[tuple[str, str]],
) -> Room:
    """Fill in the contents and the best-effort owner of one enclosed region."""
    touching = {n for tile in region for n in neighbours8(tile)}
    room_doors = sorted(touching & doors)
    room_walls = sorted(touching & walls)

    inside = [o for o in standing.values() if o.tile in region]
    beds = [o for o in inside if o.kind == "bed"]
    furniture = Counter(
        o.kind
        for o in inside
        if o.kind in (FURNITURE_KINDS | BUILD_STATION_KINDS | FIXTURE_KINDS)
    )
    floor_tiles = len({o.tile for o in inside if o.kind in GROUND_KINDS})

    bed_ids = {bed.object_id for bed in beds}
    sleepers: Counter[str] = Counter()
    for (entity_id, bed_id), ticks in sleep_on_bed.items():
        if bed_id in bed_ids:
            sleepers[entity_id] += ticks
    sleeper, sleeper_ticks = ("", 0)
    if sleepers:
        sleeper, sleeper_ticks = sleepers.most_common(1)[0]

    wall_owners = Counter(
        o.owner for o in standing.values() if o.tile in set(room_walls) and o.owner
    )
    builder, builder_walls = ("", 0)
    if wall_owners:
        builder, builder_walls = wall_owners.most_common(1)[0]

    return Room(
        index=index,
        tiles=sorted(region),
        door_tiles=room_doors,
        wall_tiles=room_walls,
        beds=beds,
        furniture=furniture,
        floor_tiles=floor_tiles,
        sleeper=sleeper,
        sleeper_ticks=sleeper_ticks,
        builder=builder,
        builder_walls=builder_walls,
    )


def _near_rooms(
    walls: set[tuple[int, int]],
    boundary: set[tuple[int, int]],
    enclosing: set[tuple[int, int]],
) -> list[NearRoom]:
    """8-connected wall clusters of >= 6 pieces that enclose nothing."""
    near: list[NearRoom] = []
    seen: set[tuple[int, int]] = set()
    for tile in sorted(walls):
        if tile in seen:
            continue
        cluster = _wall_cluster(tile, walls)
        seen |= cluster
        if len(cluster) < MIN_NEAR_ROOM_WALLS or cluster & enclosing:
            continue
        near.append(
            NearRoom(
                index=len(near) + 1,
                tiles=sorted(cluster),
                single_tile_gaps=_single_tile_gaps(cluster, boundary),
            )
        )
    return near


def _wall_cluster(
    start: tuple[int, int], walls: set[tuple[int, int]]
) -> set[tuple[int, int]]:
    cluster = {start}
    stack = [start]
    while stack:
        for neighbour in neighbours8(stack.pop()):
            if neighbour in walls and neighbour not in cluster:
                cluster.add(neighbour)
                stack.append(neighbour)
    return cluster


def _single_tile_gaps(
    cluster: set[tuple[int, int]], boundary: set[tuple[int, int]]
) -> int:
    """Free tiles that sit between two opposite pieces of the same cluster.

    A cheap stand-in for "how many pieces short is this of closing": a ring
    with one piece missing scores 1. It only sees one-tile holes in a straight
    run of wall, so a wider opening scores 0 and the number is a lower bound.
    """
    candidates = {n for tile in cluster for n in neighbours4(tile)} - boundary
    gaps = 0
    for x, y in candidates:
        horizontal = (x - 1, y) in cluster and (x + 1, y) in cluster
        vertical = (x, y - 1) in cluster and (x, y + 1) in cluster
        if horizontal or vertical:
            gaps += 1
    return gaps
