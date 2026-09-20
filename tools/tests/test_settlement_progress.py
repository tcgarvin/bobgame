"""Tests for tools/settlement_progress.py against synthetic run directories.

The fixtures write the real recording shapes from docs/07_replay.md:
meta.json, world/objects.jsonl.gz and world/ticks.jsonl.gz.
"""

from __future__ import annotations

import gzip
import json
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import settlement_progress as sp  # noqa: E402
from runlib import runio, worldscan  # noqa: E402

RUN_ID = "20260920-120000-settlement"
SETTLERS = ("ada", "bram")


# --------------------------------------------------------------------------
# fixture builders
# --------------------------------------------------------------------------


def write_gz_jsonl(path: Path, rows: list[dict], sync: bool = False) -> None:
    """Write gzip JSONL, optionally flushing after every line like the world."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb") as handle:
        for row in rows:
            handle.write((json.dumps(row) + "\n").encode("utf-8"))
            if sync:
                handle.flush(zlib.Z_SYNC_FLUSH)


def object_state(object_id: str, kind: str, x: int, y: int, owner: str = "") -> dict:
    return {
        "object_id": object_id,
        "position": {"x": x, "y": y},
        "object_type": kind,
        "state": {"owner": owner} if owner else {},
    }


def entity_update(
    entity_id: str,
    x: int = 0,
    y: int = 0,
    food: int = 50,
    health: int = 20,
    sleeping_on: str = "",
    asleep: bool = False,
) -> dict:
    return {
        "entity_id": entity_id,
        "position": {"x": x, "y": y},
        "entity_type": "player",
        "tags": [],
        "health": health,
        "max_health": 20,
        "food": food,
        "max_food": 100,
        "wielded": "",
        "alive": True,
        "fatigue": 1,
        "max_fatigue": 100,
        "asleep": asleep,
        "sleeping_on": sleeping_on,
        "collapsed": False,
        "inventory": {},
    }


def tick_record(tick_id: int, **extra) -> dict:
    record = {
        "type": "tick",
        "tick_id": tick_id,
        "clock": {
            "day": tick_id // 300,
            "tick_of_day": tick_id % 300,
            "day_length": 300,
            "night": False,
        },
        "moves": [],
        "entity_updates": [entity_update(name) for name in SETTLERS],
        "object_changes": [],
        "objects_added": [],
        "objects_removed": [],
        "actions": [],
        "utterances": [],
        "damage": [],
        "deaths": [],
        "respawns": [],
        "entities_spawned": [],
        "entities_despawned": [],
    }
    record.update(extra)
    return record


def make_run(tmp_path: Path, ticks: list[dict], sync: bool = False) -> Path:
    run_dir = tmp_path / RUN_ID
    (run_dir / "world").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "run_id": RUN_ID,
                "config_name": "settlement",
                "day_length_ticks": 300,
                "entities": [
                    {"entity_id": name, "entity_type": "player"} for name in SETTLERS
                ],
            }
        ),
        encoding="utf-8",
    )
    write_gz_jsonl(
        run_dir / "world" / "objects.jsonl.gz",
        [object_state("tree_0", "tree", 100, 100)],
    )
    write_gz_jsonl(run_dir / "world" / "ticks.jsonl.gz", ticks, sync=sync)
    return run_dir


def ring_tiles(min_x: int, min_y: int, size: int) -> list[tuple[int, int]]:
    """The perimeter tiles of a ``size`` x ``size`` box, clockwise-ish order."""
    max_x, max_y = min_x + size - 1, min_y + size - 1
    tiles = []
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            if x in (min_x, max_x) or y in (min_y, max_y):
                tiles.append((x, y))
    return tiles


def ring_objects(
    tiles: list[tuple[int, int]],
    prefix: str,
    owner: str,
    door_at: tuple[int, int] | None = None,
) -> list[dict]:
    objects = []
    for i, (x, y) in enumerate(tiles):
        kind = "door" if door_at == (x, y) else "wood_wall"
        objects.append(object_state(f"{prefix}_{i}", kind, x, y, owner=owner))
    return objects


def analyse(run_dir: Path, bucket_ticks: int = 0):
    return sp.analyse(run_dir, bucket_ticks)


# --------------------------------------------------------------------------
# rooms
# --------------------------------------------------------------------------


def test_closed_ring_with_door_and_bed_is_one_room(tmp_path: Path) -> None:
    tiles = ring_tiles(0, 0, 5)
    added = ring_objects(tiles, "w", owner="ada", door_at=(2, 0))
    added.append(object_state("bed_1", "bed", 2, 2, owner="ada"))
    added.append(object_state("wood_floor_1", "wood_floor", 1, 1, owner="ada"))
    ticks = [
        tick_record(0),
        tick_record(1, objects_added=added),
        tick_record(
            2,
            entity_updates=[
                entity_update("ada", asleep=True, sleeping_on="bed_1"),
                entity_update("bram"),
            ],
        ),
    ]
    scan, rooms, near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert len(rooms) == 1
    room = rooms[0]
    assert len(room.tiles) == 9
    assert room.bbox == (1, 1, 3, 3)
    assert len(room.door_tiles) == 1
    assert not room.sealed
    assert [bed.object_id for bed in room.beds] == ["bed_1"]
    assert room.floor_tiles == 1
    assert room.sleeper == "ada"
    assert room.sleeper_ticks == 1
    assert room.builder == "ada"
    assert near == []
    assert scan.totals_placed["wood_wall"] == 15


def test_sealed_ring_has_no_doors(tmp_path: Path) -> None:
    added = ring_objects(ring_tiles(0, 0, 5), "w", owner="ada")
    ticks = [tick_record(0, objects_added=added)]
    _scan, rooms, _near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert len(rooms) == 1
    assert rooms[0].sealed
    assert rooms[0].door_tiles == []


def test_ring_with_a_missing_wall_is_a_near_room(tmp_path: Path) -> None:
    tiles = ring_tiles(0, 0, 5)
    tiles.remove((2, 0))
    added = ring_objects(tiles, "w", owner="bram")
    ticks = [tick_record(0, objects_added=added)]
    _scan, rooms, near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert rooms == []
    assert len(near) == 1
    assert len(near[0].tiles) == 15
    assert near[0].bbox == (0, 0, 4, 4)
    assert near[0].single_tile_gaps == 1


def test_wall_cluster_below_the_threshold_is_not_reported(tmp_path: Path) -> None:
    added = [object_state(f"w_{i}", "wood_wall", i, 0, owner="ada") for i in range(5)]
    ticks = [tick_record(0, objects_added=added)]
    _scan, rooms, near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert rooms == []
    assert near == []


def test_two_rooms_sharing_a_wall_count_as_two(tmp_path: Path) -> None:
    added = ring_objects(ring_tiles(0, 0, 5), "a", owner="ada")
    added += ring_objects(ring_tiles(4, 0, 5), "b", owner="bram")
    ticks = [tick_record(0, objects_added=added)]
    _scan, rooms, _near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert len(rooms) == 2
    assert sorted(room.bbox for room in rooms) == [(1, 1, 3, 3), (5, 1, 7, 3)]


def wall_id_at(objects: list[dict], x: int, y: int) -> str:
    for obj in objects:
        if obj["position"] == {"x": x, "y": y}:
            return obj["object_id"]
    raise AssertionError(f"no object at ({x}, {y})")


def test_dismantling_a_corner_leaves_the_room_sealed(tmp_path: Path) -> None:
    """The world's diagonal rule means a corner hole is not a way out."""
    added = ring_objects(ring_tiles(0, 0, 5), "w", owner="ada")
    ticks = [
        tick_record(0, objects_added=added),
        tick_record(1, objects_removed=[wall_id_at(added, 0, 0)]),
    ]
    _scan, rooms, _near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert len(rooms) == 1
    assert rooms[0].sealed


def test_dismantled_walls_stop_enclosing(tmp_path: Path) -> None:
    added = ring_objects(ring_tiles(0, 0, 5), "w", owner="ada")
    removed_id = wall_id_at(added, 2, 0)
    ticks = [
        tick_record(0, objects_added=added),
        tick_record(1, objects_removed=[removed_id]),
    ]
    scan, rooms, near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert rooms == []
    assert len(near) == 1
    assert scan.totals_dismantled["wood_wall"] == 1


def test_a_huge_enclosure_is_not_a_room(tmp_path: Path) -> None:
    added = ring_objects(ring_tiles(0, 0, 20), "w", owner="ada")
    ticks = [tick_record(0, objects_added=added)]
    _scan, rooms, near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert rooms == []
    assert len(near) == 1


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------


def test_buckets_accumulate_placements_crafts_and_sleep(tmp_path: Path) -> None:
    ticks = [
        tick_record(
            0,
            objects_added=[object_state("chest_1", "chest", 0, 0, owner="ada")],
            actions=[
                {
                    "entity_id": "ada",
                    "action_type": "craft",
                    "success": True,
                    "details": "crafted axe",
                }
            ],
        ),
        tick_record(
            10,
            entity_updates=[
                entity_update("ada", asleep=True, sleeping_on="bed_1"),
                entity_update("bram", asleep=True),
            ],
        ),
        tick_record(
            300,
            objects_added=[object_state("bed_1", "bed", 1, 1, owner="bram")],
            deaths=[{"entity_id": "ada", "killer_id": "wolf_1"}],
            entities_despawned=[{"entity_id": "wolf_1", "reason": "killed"}],
        ),
        tick_record(
            310,
            entity_updates=[
                entity_update("ada", food=10, health=4),
                entity_update("bram", food=20, health=6),
            ],
        ),
    ]
    scan, _rooms, _near, _layout, bucket_ticks = analyse(make_run(tmp_path, ticks))

    assert bucket_ticks == 300
    assert [bucket.index for bucket in scan.buckets] == [0, 1]
    first, second = scan.buckets
    assert first.placed == {"chest": 1}
    assert first.crafts == {"axe": 1}
    assert first.new_tiers == ["axe"]
    assert first.sleep_bed_ticks == 1
    assert first.sleep_ground_ticks == 1
    assert second.placed == {"bed": 1}
    assert second.deaths == 1
    assert second.wolf_kills == 1
    assert second.mean_food == 15.0
    assert second.mean_health == 5.0
    assert scan.totals_placed == {"chest": 1, "bed": 1}


def test_bucket_ticks_override(tmp_path: Path) -> None:
    ticks = [tick_record(t) for t in (0, 50, 120)]
    scan, _rooms, _near, _layout, bucket_ticks = analyse(
        make_run(tmp_path, ticks), bucket_ticks=100
    )

    assert bucket_ticks == 100
    assert [bucket.index for bucket in scan.buckets] == [0, 1]


def test_plateau_counts_buckets_since_the_last_new_structure(tmp_path: Path) -> None:
    ticks = [
        tick_record(0, objects_added=[object_state("chest_1", "chest", 0, 0)]),
        tick_record(300),
        tick_record(600),
    ]
    scan, _rooms, _near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert worldscan.plateau_buckets(scan.buckets) == 2


def test_conversations_and_item_piles_are_not_structures(tmp_path: Path) -> None:
    ticks = [
        tick_record(
            0,
            objects_added=[
                object_state("conv_1", "conversation", 0, 0),
                object_state("item_pile_1", "item_pile", 1, 1),
            ],
        )
    ]
    scan, _rooms, _near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert scan.conversations == 1
    assert scan.totals_placed == {}
    assert scan.standing == {}


# --------------------------------------------------------------------------
# robustness and output
# --------------------------------------------------------------------------


def test_a_truncated_gzip_tail_is_tolerated(tmp_path: Path) -> None:
    ticks = [
        tick_record(t, objects_added=[object_state(f"chest_{t}", "chest", t, 0)])
        for t in range(6)
    ]
    run_dir = make_run(tmp_path, ticks, sync=True)
    path = run_dir / "world" / "ticks.jsonl.gz"
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) - 40])

    scan, _rooms, _near, _layout, _bucket = analyse(run_dir)

    assert 0 < scan.tick_count < 6
    assert scan.totals_placed["chest"] == scan.tick_count


def test_missing_ticks_file_is_an_error(tmp_path: Path) -> None:
    run_dir = make_run(tmp_path, [tick_record(0)])
    (run_dir / "world" / "ticks.jsonl.gz").unlink()
    try:
        analyse(run_dir)
    except FileNotFoundError as error:
        assert "ticks.jsonl.gz" in str(error)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_main_prints_a_headline_and_links(tmp_path: Path, capsys) -> None:
    tiles = ring_tiles(10, 10, 5)
    added = ring_objects(tiles, "w", owner="ada", door_at=(12, 10))
    added.append(object_state("bed_1", "bed", 12, 12, owner="ada"))
    run_dir = make_run(tmp_path, [tick_record(0, objects_added=added)])

    assert sp.main([str(run_dir)]) == 0
    out = capsys.readouterr().out
    assert "rooms: 1 (with bed: 1" in out
    assert f"?run={RUN_ID}&tick=0&x=12&y=12" in out


def test_main_json_round_trips(tmp_path: Path, capsys) -> None:
    added = ring_objects(ring_tiles(0, 0, 5), "w", owner="ada")
    run_dir = make_run(tmp_path, [tick_record(0, objects_added=added)])

    assert sp.main([str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == RUN_ID
    assert payload["headline"]["rooms"] == 1
    assert payload["rooms"][0]["sealed"] is True
    assert payload["standing"]["counts"] == {"wood_wall": 16}


def test_a_legacy_hunger_key_is_read_as_food(tmp_path: Path) -> None:
    """The same shared reader as analyze_run (docs/07_replay.md)."""
    update = entity_update("ada", food=30)
    update["hunger"] = update.pop("food")
    update["max_hunger"] = update.pop("max_food")
    ticks = [tick_record(0, entity_updates=[update])]
    scan, _rooms, _near, _layout, _bucket = analyse(make_run(tmp_path, ticks))

    assert scan.buckets[0].mean_food == 30.0


def test_a_directory_without_meta_json_is_an_error(tmp_path: Path, capsys) -> None:
    (tmp_path / "world").mkdir()
    assert sp.main([str(tmp_path)]) == 1
    assert "not a run directory" in capsys.readouterr().err


def test_both_tools_read_the_same_scan(tmp_path: Path) -> None:
    """One pass feeds both reports: the same deaths and the same wolf kills."""
    ticks = [
        tick_record(
            0,
            entity_updates=[entity_update("ada"), entity_update("bram")],
            objects_added=[object_state("bed_1", "bed", 1, 1, owner="ada")],
        ),
        tick_record(
            1,
            deaths=[
                {"entity_id": "ada", "killer_id": "wolf_1"},
                {"entity_id": "wolf_1", "killer_id": "bram"},
            ],
            entities_despawned=[{"entity_id": "wolf_1", "reason": "killed"}],
        ),
    ]
    run_dir = make_run(tmp_path, ticks)
    whole = worldscan.scan_world(
        runio.load_layout(run_dir).ticks_path,
        worldscan.initial_objects(runio.objects_path(runio.load_layout(run_dir))),
    )

    assert whole.build.deaths == sum(whole.facts.deaths.values()) == 1
    assert whole.build.wolf_kills == sum(whole.facts.wolf_kills.values()) == 1
    assert whole.build.totals_placed == {"bed": 1}
