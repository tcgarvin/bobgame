"""Notable moments and the text rendering of both reports."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .runio import RunLayout
    from .worldscan import BuildScan, WorldFacts

DEFAULT_MAX_MOMENTS = 60

# Notable moment kinds, most interesting first. The cap keeps the rarest kinds.
MOMENT_PRIORITY = (
    "death",
    "reflex_death",
    "wolf_killed",
    "expensive_turn",
    "planner_failed",
    "history_reset",
    "first_wolf",
    "reflex_during_conversation",
    "conversation_give",
    "conversation_joined",
    "hail",
    "milestone",
    "sign",
    "write_note",
    "place",
    "craft",
)


def deep_link(viewer_url: str, run_id: str, tick: int, entity_id: str = "") -> str:
    """A viewer deep link for one tick, optionally focused on one entity."""
    link = f"{viewer_url}/?run={run_id}&tick={tick}"
    if entity_id:
        link += f"&entity={entity_id}"
    return link


@dataclass
class Moment:
    tick: int
    kind: str
    entity_id: str
    text: str

    def link(self, viewer_url: str, run_id: str) -> str:
        return deep_link(viewer_url, run_id, self.tick, self.entity_id)

    def as_dict(self, viewer_url: str, run_id: str) -> dict:
        return {
            "tick": self.tick,
            "kind": self.kind,
            "entity_id": self.entity_id,
            "text": self.text,
            "link": self.link(viewer_url, run_id),
        }


def select_moments(
    moments: list[Moment], limit: int
) -> tuple[list[Moment], Counter[str]]:
    """Keep at most ``limit`` moments, dropping the most common kinds first."""
    order = {kind: i for i, kind in enumerate(MOMENT_PRIORITY)}
    ranked = sorted(moments, key=lambda m: (order.get(m.kind, len(order)), m.tick))
    kept = ranked[:limit]
    omitted = Counter(m.kind for m in ranked[limit:])
    return sorted(kept, key=lambda m: (m.tick, m.kind)), omitted


# --------------------------------------------------------------------------
# analyze_run's report
# --------------------------------------------------------------------------


def format_usd(amount: float) -> str:
    """Dollars with four decimals below $1 and two above (docs/11)."""
    return f"${amount:.4f}" if amount < 1.0 else f"${amount:.2f}"


def print_cost_section(run_cost: dict, summaries: list[dict]) -> None:
    """The "cost" report section: run totals, rates and the per-agent table."""
    print("== cost (docs/11_cost_accounting.md) ==")
    planner_text = (
        format_usd(run_cost["planner_usd"])
        if run_cost["planner_cost_available"]
        else "n/a (run recorded before cost accounting)"
    )
    print(
        f"total: {format_usd(run_cost['total_usd'])}  "
        f"planner: {planner_text}  "
        f"converser: {format_usd(run_cost['converser_usd'])}  "
        f"journal: {format_usd(run_cost['journal_usd'])}  "
        f"jev: {format_usd(run_cost['jev_usd'])}"
    )
    if run_cost["planner_cost_is_lower_bound"]:
        print("planner total is a LOWER BOUND: some turns reported no cost")
    rate_parts = []
    if "usd_per_100_ticks" in run_cost:
        rate_parts.append(f"{format_usd(run_cost['usd_per_100_ticks'])} / 100 ticks")
    if "usd_per_hour" in run_cost:
        rate_parts.append(f"{format_usd(run_cost['usd_per_hour'])} / wall-clock hour")
    print(f"rates: {', '.join(rate_parts) if rate_parts else 'unavailable'}")

    print(
        f"{'agent':7s} {'planner':>9s} {'convers':>9s} {'journal':>9s} "
        f"{'jev':>9s} {'total':>9s} "
        f"{'turns':>5s} {'$/turn':>9s} {'req/turn':>8s} {'cached':>7s}"
    )
    for s in summaries:
        cost = s["cost"]
        # An agent with no finished turn in an instrumented run has spent
        # nothing on the planner; only a pre-accounting run is truly unknown.
        available = cost["planner_cost_available"] or run_cost["planner_cost_available"]
        planner_cell = format_usd(cost["planner_usd"]) if available else "n/a"
        per_turn_cell = format_usd(cost["usd_per_turn_mean"]) if available else "n/a"
        print(
            f"{s['agent']:7s} {planner_cell:>9s} "
            f"{format_usd(cost['converser_usd']):>9s} "
            f"{format_usd(cost['journal_usd']):>9s} "
            f"{format_usd(cost['jev_usd']):>9s} "
            f"{format_usd(cost['total_usd']):>9s} "
            f"{cost['turns_with_usage']:5d} {per_turn_cell:>9s} "
            f"{cost['requests_per_turn_mean']:8.1f} "
            f"{cost['cached_token_share'] * 100:6.1f}%"
        )


def print_journal_section(summaries: list[dict]) -> None:
    """The "journal" section: rewrites, failures and truncations per settler."""
    print("== journal (docs/12_sleep_journal.md) ==")
    rewrites = sum(s["cost"]["journal_rewrites"] for s in summaries)
    failures = sum(s["cost"]["journal_failures"] for s in summaries)
    if not rewrites and not failures:
        print("no journal rewrites recorded (run predates docs/12)")
        return
    print(f"rewrites: {rewrites}  failures: {failures}")
    print(
        f"{'agent':7s} {'rewrites':>8s} {'failed':>6s}  "
        f"{'triggers':22s} truncated sections"
    )
    for s in summaries:
        cost = s["cost"]
        if not cost["journal_rewrites"] and not cost["journal_failures"]:
            continue
        triggers = ", ".join(
            f"{name} x{count}" for name, count in cost["journal_triggers"].items()
        )
        truncated = (
            ", ".join(
                f"{name} x{count}"
                for name, count in cost["journal_truncations"].items()
            )
            or "none"
        )
        print(
            f"{s['agent']:7s} {cost['journal_rewrites']:8d} "
            f"{cost['journal_failures']:6d}  {triggers or 'none':22s} {truncated}"
        )


def print_world_section(facts: "WorldFacts") -> None:
    """The world, metal tier and sleep sections, from the tick recording."""
    print(
        f"== world: ticks {facts.first_tick}..{facts.last_tick} "
        f"({facts.ticks} recorded) =="
    )
    print(f"deaths: {sum(facts.deaths.values())} settlers by={dict(facts.killers)}")
    print(f"wolf kills: {sum(facts.wolf_kills.values())} by={dict(facts.wolf_kills)}")
    print(
        f"wolves: spawned={facts.wolves_spawned} "
        f"despawned={dict(facts.wolves_despawned)}"
    )
    print(f"crafts: {dict(facts.crafts)}")
    print(f"placements: {dict(facts.placements)}")
    print(
        f"building: dismantles={dict(facts.dismantles)} "
        f"workshop_crafts={facts.workshop_crafts} rests={facts.rests}"
    )
    print(
        f"notes written: {facts.notes_written}  "
        f"signs written: {facts.signs_written} "
        f"(blanked {facts.signs_cleared})  "
        f"utterances: {facts.utterances}"
        f"  shouts: {facts.shouts}"
    )
    print()
    print("== metal tier ==")
    print(f"smelts: {facts.smelts}")
    print(f"stations placed: {facts.stations_placed}")
    print(f"metal tools crafted: {facts.metal_tools_crafted}")
    print(f"vein yields: {dict(facts.vein_yields)}")
    print()
    print("== sleep ==")
    if not facts.sleep_data_available:
        print("unavailable (run recorded before docs/10_metal_and_sleep.md)")
        return
    print(
        f"sleeps: {dict(facts.sleeps)}  collapses: {facts.collapses}  "
        f"wakes: {dict(facts.wakes)}"
    )
    if facts.night_midpoints:
        print(
            "asleep at night midpoint, by day: "
            + ", ".join(
                f"day {entry['day']} (t{entry['tick']}): {entry['asleep']}"
                for entry in facts.night_midpoints
            )
        )
    else:
        print("asleep at night midpoint, by day: no complete night recorded")


def print_conversation_section(summary: dict) -> None:
    """The "conversations" section (docs/09_conversation_and_reflex.md)."""
    print("== conversations ==")
    if not (
        summary["opened"]
        or summary["end_reasons"]
        or summary["hails_attempted"]
        or summary["brief_hails_granted"]
    ):
        print("none")
        return
    print(
        f"opened: {summary['opened']}  "
        f"joined: {summary['joined']}  "
        f"distinct participants: {summary['distinct_participants']}  "
        f"utterances: {summary['utterances']}"
    )
    print(
        f"hails: {summary['hails_attempted']} attempted, "
        f"{summary['hails_succeeded']} succeeded  "
        f"opened by hail: {summary['opened_by_hail']}"
    )
    print(
        f"brief hails granted: {summary['brief_hails_granted']}  "
        f"hail: chosen by Jev: {summary['jev_hails_chosen']}  "
        f"planner talk_to hails: {summary['planner_hails_attempted']}"
    )
    if summary["hail_failures"]:
        print(f"hails refused: {summary['hail_failures']}")
    print(f"ended by: {summary['end_reasons']}")
    print(
        f"mean length: {summary['mean_duration_ticks']:.1f} ticks, "
        f"{summary['mean_lines']:.1f} lines"
    )
    stats = summary["line_count_stats"]
    print(
        f"lines per conversation: min {stats['min']}, "
        f"median {stats['median']:.1f}, max {stats['max']}"
    )
    print(f"notes written: {summary['notes_written']}")
    if summary["purpose_total"]:
        print(
            "purpose given: "
            f"{summary['purpose_given']} of {summary['purpose_total']}"
        )
    if summary["longest"]:
        print("longest conversations:")
        for entry in summary["longest"]:
            print(
                f"  {entry['conversation_id']} opened by {entry['opened_by']} "
                f"at t{entry['opened_tick']}: {entry['duration_ticks']} ticks, "
                f"{entry['utterances']} lines, with "
                f"{', '.join(entry['participants'])}"
            )
            print(f"    {entry['link']}")


def print_report(
    layout: "RunLayout",
    summaries: list[dict],
    totals: dict,
    facts: "WorldFacts | None",
    conversation_summary: dict,
    giving_summary: dict,
    reflex_summary: dict,
    moments: list[Moment],
    omitted: Counter[str],
    viewer_url: str,
) -> None:
    print(f"== run summary: {layout.run_id} ({len(summaries)} agents) ==")
    started = layout.meta.get("started_at", "?")
    finished = layout.meta.get("finished_at") or "(unfinished)"
    print(
        f"config: {layout.meta.get('config_name', '?')}  "
        f"started: {started}  finished: {finished}"
    )
    print(f"stint ticks: {totals['stint_ticks']}")
    print(f"stints: {totals['stints']} ends={totals['end_reasons']}")
    print(
        f"planner turns: {totals['planner_turns']}"
        f" failures={totals['planner_turn_failures']}"
        f" history_resets={totals['history_resets']}"
        f" tool_budget_spent={totals['budget_spent']}"
        f" tool_budget_hard_stops={totals['budget_hard_stops']}"
    )
    print(f"tool calls: {totals['tools']}")
    print(f"jev actions: {totals['actions']}")
    if totals["latency_p50_median"]:
        print(
            "jev latency p50 (median of agents): "
            f"{totals['latency_p50_median']:.0f} ms"
        )
    print()

    header = (
        f"{'agent':7s} {'ticks':>5s} {'stints':>6s} {'turns':>5s} {'p50ms':>6s} "
        f"{'p95ms':>6s} {'tok':>5s} {'eject':>5s} {'dang':>5s} {'top':>4s} "
        f"{'fail':>4s} rejected"
    )
    print(header)
    for s in summaries:
        print(
            f"{s['agent']:7s} {s['stint_ticks']:5d} {s['stints']:6d} "
            f"{s['planner_turns']:5d} "
            f"{s['latency_p50']:6.0f} {s['latency_p95']:6.0f} {s['tokens_mean']:5.0f} "
            f"{s['eject_mean']:5.2f} {s['danger_mean']:5.2f} {s['top_prob_mean']:4.2f} "
            f"{s['intent_failures']:4d} {s['rejected']}"
        )
    print()
    for s in summaries:
        if s["last_thought"]:
            print(f"[{s['agent']}] {s['last_thought'][:400]}")

    print()
    print_cost_section(totals["cost"], summaries)
    print()
    print_journal_section(summaries)

    if facts is not None:
        print()
        print_world_section(facts)

    print()
    print_conversation_section(conversation_summary)

    print()
    print("== giving ==")
    if giving_summary["count"]:
        print(f"gives: {giving_summary['count']}  kinds: {giving_summary['kinds']}")
        print(f"pairs: {giving_summary['pairs']}")
    else:
        print("none")

    print()
    print("== reflexes ==")
    if reflex_summary["agents"] or reflex_summary["end_reasons"]:
        print("registered:")
        for agent_id, info in reflex_summary["agents"].items():
            print(f"  {agent_id} at t{info['registered_tick']}: {info['digest']}")
        print(f"firings by trigger: {reflex_summary['firings_by_trigger']}")
        print(f"firings by interrupted: {reflex_summary['firings_by_interrupted']}")
        print(f"end reasons: {reflex_summary['end_reasons']}")
        print(
            f"mean health change: {reflex_summary['mean_health_change']:.1f}  "
            "mean ticks trigger->first action: "
            f"{reflex_summary['mean_ticks_to_first_action']:.1f}"
        )
    else:
        print("none")

    if moments:
        print()
        print(f"== notable moments ({len(moments)} shown) ==")
        for moment in moments:
            print(f"  t{moment.tick:<6d} {moment.kind:<14s} {moment.text}")
            print(f"           {moment.link(viewer_url, layout.run_id)}")
        if omitted:
            total = sum(omitted.values())
            print(f"  ... {total} more omitted: {dict(omitted.most_common())}")


# --------------------------------------------------------------------------
# settlement_progress's report
# --------------------------------------------------------------------------

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

# Zoom level for the room deep links (docs/07_replay.md, "Deep links").
ROOM_ZOOM = 2.0


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


def room_link(
    viewer_url: str, run_id: str, tick: int, bbox: tuple[int, int, int, int]
) -> str:
    """A viewer deep link centred on a bounding box (docs/07_replay.md)."""
    min_x, min_y, max_x, max_y = bbox
    x = (min_x + max_x) // 2
    y = (min_y + max_y) // 2
    return f"{viewer_url}/?run={run_id}&tick={tick}&x={x}&y={y}&zoom={ROOM_ZOOM}"


def print_timeline(scan: "BuildScan", bucket_ticks: int) -> None:
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


def print_progress_report(
    scan: "BuildScan",
    rooms: Sequence,
    near: Sequence,
    run_id: str,
    bucket_ticks: int,
    viewer_url: str,
    plateau: int,
) -> None:
    """settlement_progress's human report."""
    from .rooms import bbox_of  # local import: geometry only needed here

    tick = scan.last_tick
    settlers = len(scan.settlers)
    with_bed = sum(1 for room in rooms if room.beds)
    owners = {room.sleeper for room in rooms if room.sleeper}

    print(f"Settlement progress: {run_id}")
    print(f"  ticks {scan.first_tick}-{scan.last_tick} ({scan.tick_count} recorded)")
    print(
        f"  rooms: {len(rooms)} (with bed: {with_bed}, distinct owners: "
        f"{len(owners)}) / settlers: {settlers}"
    )
    print(
        f"  near-rooms: {len(near)}   plateau: {plateau} bucket(s) since the last "
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
