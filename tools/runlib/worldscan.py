"""One pass over ``world/ticks.jsonl.gz``.

Both `analyze_run.py` and `settlement_progress.py` read the world recording
through :func:`scan_world`, so every number either of them prints comes from
the same traversal and the same definitions:

* :class:`WorldFacts` - the aggregate counts and the notable moments
* :class:`BuildScan`  - the build-out timeline, the standing set and sleep
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Container, Mapping

from bobgame_rules.clock import DEFAULT_DAY_LENGTH_TICKS
from bobgame_rules.entities import WOLF_ENTITY_TYPE
from bobgame_rules.items import (
    BLOCKING_OBJECT_TYPES,
    GROUND_LAYER_KINDS,
    STATION_KINDS as CRAFT_STATION_KINDS,
)
from bobgame_rules.social import CONVERSATION

from .report import Moment
from .runio import entity_food, iter_jsonl

# --------------------------------------------------------------------------
# what the world's own words mean (world/src/world/items.py, containers.py)
# --------------------------------------------------------------------------

CRAFT_ACTIONS = frozenset({"craft"})
PLACE_ACTIONS = frozenset({"place"})
NOTE_ACTIONS = frozenset({"write_note"})
REST_ACTIONS = frozenset({"rest"})
EXTRACT_ACTIONS = frozenset({"extract"})
CONVERSE_ACTIONS = frozenset({"converse"})
GIVE_ACTIONS = frozenset({"give"})
SLEEP_ACTIONS = frozenset({"sleep"})
WAKE_ACTIONS = frozenset({"wake"})
COLLAPSE_ACTIONS = frozenset({"collapse"})

# Building kinds (docs/08_building.md, docs/10_metal_and_sleep.md). A town
# lays hundreds of these, so they are counted but only the first of each kind
# becomes a notable moment. `furnace`/`anvil` are the metal-tier stations.
STATION_KINDS = frozenset({"furnace", "anvil"})
BUILDING_KINDS = (
    frozenset(
        {
            "road",
            "sign",
            "wood_floor",
            "stone_floor",
            "wood_wall",
            "stone_wall",
            "door",
            "bed",
            "chair",
            "table",
            "workshop_table",
        }
    )
    | STATION_KINDS
)

# The metal tier (docs/10_metal_and_sleep.md, sections 2 and 7).
SMELT_KINDS = frozenset({"charcoal", "copper_ingot", "iron_ingot"})
INGOT_KINDS = frozenset({"copper_ingot", "iron_ingot"})
METAL_TOOL_KINDS = frozenset(
    {"copper_axe", "copper_pickaxe", "iron_axe", "iron_pickaxe", "iron_sword"}
)
IRON_TOOL_KINDS = frozenset({"iron_axe", "iron_pickaxe", "iron_sword"})

# Bulk intermediates: counted, never a moment of their own. Smelting products
# and metal tools would otherwise spam a "craft" moment per settler per item;
# they get their own milestones (first ingot, first iron tool) instead.
BULK_CRAFTS = (
    BUILDING_KINDS | frozenset({"plank", "rope"}) | SMELT_KINDS | METAL_TOOL_KINDS
)
WORKSHOP_RECIPES = frozenset(
    {"stone_wall", "stone_floor", "door", "bed", "chair", "table"}
)

# The world writes prose details, e.g. "crafted sword", "placed chest_3 at (1,2)".
CRAFTED_RE = re.compile(r"crafted (\S+)")
PLACED_RE = re.compile(r"placed (\S+?)(?:_\d+)? ")
DISMANTLED_RE = re.compile(r"dismantled (\S+?)(?:_\d+)? ")

# Vein extraction (docs/10_metal_and_sleep.md, section 2): a successful
# `extract` on a copper_vein/iron_vein reports "worked <object_id> (+1
# <ore kind>)".
VEIN_YIELD_RE = re.compile(r"\(\+1 (copper_ore|iron_ore)\)")

# Sleep and wake details (world/src/world/sleep.py): "asleep on <bed id|the
# ground>" and "woke up: <reason>".
SLEEP_RE = re.compile(r"^asleep on (.+)$")
WAKE_RE = re.compile(r"^woke up: (.+)$")
GROUND_SLEEP_PLACE = "the ground"

# A sign write and a sign blanking, as `world/containers.py` words them. Signs
# share the `write_note` action with message boards (docs/08_building.md).
SIGN_WRITE_RE = re.compile(r'^wrote (sign_\d+): "(.*)"$')
SIGN_CLEAR_RE = re.compile(r"^cleared (sign_\d+)$")

# Conversation and give details, docs/09_conversation_and_reflex.md sections
# 2.3 and 3: "open conv_12", "join conv_12", "gave 3 stone to mira".
CONVERSE_OPEN_RE = re.compile(r"^open (conv_\S+)$")
CONVERSE_JOIN_RE = re.compile(r"^join (conv_\S+)$")
# Hails, docs/09_conversation_and_reflex.md section 9: the hailer's details are
# "hail conv_12 mira" and the settler it seated reads "hailed conv_12 ivo".
CONVERSE_HAIL_RE = re.compile(r"^hail (conv_\S+) (\S+)$")
CONVERSE_HAILED_RE = re.compile(r"^hailed (conv_\S+) (\S+)$")
GAVE_RE = re.compile(r"^gave (\d+) (\S+) to (\S+)$")

# A failed converse action reports only the world's reason, not which action
# asked for it, so a failed hail can only be recognised by its wording. These
# are the reasons `hail` alone produces (docs/09 section 9); the ones it shares
# with other refusals ("not next to <id>", "already in a conversation") are
# left out rather than attributed to the wrong action, so the attempted count
# is a lower bound.
HAIL_FAILURE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^there is no settler called \S+$"), "no such settler"),
    (re.compile(r"^\S+ is dead$"), "target dead"),
    (re.compile(r"^\S+ is asleep$"), "target asleep"),
    (re.compile(r"^you are asleep$"), "hailer asleep"),
    (
        re.compile(r"^\S+ is already in conversation conv_\S+$"),
        "target already talking",
    ),
    (re.compile(r"cannot be hailed for another \d+ ticks$"), "hail cooldown"),
    (re.compile(r"^no free tile next to you both$"), "no free tile"),
    (re.compile(r"^an opening line is required$"), "no opening line"),
)

PLAYER_ENTITY_TYPE = "player"
CONVERSATION_OBJECT_TYPE = CONVERSATION

# Object kinds settlement_progress follows from tick 0 to the last tick.
WALL_KINDS: frozenset[str] = BLOCKING_OBJECT_TYPES
DOOR_KINDS: frozenset[str] = frozenset({"door"})
GROUND_KINDS: frozenset[str] = GROUND_LAYER_KINDS
BUILD_STATION_KINDS: frozenset[str] = CRAFT_STATION_KINDS
FURNITURE_KINDS: frozenset[str] = frozenset({"bed", "chair", "table"})
FIXTURE_KINDS: frozenset[str] = frozenset({"chest", "message_board", "sign"})

# Natural objects and item piles are deliberately left out: the standing set
# is meant to be what the settlers built.
TRACKED_KINDS: frozenset[str] = (
    WALL_KINDS
    | DOOR_KINDS
    | GROUND_KINDS
    | BUILD_STATION_KINDS
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
    | BUILD_STATION_KINDS
)

DEFAULT_DAY_LENGTH = DEFAULT_DAY_LENGTH_TICKS


def hail_failure_label(details: str) -> str:
    """The bucket for a failed `hail`, or "" when the reason is not one."""
    for pattern, label in HAIL_FAILURE_PATTERNS:
        if pattern.search(details):
            return label
    return ""


def is_wolf(
    entity_id: str, entity_types: Mapping[str, str], wolf_ids: Container[str]
) -> bool:
    """Whether this entity is a wolf, by recorded type, spawn record or name.

    Runs recorded before `entity_updates` carried `entity_type` fall back to
    the spawn records and, failing those, to the `wolf_*` id convention.
    """
    known = entity_types.get(entity_id, "")
    if known:
        return known == WOLF_ENTITY_TYPE
    return entity_id in wolf_ids or entity_id.startswith("wolf")


def death_cause(
    killer_id: str,
    food: int,
    entity_types: Mapping[str, str],
    wolf_ids: Container[str],
) -> str:
    """What killed a settler: a wolf, another settler by name, or no one.

    The world leaves `killer_id` empty for a death nobody dealt, which in
    practice is starvation; it is only called that when the record shows the
    settler's food at zero, and `unknown` otherwise.
    """
    if not killer_id:
        return "starvation" if food == 0 else "unknown"
    if is_wolf(killer_id, entity_types, wolf_ids):
        return WOLF_ENTITY_TYPE
    return killer_id


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass
class ConversationRecord:
    """Everything the world's own ticks say about one `conversation` object.

    Built from `converse` actions (which carry the conversation id only for
    `open`/`join`, docs/09 section 2.3) and from utterances on the
    `conversation` channel (which carry it for every line, section 2.3).
    """

    conversation_id: str
    opened_tick: int = -1
    opened_by: str = ""
    joins: list[tuple[int, str]] = field(default_factory=list)
    participants: set[str] = field(default_factory=set)
    utterance_ticks: list[int] = field(default_factory=list)
    end_tick: int = -1
    # Whether it began with a `hail` (docs/09 section 9).
    via_hail: bool = False

    @property
    def utterance_count(self) -> int:
        return len(self.utterance_ticks)

    @property
    def duration_ticks(self) -> int:
        """Ticks from open to close, or 0 when either end is unknown."""
        if self.opened_tick < 0 or self.end_tick < 0:
            return 0
        return self.end_tick - self.opened_tick

    def active_at(self, entity_id: str, tick: int) -> bool:
        """Whether `entity_id` was seated in this conversation at `tick`.

        A conversation with no recorded close yet is treated as still open.
        """
        if entity_id not in self.participants or self.opened_tick < 0:
            return False
        if tick < self.opened_tick:
            return False
        end = self.end_tick if self.end_tick >= 0 else tick
        return tick <= end


@dataclass
class GiveEvent:
    """One successful `give` action, parsed from its `EntityActed.details`."""

    tick: int
    giver: str
    receiver: str
    kind: str
    amount: int


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
class WorldFacts:
    """The run-level counts `analyze_run` reports."""

    ticks: int = 0
    first_tick: int = 0
    last_tick: int = 0
    # Settler deaths only, by the settler who died and by what killed it
    # (`wolf` for any wolf, `starvation` for an empty killer on an empty
    # stomach, `unknown` otherwise). Wolves dying are kills, not deaths, and
    # are counted in `wolf_kills` by the settler who landed the blow.
    deaths: Counter[str] = field(default_factory=Counter)
    killers: Counter[str] = field(default_factory=Counter)
    wolf_kills: Counter[str] = field(default_factory=Counter)
    wolves_spawned: int = 0
    wolves_despawned: Counter[str] = field(default_factory=Counter)
    crafts: Counter[str] = field(default_factory=Counter)
    placements: Counter[str] = field(default_factory=Counter)
    dismantles: Counter[str] = field(default_factory=Counter)
    workshop_crafts: int = 0
    rests: int = 0
    notes_written: int = 0
    signs_written: int = 0
    signs_cleared: int = 0
    utterances: int = 0
    shouts: int = 0
    # Hails, docs/09_conversation_and_reflex.md section 9.
    hails_succeeded: int = 0
    hail_failures: Counter[str] = field(default_factory=Counter)

    # Metal tier and sleep (docs/10_metal_and_sleep.md, section 7).
    vein_yields: Counter[str] = field(default_factory=Counter)
    sleeps: Counter[str] = field(default_factory=Counter)
    collapses: int = 0
    wakes: Counter[str] = field(default_factory=Counter)
    sleep_data_available: bool = False
    # One entry per night seen, at that night's midpoint tick:
    # {"day", "tick", "asleep"}. Only meaningful when `sleep_data_available`.
    night_midpoints: list[dict] = field(default_factory=list)

    @property
    def smelts(self) -> dict[str, int]:
        """Charcoal and ingot craft counts, a subset of `crafts`."""
        return {
            kind: count for kind, count in self.crafts.items() if kind in SMELT_KINDS
        }

    @property
    def stations_placed(self) -> dict[str, int]:
        """Furnace/anvil placement counts, a subset of `placements`."""
        return {
            kind: count
            for kind, count in self.placements.items()
            if kind in STATION_KINDS
        }

    @property
    def metal_tools_crafted(self) -> dict[str, int]:
        """Copper/iron tool craft counts, a subset of `crafts`."""
        return {
            kind: count
            for kind, count in self.crafts.items()
            if kind in METAL_TOOL_KINDS
        }

    def as_dict(self) -> dict:
        return {
            "ticks": self.ticks,
            "first_tick": self.first_tick,
            "last_tick": self.last_tick,
            "deaths": dict(self.deaths),
            "killers": dict(self.killers),
            "wolf_kills": dict(self.wolf_kills),
            "wolves_spawned": self.wolves_spawned,
            "wolves_despawned": dict(self.wolves_despawned),
            "crafts": dict(self.crafts),
            "placements": dict(self.placements),
            "dismantles": dict(self.dismantles),
            "workshop_crafts": self.workshop_crafts,
            "rests": self.rests,
            "notes_written": self.notes_written,
            "signs_written": self.signs_written,
            "signs_cleared": self.signs_cleared,
            "utterances": self.utterances,
            "shouts": self.shouts,
            "hails_succeeded": self.hails_succeeded,
            "hail_failures": dict(self.hail_failures),
            "smelts": self.smelts,
            "stations_placed": self.stations_placed,
            "metal_tools_crafted": self.metal_tools_crafted,
            "vein_yields": dict(self.vein_yields),
            "sleeps": dict(self.sleeps),
            "collapses": self.collapses,
            "wakes": dict(self.wakes),
            "sleep_data_available": self.sleep_data_available,
            "night_midpoints": self.night_midpoints,
        }


@dataclass
class BuildScan:
    """The build-out view of the same pass, for settlement_progress."""

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


@dataclass
class WorldScan:
    """Everything one pass over the tick stream produces."""

    facts: WorldFacts
    moments: list[Moment]
    conversations: dict[str, ConversationRecord]
    giving: list[GiveEvent]
    build: BuildScan


# --------------------------------------------------------------------------
# the pass
# --------------------------------------------------------------------------


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


def initial_objects(path: Path | None) -> dict[str, Standing]:
    """The tracked built objects present at tick 0.

    ``world/objects.jsonl.gz`` holds every object the world started with,
    mostly trees and rocks; only the built kinds are kept.
    """
    standing: dict[str, Standing] = {}
    if path is None:
        return standing
    for record in iter_jsonl(path):
        if record.get("object_type", "") not in TRACKED_KINDS:
            continue
        standing[record["object_id"]] = to_standing(record, tick=0)
    return standing


def craft_kind(details: str) -> str:
    """The item kind named by a successful craft action's details."""
    match = CRAFTED_RE.search(details)
    return match.group(1) if match else details or "?"


def placed_kind(details: str) -> str:
    """The object kind named by a successful place action's details."""
    match = PLACED_RE.search(details)
    return match.group(1) if match else details or "?"


def scan_world(
    path: Path,
    start_objects: dict[str, Standing] | None = None,
    bucket_ticks: int = DEFAULT_DAY_LENGTH,
) -> WorldScan:
    """Read ``world/ticks.jsonl.gz`` once into everything both tools report."""
    facts = WorldFacts()
    build = BuildScan(standing=dict(start_objects or {}))
    moments: list[Moment] = []
    conversations: dict[str, ConversationRecord] = {}
    giving: list[GiveEvent] = []

    wolf_ids: set[str] = set()
    seen_first_wolf = False
    seen_first_ingot = False
    seen_first_iron_tool = False
    seen_first_collapse = False
    seen_first_asleep_death = False
    seen_tiers: set[str] = set()
    first_tick_set = False
    # Asleep state per entity as of the *previous* tick's `entity_updates`,
    # used to tell whether a death this tick struck a sleeper (death clears
    # `asleep` on the same tick, so the current tick's own state can't say).
    last_asleep: dict[str, bool] = {}
    # Entity type and food per entity, from the latest `entity_updates` seen.
    # Both are needed to read a death record: wolves dying are kills, not
    # deaths, and an unattributed death is starvation only on an empty stomach.
    entity_types: dict[str, str] = {}
    food_now: dict[str, int] = {}
    night_ticks_by_day: defaultdict[int, list[tuple[int, int]]] = defaultdict(list)
    buckets: dict[int, Bucket] = {}
    last_stats: dict[int, tuple[float, float]] = {}

    for record in iter_jsonl(path):
        if record.get("type") != "tick":
            continue
        tick = int(record.get("tick_id", 0))
        facts.ticks += 1
        if not first_tick_set:
            facts.first_tick = tick
            build.first_tick = tick
            first_tick_set = True
        facts.last_tick = max(facts.last_tick, tick)
        build.last_tick = facts.last_tick
        build.tick_count += 1

        index = (tick - build.first_tick) // max(1, bucket_ticks)
        bucket = buckets.get(index)
        if bucket is None:
            bucket = Bucket(index=index, start_tick=tick, end_tick=tick)
            buckets[index] = bucket
        bucket.end_tick = max(bucket.end_tick, tick)

        clock = record.get("clock")
        entity_updates = record.get("entity_updates", ())
        asleep_now = {
            eu["entity_id"]: bool(eu.get("asleep", False))
            for eu in entity_updates
            if eu.get("entity_id")
        }
        foods: list[float] = []
        healths: list[float] = []
        for eu in entity_updates:
            entity_id = eu.get("entity_id", "")
            if not entity_id:
                continue
            entity_type = str(eu.get("entity_type", ""))
            if entity_type:
                entity_types[entity_id] = entity_type
            food = entity_food(eu)
            if food >= 0:
                food_now[entity_id] = food
            if entity_type != PLAYER_ENTITY_TYPE:
                continue
            build.settlers.add(entity_id)
            if eu.get("alive", True):
                foods.append(float(entity_food(eu, default=0)))
                healths.append(float(eu.get("health", 0)))
            if not eu.get("asleep"):
                continue
            bed = str(eu.get("sleeping_on", ""))
            if bed:
                bucket.sleep_bed_ticks += 1
                build.sleep_on_bed[(entity_id, bed)] += 1
            else:
                bucket.sleep_ground_ticks += 1
        if foods:
            last_stats[index] = (
                sum(foods) / len(foods),
                sum(healths) / len(healths),
            )

        if clock is not None:
            facts.sleep_data_available = True
            if clock.get("night"):
                night_ticks_by_day[int(clock.get("day", 0))].append(
                    (tick, sum(asleep_now.values()))
                )

        facts.utterances += len(record.get("utterances", ()))
        facts.shouts += sum(
            1 for u in record.get("utterances", ()) if u.get("channel") == "shout"
        )
        for utterance in record.get("utterances", ()):
            if utterance.get("channel") != "conversation":
                continue
            conv_id = utterance.get("conversation_id", "")
            if not conv_id:
                continue
            conv = conversations.setdefault(conv_id, ConversationRecord(conv_id))
            conv.utterance_ticks.append(tick)
            speaker = utterance.get("speaker_id", "")
            if speaker:
                conv.participants.add(speaker)

        for added in record.get("objects_added", ()):
            kind = added.get("object_type", "")
            if kind == CONVERSATION_OBJECT_TYPE:
                bucket.conversations += 1
                build.conversations += 1
                continue
            if kind not in TRACKED_KINDS:
                continue
            build.standing[added["object_id"]] = to_standing(added, tick)
            bucket.placed[kind] += 1
            build.totals_placed[kind] += 1

        for object_id in record.get("objects_removed", ()):
            if object_id in conversations:
                conversations[object_id].end_tick = tick
            gone = build.standing.pop(object_id, None)
            if gone is not None:
                bucket.dismantled[gone.kind] += 1
                build.totals_dismantled[gone.kind] += 1

        for spawn in record.get("entities_spawned", ()):
            if spawn.get("entity_type") == WOLF_ENTITY_TYPE:
                wolf_ids.add(spawn.get("entity_id", ""))
                facts.wolves_spawned += 1
                if not seen_first_wolf:
                    seen_first_wolf = True
                    moments.append(
                        Moment(
                            tick,
                            "first_wolf",
                            spawn.get("entity_id", ""),
                            "first wolf appeared",
                        )
                    )

        # A damage record in the same tick names whoever landed the blow.
        attackers = {
            d.get("entity_id", ""): d.get("attacker_id", "")
            for d in record.get("damage", ())
        }

        for death in record.get("deaths", ()):
            entity = death.get("entity_id", "")
            killer = death.get("killer_id", "")
            if is_wolf(entity, entity_types, wolf_ids):
                # A wolf dying is a settler's kill. The despawn a moment later
                # already produces the `wolf_killed` moment, so no death one.
                facts.wolf_kills[killer or "unknown"] += 1
                continue
            facts.deaths[entity] += 1
            bucket.deaths += 1
            build.deaths += 1
            label = death_cause(
                killer, food_now.get(entity, -1), entity_types, wolf_ids
            )
            facts.killers[label] += 1
            moments.append(
                Moment(tick, "death", entity, f"{entity} killed by {killer or label}")
            )
            if last_asleep.get(entity, False) and not seen_first_asleep_death:
                seen_first_asleep_death = True
                moments.append(
                    Moment(
                        tick,
                        "milestone",
                        entity,
                        f"first death while asleep: {entity} "
                        f"killed by {killer or label}",
                    )
                )

        for despawn in record.get("entities_despawned", ()):
            entity = despawn.get("entity_id", "")
            reason = despawn.get("reason", "")
            if not is_wolf(entity, entity_types, wolf_ids):
                continue
            facts.wolves_despawned[reason or "?"] += 1
            if reason != "killed":
                continue
            bucket.wolf_kills += 1
            build.wolf_kills += 1
            killer = attackers.get(entity, "")
            who = f" by {killer}" if killer else ""
            moments.append(
                Moment(tick, "wolf_killed", killer or entity, f"{entity} killed{who}")
            )

        for action in record.get("actions", ()):
            if not action.get("success"):
                if action.get("action_type", "") in CONVERSE_ACTIONS:
                    label = hail_failure_label(action.get("details", ""))
                    if label:
                        facts.hail_failures[label] += 1
                continue
            entity = action.get("entity_id", "")
            action_type = action.get("action_type", "")
            details = action.get("details", "")
            if action_type in CRAFT_ACTIONS:
                kind = craft_kind(details)
                facts.crafts[kind] += 1
                bucket.crafts[kind] += 1
                build.totals_crafts[kind] += 1
                if kind in TIER_CRAFT_KINDS and kind not in seen_tiers:
                    seen_tiers.add(kind)
                    bucket.new_tiers.append(kind)
                if kind in WORKSHOP_RECIPES:
                    facts.workshop_crafts += 1
                if kind in INGOT_KINDS and not seen_first_ingot:
                    seen_first_ingot = True
                    moments.append(
                        Moment(
                            tick,
                            "milestone",
                            entity,
                            f"first ingot: {entity} {details}",
                        )
                    )
                if kind in IRON_TOOL_KINDS and not seen_first_iron_tool:
                    seen_first_iron_tool = True
                    moments.append(
                        Moment(
                            tick,
                            "milestone",
                            entity,
                            f"first iron tool: {entity} {details}",
                        )
                    )
                if kind not in BULK_CRAFTS:
                    moments.append(Moment(tick, "craft", entity, f"{entity} {details}"))
            elif action_type in PLACE_ACTIONS:
                kind = placed_kind(details)
                facts.placements[kind] += 1
                if kind not in BUILDING_KINDS:
                    moments.append(Moment(tick, "place", entity, f"{entity} {details}"))
                elif facts.placements[kind] == 1:
                    moments.append(
                        Moment(
                            tick,
                            "milestone",
                            entity,
                            f"first {kind}: {entity} {details}",
                        )
                    )
            elif action_type in REST_ACTIONS:
                facts.rests += 1
            elif action_type in EXTRACT_ACTIONS:
                match = DISMANTLED_RE.search(details)
                if match:
                    facts.dismantles[match.group(1)] += 1
                else:
                    vein_match = VEIN_YIELD_RE.search(details)
                    if vein_match:
                        facts.vein_yields[vein_match.group(1)] += 1
            elif action_type in SLEEP_ACTIONS:
                match = SLEEP_RE.match(details)
                if match:
                    place = match.group(1)
                    facts.sleeps[
                        "ground" if place == GROUND_SLEEP_PLACE else "bed"
                    ] += 1
            elif action_type in WAKE_ACTIONS:
                match = WAKE_RE.match(details)
                if match:
                    facts.wakes[match.group(1)] += 1
            elif action_type in COLLAPSE_ACTIONS:
                facts.collapses += 1
                if not seen_first_collapse:
                    seen_first_collapse = True
                    moments.append(
                        Moment(
                            tick,
                            "milestone",
                            entity,
                            f"first collapse: {entity} {details}",
                        )
                    )
            elif action_type in NOTE_ACTIONS:
                _scan_note(facts, moments, tick, entity, details)
            elif action_type in CONVERSE_ACTIONS:
                _scan_converse(facts, moments, conversations, tick, entity, details)
            elif action_type in GIVE_ACTIONS:
                match = GAVE_RE.match(details)
                if match:
                    giving.append(
                        GiveEvent(
                            tick=tick,
                            giver=entity,
                            receiver=match.group(3),
                            kind=match.group(2),
                            amount=int(match.group(1)),
                        )
                    )

        if asleep_now:
            last_asleep = asleep_now

    for day in sorted(night_ticks_by_day):
        entries = sorted(night_ticks_by_day[day])
        mid_tick, mid_asleep = entries[len(entries) // 2]
        facts.night_midpoints.append(
            {"day": day, "tick": mid_tick, "asleep": mid_asleep}
        )

    build.buckets = [buckets[i] for i in sorted(buckets)]
    for bucket in build.buckets:
        food, health = last_stats.get(bucket.index, (0.0, 0.0))
        bucket.mean_food = food
        bucket.mean_health = health

    for conv in conversations.values():
        if not conv.joins:
            continue
        joiners = ", ".join(entity for _, entity in conv.joins)
        moments.append(
            Moment(
                conv.opened_tick,
                "conversation_joined",
                conv.opened_by,
                f"{conv.opened_by} opened {conv.conversation_id}, "
                f"joined by {joiners}",
            )
        )

    for give in giving:
        shared = [
            conv
            for conv in conversations.values()
            if conv.active_at(give.giver, give.tick)
            and conv.active_at(give.receiver, give.tick)
        ]
        if shared:
            moments.append(
                Moment(
                    give.tick,
                    "conversation_give",
                    give.giver,
                    f"{give.giver} gave {give.amount} {give.kind} to "
                    f"{give.receiver} during {shared[0].conversation_id}",
                )
            )

    return WorldScan(
        facts=facts,
        moments=moments,
        conversations=conversations,
        giving=giving,
        build=build,
    )


def _scan_note(
    facts: WorldFacts,
    moments: list[Moment],
    tick: int,
    entity: str,
    details: str,
) -> None:
    """A `write_note` action: a sign line, a sign blanking or a board note."""
    sign_match = SIGN_WRITE_RE.match(details)
    if sign_match:
        facts.signs_written += 1
        moments.append(
            Moment(
                tick,
                "sign",
                entity,
                f"{entity} wrote on {sign_match.group(1)}: " f'"{sign_match.group(2)}"',
            )
        )
        return
    if SIGN_CLEAR_RE.match(details):
        facts.signs_cleared += 1
        return
    facts.notes_written += 1
    moments.append(
        Moment(tick, "write_note", entity, f"{entity} wrote a note: {details}")
    )


def _scan_converse(
    facts: WorldFacts,
    moments: list[Moment],
    conversations: dict[str, ConversationRecord],
    tick: int,
    entity: str,
    details: str,
) -> None:
    """A successful `converse` action: an open, a hail, a seating or a join."""
    open_match = CONVERSE_OPEN_RE.match(details)
    if open_match:
        conv = conversations.setdefault(
            open_match.group(1), ConversationRecord(open_match.group(1))
        )
        conv.opened_tick = tick
        conv.opened_by = entity
        conv.participants.add(entity)
        return

    hail_match = CONVERSE_HAIL_RE.match(details)
    if hail_match:
        # The hailer's own success details ("hail conv_N <target>"); the
        # settler it seated reads "hailed conv_N <hailer>" (docs/09 §9).
        conv_id, target = hail_match.group(1), hail_match.group(2)
        conv = conversations.setdefault(conv_id, ConversationRecord(conv_id))
        conv.opened_tick = tick
        conv.opened_by = entity
        conv.via_hail = True
        conv.participants.add(entity)
        conv.participants.add(target)
        facts.hails_succeeded += 1
        moments.append(
            Moment(tick, "hail", entity, f"{entity} hailed {target}, {conv_id}")
        )
        return

    hailed_match = CONVERSE_HAILED_RE.match(details)
    if hailed_match:
        conv_id = hailed_match.group(1)
        conversations.setdefault(conv_id, ConversationRecord(conv_id)).participants.add(
            entity
        )
        return

    join_match = CONVERSE_JOIN_RE.match(details)
    if join_match:
        conv_id = join_match.group(1)
        conv = conversations.setdefault(conv_id, ConversationRecord(conv_id))
        conv.joins.append((tick, entity))
        conv.participants.add(entity)


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
