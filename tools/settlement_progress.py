#!/usr/bin/env python3
"""How far has the settlement got, and has it plateaued?

Reads a run recording (docs/07_replay.md) and reports the settlement's
build-out: a per-day timeline of placements and activity, what stands at the
last recorded tick, the enclosed rooms the standing walls and doors make, and
a headline with a plateau indicator.

The target this measures against is "a functioning settlement with roughly one
house or room per settler".

The reading, the room fill and the rendering live in ``tools/runlib``; this
file is the command line over them.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runlib import report, rooms, runio, worldscan  # noqa: E402


def analyse(run_dir: Path, bucket_ticks: int) -> tuple[
    worldscan.BuildScan,
    list[rooms.Room],
    list[rooms.NearRoom],
    runio.RunLayout,
    int,
]:
    """Read a run directory and derive everything the report needs."""
    layout = runio.load_layout(run_dir)
    if layout.ticks_path is None:
        raise FileNotFoundError(
            f"{run_dir} has no world/ticks.jsonl.gz; this tool needs a run "
            "directory recorded by the world server (docs/07_replay.md)"
        )
    if bucket_ticks <= 0:
        bucket_ticks = int(
            layout.meta.get("day_length_ticks", worldscan.DEFAULT_DAY_LENGTH)
        )
    start = worldscan.initial_objects(runio.objects_path(layout))
    scan = worldscan.scan_world(layout.ticks_path, start, bucket_ticks).build
    room_list, near = rooms.find_rooms(scan.standing, scan.sleep_on_bed)
    return (scan, room_list, near, layout, bucket_ticks)


def report_dict(
    scan: worldscan.BuildScan,
    room_list: list[rooms.Room],
    near: list[rooms.NearRoom],
    layout: runio.RunLayout,
    bucket_ticks: int,
    viewer_url: str,
) -> dict:
    """The ``--json`` document."""
    counts = Counter(o.kind for o in scan.standing.values())
    built = [o.tile for o in scan.standing.values()]
    min_x, min_y, max_x, max_y = rooms.bbox_of(built)
    return {
        "run_id": layout.run_id,
        "bucket_ticks": bucket_ticks,
        "first_tick": scan.first_tick,
        "last_tick": scan.last_tick,
        "ticks_recorded": scan.tick_count,
        "settlers": sorted(scan.settlers),
        "headline": {
            "rooms": len(room_list),
            "rooms_with_bed": sum(1 for room in room_list if room.beds),
            "distinct_owners": len({r.sleeper for r in room_list if r.sleeper}),
            "settlers": len(scan.settlers),
            "near_rooms": len(near),
            "buckets_since_progress": worldscan.plateau_buckets(scan.buckets),
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
                link=report.room_link(
                    viewer_url, layout.run_id, scan.last_tick, room.bbox
                ),
            )
            for room in room_list
        ],
        "near_rooms": [
            dict(
                cluster.as_dict(),
                link=report.room_link(
                    viewer_url, layout.run_id, scan.last_tick, cluster.bbox
                ),
            )
            for cluster in near
        ],
    }


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
    parser.add_argument("--viewer-url", default=runio.DEFAULT_VIEWER_URL)
    args = parser.parse_args(argv)

    run_dir = runio.resolve_run_dir(args.run_dir)
    if not run_dir.exists():
        print(f"no such run directory: {run_dir}", file=sys.stderr)
        return 1

    try:
        scan, room_list, near, layout, bucket_ticks = analyse(
            run_dir, args.bucket_ticks
        )
    except runio.RunLayoutError as error:
        print(str(error), file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                report_dict(
                    scan, room_list, near, layout, bucket_ticks, args.viewer_url
                ),
                indent=2,
            )
        )
    else:
        report.print_progress_report(
            scan,
            room_list,
            near,
            layout.run_id,
            bucket_ticks,
            args.viewer_url,
            worldscan.plateau_buckets(scan.buckets),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
