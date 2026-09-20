#!/usr/bin/env python3
"""How far has the settlement got, and has it plateaued?

Reads a run recording (docs/07_replay.md) and reports the settlement's
build-out: a per-day timeline of placements and activity, what stands at the
last recorded tick, the enclosed rooms the standing walls and doors make, and
a headline with a plateau indicator.

The target this measures against is "a functioning settlement with roughly one
house or room per settler".

Usage:
    python tools/settlement_progress.py [run_dir] [--json]
                                        [--bucket-ticks N] [--viewer-url URL]

Works on a run that is still in progress: the gzip readers stop at a
truncated tail rather than raising (docs/07_replay.md, "Gzip JSONL writing").
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analyze_run import (  # noqa: E402
    CRAFTED_RE,
    DEFAULT_VIEWER_URL,
    RunLayout,
    first_existing,
    iter_jsonl,
    load_layout,
    resolve_run_dir,
)

# --------------------------------------------------------------------------
# what counts as what (docs/08_building.md, world/src/world/items.py)
# --------------------------------------------------------------------------

WALL_KINDS: frozenset[str] = frozenset({"wood_wall", "stone_wall"})
DOOR_KINDS: frozenset[str] = frozenset({"door"})
GROUND_KINDS: frozenset[str] = frozenset({"road", "wood_floor", "stone_floor"})
STATION_KINDS: frozenset[str] = frozenset({"workshop_table", "furnace", "anvil"})
FURNITURE_KINDS: frozenset[str] = frozenset({"bed", "chair", "table"})
FIXTURE_KINDS: frozenset[str] = frozenset({"chest", "message_board", "sign"})

# Every object kind this tool follows from tick 0 to the last tick. Natural
# objects and item piles are deliberately left out: the standing set is meant
# to be what the settlers built.
TRACKED_KINDS: frozenset[str] = (
    WALL_KINDS
    | DOOR_KINDS
    | GROUND_KINDS
    | STATION_KINDS
    | FURNITURE_KINDS
    | FIXTURE_KINDS
)

# Kinds whose first appearance counts as progress even when nothing is placed:
# a new tool tier or a new crafting station moves the settlement forward.
TIER_CRAFT_KINDS: frozenset[str] = (
    frozenset(
        {
            "axe",
            "pickaxe",
            "sword",
            "copper_ingot",
            "iron_ingot",
            "copper_axe",
            "copper_pickaxe",
            "iron_axe",
            "iron_pickaxe",
            "iron_sword",
        }
    )
    | STATION_KINDS
)

# The column order of the structures table; anything else seen is appended.
KIND_ORDER: tuple[str, ...] = (
    "wood_wall",
    "stone_wall",
    "door",
    "wood_floor",
    "stone_floor",
    "road",
    "bed",
    "chair",
    "table",
    "workshop_table",
    "furnace",
    "anvil",
    "chest",
    "message_board",
    "sign",
)

# A flood fill larger than this is the outdoors, not a room.
MAX_ROOM_TILES = 100
# A wall cluster smaller than this is not an attempt at a room.
MIN_NEAR_ROOM_WALLS = 6
# Zoom level for the room deep links (docs/07_replay.md, "Deep links").
ROOM_ZOOM = 2.0

DEFAULT_DAY_LENGTH = 300


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Standing:
    """One built object that exists at some point in the run."""

    object_id: str
    kind: str
    x: int
    y: int
    owner: str
    placed_tick: int

    @property
    def tile(self) -> tuple[int, int]:
        return (self.x, self.y)


@dataclass
class Bucket:
    """One time slice of the run, by default one in-game day."""

    index: int
    start_tick: int
    end_tick: int
    placed: Counter[str] = field(default_factory=Counter)
    dismantled: Counter[str] = field(default_factory=Counter)
    crafts: Counter[str] = field(default_factory=Counter)
    deaths: int = 0
    wolf_kills: int = 0
    sleep_bed_ticks: int = 0
    sleep_ground_ticks: int = 0
    conversations: int = 0
    mean_food: float = 0.0
    mean_health: float = 0.0
    new_tiers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "start_tick": self.start_tick,
            "end_tick": self.end_tick,
            "placed": dict(self.placed),
            "dismantled": dict(self.dismantled),
            "crafts": dict(self.crafts),
            "deaths": self.deaths,
            "wolf_kills": self.wolf_kills,
            "sleep_bed_ticks": self.sleep_bed_ticks,
            "sleep_ground_ticks": self.sleep_ground_ticks,
            "conversations": self.conversations,
            "mean_food": round(self.mean_food, 1),
            "mean_health": round(self.mean_health, 1),
            "new_tiers": list(self.new_tiers),
        }


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


@dataclass
class Scan:
    """Everything one pass over the tick stream produces."""

    first_tick: int = 0
    last_tick: int = 0
    tick_count: int = 0
    buckets: list[Bucket] = field(default_factory=list)
    standing: dict[str, Standing] = field(default_factory=dict)
    settlers: set[str] = field(default_factory=set)
    sleep_on_bed: Counter[tuple[str, str]] = field(default_factory=Counter)
    totals_placed: Counter[str] = field(default_factory=Counter)
    totals_dismantled: Counter[str] = field(default_factory=Counter)
    totals_crafts: Counter[str] = field(default_factory=Counter)
    deaths: int = 0
    wolf_kills: int = 0
    conversations: int = 0


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------


def bbox_of(tiles: Iterable[tuple[int, int]]) -> tuple[int, int, int, int]:
    """(min_x, min_y, max_x, max_y) of a non-empty tile collection."""
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


# --------------------------------------------------------------------------
# reading the recording
# --------------------------------------------------------------------------


def initial_objects(path: Path | None) -> dict[str, Standing]:
    """The tracked built objects present at tick 0.

    ``world/objects.jsonl.gz`` holds every object the world started with,
    mostly trees and rocks; only the built kinds are kept.
    """
    standing: dict[str, Standing] = {}
    if path is None:
        return standing
    for record in iter_jsonl(path):
        kind = record.get("object_type", "")
        if kind not in TRACKED_KINDS:
            continue
        standing[record["object_id"]] = to_standing(record, tick=0)
    return standing


def to_standing(record: dict, tick: int) -> Standing:
    """Turn a recorded ObjectState into a :class:`Standing`."""
    position = record.get("position", {})
    state = record.get("state", {})
    return Standing(
        object_id=record.get("object_id", ""),
        kind=record.get("object_type", ""),
        x=int(position.get("x", 0)),
        y=int(position.get("y", 0)),
        owner=str(state.get("owner", "")),
        placed_tick=tick,
    )


def bucket_for(tick: int, first_tick: int, bucket_ticks: int) -> int:
    return (tick - first_tick) // bucket_ticks


def craft_kind(details: str) -> str:
    """The item kind named by a successful craft action's details."""
    match = CRAFTED_RE.search(details)
    return match.group(1) if match else details or "?"


def dismantled_kind(details: str) -> str:
    """The object kind named by a successful dismantle, or "" if not one."""
    match = DISMANTLED_RE.search(details)
    return match.group(1) if match else ""


def scan_ticks(path: Path, start: dict[str, Standing], bucket_ticks: int) -> Scan:
    """Walk the tick stream once, filling buckets and the standing set."""
    scan = Scan(standing=dict(start))
    buckets: dict[int, Bucket] = {}
    first_seen = False
    seen_tiers: set[str] = set()
    wolves: set[str] = set()
    last_stats: dict[int, tuple[float, float]] = {}

    for record in iter_jsonl(path):
        if record.get("type") != "tick":
            continue
        tick = int(record.get("tick_id", 0))
        if not first_seen:
            scan.first_tick = tick
            first_seen = True
        scan.last_tick = max(scan.last_tick, tick)
        scan.tick_count += 1

        index = bucket_for(tick, scan.first_tick, bucket_ticks)
        bucket = buckets.get(index)
        if bucket is None:
            bucket = Bucket(index=index, start_tick=tick, end_tick=tick)
            buckets[index] = bucket
        bucket.end_tick = max(bucket.end_tick, tick)

        _scan_entities(record, scan, bucket, wolves, last_stats, index)
        _scan_objects(record, scan, bucket, tick)
        _scan_actions(record, scan, bucket, seen_tiers)

    scan.buckets = [buckets[i] for i in sorted(buckets)]
    for bucket in scan.buckets:
        food, health = last_stats.get(bucket.index, (0.0, 0.0))
        bucket.mean_food = food
        bucket.mean_health = health
    return scan


def _scan_entities(
    record: dict,
    scan: Scan,
    bucket: Bucket,
    wolves: set[str],
    last_stats: dict[int, tuple[float, float]],
    index: int,
) -> None:
    """Settler roster, sleep ticks, deaths, wolf kills and the stats line."""
    foods: list[float] = []
    healths: list[float] = []
    for update in record.get("entity_updates", ()):
        entity_id = update.get("entity_id", "")
        if update.get("entity_type") == "wolf":
            wolves.add(entity_id)
            continue
        if update.get("entity_type") != "player":
            continue
        scan.settlers.add(entity_id)
        if update.get("alive", True):
            # Runs recorded before 2026-09-18 call the food stat `hunger`
            # (docs/07_replay.md, "world/ticks.jsonl.gz records").
            foods.append(float(update.get("food", update.get("hunger", 0))))
            healths.append(float(update.get("health", 0)))
        if not update.get("asleep"):
            continue
        bed = str(update.get("sleeping_on", ""))
        if bed:
            bucket.sleep_bed_ticks += 1
            scan.sleep_on_bed[(entity_id, bed)] += 1
        else:
            bucket.sleep_ground_ticks += 1
    if foods:
        last_stats[index] = (
            sum(foods) / len(foods),
            sum(healths) / len(healths),
        )

    for death in record.get("deaths", ()):
        if death.get("entity_id", "") in wolves:
            continue
        bucket.deaths += 1
        scan.deaths += 1
    for despawn in record.get("entities_despawned", ()):
        entity_id = despawn.get("entity_id", "")
        if despawn.get("reason") != "killed":
            continue
        if entity_id in wolves or entity_id.startswith("wolf"):
            bucket.wolf_kills += 1
            scan.wolf_kills += 1


def _scan_objects(record: dict, scan: Scan, bucket: Bucket, tick: int) -> None:
    """Placements, removals and conversations from the object deltas."""
    for added in record.get("objects_added", ()):
        kind = added.get("object_type", "")
        if kind == "conversation":
            bucket.conversations += 1
            scan.conversations += 1
            continue
        if kind not in TRACKED_KINDS:
            continue
        scan.standing[added["object_id"]] = to_standing(added, tick)
        bucket.placed[kind] += 1
        scan.totals_placed[kind] += 1
    for object_id in record.get("objects_removed", ()):
        gone = scan.standing.pop(object_id, None)
        if gone is not None:
            bucket.dismantled[gone.kind] += 1
            scan.totals_dismantled[gone.kind] += 1


def _scan_actions(
    record: dict, scan: Scan, bucket: Bucket, seen_tiers: set[str]
) -> None:
    """Crafts, and the first appearance of each tool tier or station."""
    for action in record.get("actions", ()):
        if not action.get("success"):
            continue
        if action.get("action_type") != "craft":
            continue
        kind = craft_kind(action.get("details", ""))
        bucket.crafts[kind] += 1
        scan.totals_crafts[kind] += 1
        if kind in TIER_CRAFT_KINDS and kind not in seen_tiers:
            seen_tiers.add(kind)
            bucket.new_tiers.append(kind)


# --------------------------------------------------------------------------
# rooms
# --------------------------------------------------------------------------
#
# Connectivity: the world moves entities 8-connected but refuses a diagonal
# step unless both of its orthogonal components are passable
# (world/movement.py, "diagonal blocking rule"). Whenever a diagonal step is
# legal the two orthogonal steps are legal too, so reachability under the
# game's rule is exactly 4-connected reachability. The flood fill is therefore
# 4-connected, and a ring of walls touching only at the corners still leaks --
# correctly, because a settler cannot slip through that corner either.


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
        if o.kind in (FURNITURE_KINDS | STATION_KINDS | FIXTURE_KINDS)
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


# --------------------------------------------------------------------------
# headline
# --------------------------------------------------------------------------


def plateau_buckets(buckets: list[Bucket]) -> int:
    """Buckets since the last one that placed a structure or hit a new tier."""
    if not buckets:
        return 0
    last_progress = -1
    for position, bucket in enumerate(buckets):
        if sum(bucket.placed.values()) > 0 or bucket.new_tiers:
            last_progress = position
    if last_progress < 0:
        return len(buckets)
    return len(buckets) - 1 - last_progress


def room_link(
    viewer_url: str, run_id: str, tick: int, bbox: tuple[int, int, int, int]
) -> str:
    """A viewer deep link centred on a bounding box (docs/07_replay.md)."""
    min_x, min_y, max_x, max_y = bbox
    x = (min_x + max_x) // 2
    y = (min_y + max_y) // 2
    return f"{viewer_url}/?run={run_id}&tick={tick}&x={x}&y={y}&zoom={ROOM_ZOOM}"


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def kind_columns(counts: Iterable[Counter[str]]) -> list[str]:
    """The kinds actually seen, in the canonical order, extras appended."""
    seen: set[str] = set()
    for counter in counts:
        seen |= {kind for kind, n in counter.items() if n}
    ordered = [kind for kind in KIND_ORDER if kind in seen]
    return ordered + sorted(seen - set(ordered))


def short_kind(kind: str) -> str:
    """A <=6 character column header for a kind."""
    abbreviations = {
        "wood_wall": "wWall",
        "stone_wall": "sWall",
        "wood_floor": "wFlr",
        "stone_floor": "sFlr",
        "workshop_table": "wshop",
        "message_board": "board",
    }
    return abbreviations.get(kind, kind[:6])


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    print("  ".join(h.rjust(widths[i]) for i, h in enumerate(headers)))
    for row in rows:
        print("  ".join(cell.rjust(widths[i]) for i, cell in enumerate(row)))


def print_timeline(scan: Scan, bucket_ticks: int) -> None:
    """Two tables: cumulative structures, then per-bucket activity."""
    columns = kind_columns(b.placed for b in scan.buckets)
    print(f"\nStructures placed, cumulative (bucket = {bucket_ticks} ticks)")
    if not columns:
        print("  nothing was ever placed")
    else:
        running: Counter[str] = Counter()
        rows = []
        for bucket in scan.buckets:
            running += bucket.placed
            rows.append(
                [str(bucket.index), f"{bucket.start_tick}-{bucket.end_tick}"]
                + [str(running[kind]) for kind in columns]
            )
        print_table(["bkt", "ticks"] + [short_kind(kind) for kind in columns], rows)

    print("\nActivity per bucket")
    rows = []
    for bucket in scan.buckets:
        rows.append(
            [
                str(bucket.index),
                str(sum(bucket.placed.values())),
                str(sum(bucket.dismantled.values())),
                str(sum(bucket.crafts.values())),
                str(bucket.deaths),
                str(bucket.wolf_kills),
                str(bucket.sleep_bed_ticks),
                str(bucket.sleep_ground_ticks),
                str(bucket.conversations),
                f"{bucket.mean_food:.0f}",
                f"{bucket.mean_health:.0f}",
                ",".join(bucket.new_tiers) or "-",
            ]
        )
    print_table(
        [
            "bkt",
            "placed",
            "dismtl",
            "crafts",
            "deaths",
            "wolves",
            "slp_bed",
            "slp_gnd",
            "convs",
            "food",
            "hp",
            "new tier/station",
        ],
        rows,
    )


def print_report(
    scan: Scan,
    rooms: list[Room],
    near: list[NearRoom],
    layout: RunLayout,
    bucket_ticks: int,
    viewer_url: str,
) -> None:
    run_id = layout.run_id
    tick = scan.last_tick
    settlers = len(scan.settlers)
    with_bed = sum(1 for room in rooms if room.beds)
    owners = {room.sleeper for room in rooms if room.sleeper}
    stalled = plateau_buckets(scan.buckets)

    print(f"Settlement progress: {run_id}")
    print(f"  ticks {scan.first_tick}-{scan.last_tick} ({scan.tick_count} recorded)")
    print(
        f"  rooms: {len(rooms)} (with bed: {with_bed}, distinct owners: "
        f"{len(owners)}) / settlers: {settlers}"
    )
    print(
        f"  near-rooms: {len(near)}   plateau: {stalled} bucket(s) since the last "
        "new structure or tier"
    )

    print_timeline(scan, bucket_ticks)

    print("\nStanding at the last recorded tick")
    counts = Counter(o.kind for o in scan.standing.values())
    if not counts:
        print("  nothing built")
    else:
        for kind in kind_columns([counts]):
            print(f"  {kind:<16} {counts[kind]:>4}")
        built = [o.tile for o in scan.standing.values()]
        min_x, min_y, max_x, max_y = bbox_of(built)
        print(
            f"  bounding box      ({min_x}, {min_y}) - ({max_x}, {max_y}) "
            f"= {max_x - min_x + 1} x {max_y - min_y + 1}"
        )

    print("\nRooms")
    if not rooms:
        print("  none: no standing walls enclose a region")
    for room in rooms:
        min_x, min_y, max_x, max_y = room.bbox
        bits = [
            f"room {room.index}: ({min_x}, {min_y})-({max_x}, {max_y})",
            f"{len(room.tiles)} tiles",
            "sealed" if room.sealed else f"{len(room.door_tiles)} door(s)",
            f"{len(room.beds)} bed(s)",
            f"floor {room.floor_tiles}/{len(room.tiles)}",
        ]
        other = {k: v for k, v in room.furniture.items() if k != "bed"}
        if other:
            bits.append(", ".join(f"{k} x{v}" for k, v in sorted(other.items())))
        if room.sleeper:
            bits.append(f"slept in by {room.sleeper} ({room.sleeper_ticks} ticks)")
        if room.builder:
            bits.append(f"walls mostly {room.builder} ({room.builder_walls})")
        print("  " + " | ".join(bits))
        print("    " + room_link(viewer_url, run_id, tick, room.bbox))

    print("\nNear-rooms (wall clusters that enclose nothing)")
    if not near:
        print("  none")
    for cluster in near:
        min_x, min_y, max_x, max_y = cluster.bbox
        print(
            f"  cluster {cluster.index}: {len(cluster.tiles)} pieces, "
            f"({min_x}, {min_y})-({max_x}, {max_y}), "
            f"{cluster.single_tile_gaps} one-tile gap(s)"
        )
        print("    " + room_link(viewer_url, run_id, tick, cluster.bbox))


def report_dict(
    scan: Scan,
    rooms: list[Room],
    near: list[NearRoom],
    layout: RunLayout,
    bucket_ticks: int,
    viewer_url: str,
) -> dict:
    counts = Counter(o.kind for o in scan.standing.values())
    built = [o.tile for o in scan.standing.values()]
    min_x, min_y, max_x, max_y = bbox_of(built)
    return {
        "run_id": layout.run_id,
        "bucket_ticks": bucket_ticks,
        "first_tick": scan.first_tick,
        "last_tick": scan.last_tick,
        "ticks_recorded": scan.tick_count,
        "settlers": sorted(scan.settlers),
        "headline": {
            "rooms": len(rooms),
            "rooms_with_bed": sum(1 for room in rooms if room.beds),
            "distinct_owners": len({r.sleeper for r in rooms if r.sleeper}),
            "settlers": len(scan.settlers),
            "near_rooms": len(near),
            "buckets_since_progress": plateau_buckets(scan.buckets),
        },
        "totals": {
            "placed": dict(scan.totals_placed),
            "dismantled": dict(scan.totals_dismantled),
            "crafts": dict(scan.totals_crafts),
            "deaths": scan.deaths,
            "wolf_kills": scan.wolf_kills,
            "conversations": scan.conversations,
        },
        "buckets": [bucket.as_dict() for bucket in scan.buckets],
        "standing": {
            "counts": dict(counts),
            "bbox": {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y},
        },
        "rooms": [
            dict(
                room.as_dict(),
                link=room_link(viewer_url, layout.run_id, scan.last_tick, room.bbox),
            )
            for room in rooms
        ],
        "near_rooms": [
            dict(
                cluster.as_dict(),
                link=room_link(viewer_url, layout.run_id, scan.last_tick, cluster.bbox),
            )
            for cluster in near
        ],
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def analyse(
    run_dir: Path, bucket_ticks: int
) -> tuple[Scan, list[Room], list[NearRoom], RunLayout, int]:
    """Read a run directory and derive everything the report needs."""
    layout = load_layout(run_dir)
    if layout.ticks_path is None:
        raise FileNotFoundError(
            f"{run_dir} has no world/ticks.jsonl.gz; this tool needs a run "
            "directory recorded by the world server (docs/07_replay.md)"
        )
    if bucket_ticks <= 0:
        bucket_ticks = int(layout.meta.get("day_length_ticks", DEFAULT_DAY_LENGTH))
    objects_path = first_existing(
        run_dir / "world" / "objects.jsonl.gz",
        run_dir / "world" / "objects.jsonl",
    )
    scan = scan_ticks(layout.ticks_path, initial_objects(objects_path), bucket_ticks)
    rooms, near = find_rooms(scan.standing, scan.sleep_on_bed)
    return (scan, rooms, near, layout, bucket_ticks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "run_dir", nargs="?", help="run directory (default runs/latest)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable dump")
    parser.add_argument(
        "--bucket-ticks",
        type=int,
        default=0,
        help="timeline bucket size; default is the run's day length",
    )
    parser.add_argument("--viewer-url", default=DEFAULT_VIEWER_URL)
    args = parser.parse_args(argv)

    run_dir = resolve_run_dir(args.run_dir)
    if not run_dir.exists():
        print(f"no such run directory: {run_dir}", file=sys.stderr)
        return 1

    scan, rooms, near, layout, bucket_ticks = analyse(run_dir, args.bucket_ticks)
    if args.json:
        print(
            json.dumps(
                report_dict(scan, rooms, near, layout, bucket_ticks, args.viewer_url),
                indent=2,
            )
        )
    else:
        print_report(scan, rooms, near, layout, bucket_ticks, args.viewer_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
