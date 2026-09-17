#!/usr/bin/env python3
"""Summarise a settlement run.

Reads a run directory produced by ``dev.sh`` (``runs/<run_id>/``, see
``docs/07_replay.md``) and prints a per-agent and aggregate summary: stint
counts and end reasons, Jev latency and token sizes, action mix, eject and
danger distributions, intent failure rates, planner turn counts and tool usage,
deaths, and the final planner thoughts. It then prints a "notable moments"
section: deaths, wolf kills, crafts, placed objects, notes written and planner
trouble, each with a viewer deep link.

The older flat ``logs/`` layout (plain ``.jsonl`` traces, no ``meta.json``) is
still supported so earlier runs stay readable.

Usage:
    python tools/analyze_run.py [run_dir]      # default: runs/latest
    python tools/analyze_run.py logs           # legacy layout
    python tools/analyze_run.py --json         # machine-readable dump
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import statistics
import sys
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

TOOL_CALL_RE = re.compile(r"planner_tool_call .*?tool=(\S+)")
TURN_RE = re.compile(r"planner_turn_started")
THOUGHT_RE = re.compile(r"planner_thought .*?text=(.*)$")
FAILED_RE = re.compile(r"planner_turn_failed .*?error=(.*)$")
REJECTED_RE = re.compile(r"intent_rejected .*?reason=(\S+)")

DEFAULT_VIEWER_URL = "http://localhost:5173"
DEFAULT_MAX_MOMENTS = 60

# Notable moment kinds, most interesting first. The cap keeps the rarest kinds.
MOMENT_PRIORITY = (
    "death",
    "reflex_death",
    "wolf_killed",
    "planner_failed",
    "history_reset",
    "first_wolf",
    "reflex_during_conversation",
    "conversation_give",
    "conversation_joined",
    "milestone",
    "write_note",
    "place",
    "craft",
)

CRAFT_ACTIONS = frozenset({"craft"})
PLACE_ACTIONS = frozenset({"place"})
NOTE_ACTIONS = frozenset({"write_note"})
REST_ACTIONS = frozenset({"rest"})
EXTRACT_ACTIONS = frozenset({"extract"})
CONVERSE_ACTIONS = frozenset({"converse"})
GIVE_ACTIONS = frozenset({"give"})

# Building kinds (docs/08_building.md). A town lays hundreds of these, so they
# are counted but only the first of each kind becomes a notable moment.
BUILDING_KINDS = frozenset(
    {
        "road", "wood_floor", "stone_floor", "wood_wall", "stone_wall",
        "door", "bed", "chair", "table", "workshop_table",
    }
)
# Bulk intermediates: counted, never a moment of their own.
BULK_CRAFTS = BUILDING_KINDS | frozenset({"plank", "rope"})
WORKSHOP_RECIPES = frozenset(
    {"stone_wall", "stone_floor", "door", "bed", "chair", "table"}
)

# The world writes prose details, e.g. "crafted sword", "placed chest_3 at (1,2)".
CRAFTED_RE = re.compile(r"crafted (\S+)")
PLACED_RE = re.compile(r"placed (\S+?)(?:_\d+)? ")
DISMANTLED_RE = re.compile(r"dismantled (\S+?)(?:_\d+)? ")

# Conversation and give details, docs/09_conversation_and_reflex.md sections 2.3
# and 3: "open conv_12", "join conv_12", "gave 3 stone to mira".
CONVERSE_OPEN_RE = re.compile(r"^open (conv_\S+)$")
CONVERSE_JOIN_RE = re.compile(r"^join (conv_\S+)$")
GAVE_RE = re.compile(r"^gave (\d+) (\S+) to (\S+)$")

# The stint report's stats line, e.g. "stats: hp 12/20, hunger 5/10 -> hp
# 8/20, hunger 3/10" (agents/src/agents/jev_agent/stint.py StintReport.to_text).
STINT_HEALTH_RE = re.compile(r"stats: hp (\d+)/\d+.*? -> hp (\d+)/\d+")

REFLEX_KIND = "reflex"


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield JSON objects from a ``.jsonl`` or ``.jsonl.gz`` file.

    A run that was killed leaves a truncated final gzip block; that is the
    normal case, not an error, so reading stops there instead of raising.
    """
    if path.suffix == ".gz":
        handle = gzip.open(path, "rt", encoding="utf-8", errors="replace")
    else:
        handle = path.open("rt", encoding="utf-8", errors="replace")
    with handle:
        while True:
            try:
                line = handle.readline()
            except (EOFError, zlib.error, gzip.BadGzipFile):
                return
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Only the truncated last line can be invalid; stop there.
                return


def first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def read_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[index]


# --------------------------------------------------------------------------
# run layout
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunLayout:
    """Where the trace files live for one run."""

    run_id: str
    root: Path
    agents_dir: Path
    ticks_path: Path | None
    meta: dict

    @property
    def is_run_dir(self) -> bool:
        return bool(self.meta)


def resolve_run_dir(raw: str | None) -> Path:
    if raw is not None:
        return Path(raw)
    latest = Path("runs/latest")
    if latest.exists():
        return latest.resolve()
    return latest


def load_layout(run_dir: Path) -> RunLayout:
    """Describe a run directory, falling back to the legacy flat layout."""
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        resolved = run_dir.resolve()
        return RunLayout(
            run_id=meta.get("run_id", resolved.name),
            root=run_dir,
            agents_dir=run_dir / "agents",
            ticks_path=first_existing(
                run_dir / "world" / "ticks.jsonl.gz",
                run_dir / "world" / "ticks.jsonl",
            ),
            meta=meta,
        )
    return RunLayout(
        run_id=run_dir.resolve().name,
        root=run_dir,
        agents_dir=run_dir,
        ticks_path=None,
        meta={},
    )


def agent_ids(layout: RunLayout) -> list[str]:
    return sorted(
        p.name.removeprefix("agent-")
        for p in layout.agents_dir.glob("agent-*")
        if p.is_dir()
    )


# --------------------------------------------------------------------------
# per-agent summary
# --------------------------------------------------------------------------


def summarise_planner_file(rows: list[dict]) -> dict:
    """Turn counts, tool usage, thoughts and failures from planner.jsonl.gz."""
    tools: Counter[str] = Counter()
    thoughts: list[str] = []
    turns = 0
    turn_failures = 0
    history_resets = 0
    budget_spent = 0
    budget_hard_stops = 0
    for row in rows:
        event = row.get("event")
        if event == "turn_start":
            turns += 1
        elif event == "tool_call":
            tools[row.get("tool", "?")] += 1
        elif event == "turn_end":
            thought = row.get("thought", "")
            if thought:
                thoughts.append(thought.strip())
        elif event == "turn_failed":
            turn_failures += 1
        elif event == "history_reset":
            history_resets += 1
        elif event == "tool_budget_spent":
            budget_spent += 1
        elif event == "tool_budget_reached":
            budget_hard_stops += 1
    return {
        "planner_turns": turns,
        "planner_turn_failures": turn_failures,
        "history_resets": history_resets,
        "budget_spent": budget_spent,
        "budget_hard_stops": budget_hard_stops,
        "tools": dict(tools.most_common()),
        "thoughts": thoughts,
    }


def summarise_planner_log(log_text: str) -> dict:
    """Same numbers scraped from the structlog output (older runs)."""
    tools: Counter[str] = Counter()
    thoughts: list[str] = []
    turns = 0
    turn_failures = 0
    for line in log_text.splitlines():
        if TURN_RE.search(line):
            turns += 1
        match = TOOL_CALL_RE.search(line)
        if match:
            tools[match.group(1)] += 1
        match = THOUGHT_RE.search(line)
        if match:
            thoughts.append(match.group(1).strip())
        if FAILED_RE.search(line):
            turn_failures += 1
    return {
        "planner_turns": turns,
        "planner_turn_failures": turn_failures,
        "history_resets": 0,
        "budget_spent": 0,
        "budget_hard_stops": 0,
        "tools": dict(tools.most_common()),
        "thoughts": thoughts,
    }


def summarise_agent(agent_id: str, layout: RunLayout) -> dict:
    agent_dir = layout.agents_dir / f"agent-{agent_id}"
    stints_path = first_existing(
        agent_dir / "stints.jsonl.gz", agent_dir / "stints.jsonl"
    )
    planner_path = first_existing(
        agent_dir / "planner.jsonl.gz", agent_dir / "planner.jsonl"
    )
    log_path = layout.agents_dir / f"agent-{agent_id}.log"

    rows = list(iter_jsonl(stints_path)) if stints_path else []
    ticks = [r for r in rows if "action" in r and "top" in r]
    ends = [r for r in rows if r.get("event") == "stint_end"]
    starts = [r for r in rows if r.get("event") == "stint_start"]

    actions = Counter(r["action"].split(":")[0] for r in ticks)
    latencies = [r["latency_ms"] for r in ticks if "latency_ms" in r]
    tokens = [r["input_tokens"] for r in ticks if r.get("input_tokens")]
    ejects = [r["eject"] for r in ticks if "eject" in r]
    dangers = [r["danger"] for r in ticks if "danger" in r]
    top_probs = [r["top"][0][1] for r in ticks if r.get("top")]
    confidences = [r["confidence"] for r in ticks if "confidence" in r]
    failures = sum(1 for r in ticks if r.get("intent_result") not in ("accepted", None))
    end_reasons = Counter(r.get("end_reason", r.get("reason", "?")) for r in ends)

    log_text = read_text(log_path)
    if planner_path:
        planner = summarise_planner_file(list(iter_jsonl(planner_path)))
    else:
        planner = summarise_planner_log(log_text)

    rejected: Counter[str] = Counter()
    for line in log_text.splitlines():
        match = REJECTED_RE.search(line)
        if match:
            rejected[match.group(1)] += 1

    thoughts = planner["thoughts"]
    return {
        "agent": agent_id,
        "stint_ticks": len(ticks),
        "stints": len(ends) or len(starts),
        "end_reasons": dict(end_reasons),
        "actions": dict(actions.most_common()),
        "latency_p50": statistics.median(latencies) if latencies else 0,
        "latency_p95": pct(latencies, 0.95),
        "tokens_mean": statistics.mean(tokens) if tokens else 0,
        "tokens_max": max(tokens) if tokens else 0,
        "eject_mean": statistics.mean(ejects) if ejects else 0,
        "danger_mean": statistics.mean(dangers) if dangers else 0,
        "top_prob_mean": statistics.mean(top_probs) if top_probs else 0,
        "confidence_mean": statistics.mean(confidences) if confidences else 0,
        "intent_failures": failures,
        "planner_turns": planner["planner_turns"],
        "planner_turn_failures": planner["planner_turn_failures"],
        "history_resets": planner["history_resets"],
        "budget_spent": planner["budget_spent"],
        "budget_hard_stops": planner["budget_hard_stops"],
        "tools": planner["tools"],
        "rejected": dict(rejected),
        "last_thought": thoughts[-1] if thoughts else "",
        "thoughts": len(thoughts),
    }


# --------------------------------------------------------------------------
# world facts and notable moments
# --------------------------------------------------------------------------


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


@dataclass
class WorldFacts:
    ticks: int = 0
    first_tick: int = 0
    last_tick: int = 0
    deaths: Counter[str] = field(default_factory=Counter)
    killers: Counter[str] = field(default_factory=Counter)
    wolves_spawned: int = 0
    wolves_despawned: Counter[str] = field(default_factory=Counter)
    crafts: Counter[str] = field(default_factory=Counter)
    placements: Counter[str] = field(default_factory=Counter)
    dismantles: Counter[str] = field(default_factory=Counter)
    workshop_crafts: int = 0
    rests: int = 0
    notes_written: int = 0
    utterances: int = 0
    shouts: int = 0

    def as_dict(self) -> dict:
        return {
            "ticks": self.ticks,
            "first_tick": self.first_tick,
            "last_tick": self.last_tick,
            "deaths": dict(self.deaths),
            "killers": dict(self.killers),
            "wolves_spawned": self.wolves_spawned,
            "wolves_despawned": dict(self.wolves_despawned),
            "crafts": dict(self.crafts),
            "placements": dict(self.placements),
            "dismantles": dict(self.dismantles),
            "workshop_crafts": self.workshop_crafts,
            "rests": self.rests,
            "notes_written": self.notes_written,
            "utterances": self.utterances,
            "shouts": self.shouts,
        }


def scan_world_ticks(
    path: Path,
) -> tuple[WorldFacts, list[Moment], dict[str, ConversationRecord], list[GiveEvent]]:
    """Read world/ticks.jsonl.gz into aggregate counts plus notable moments.

    Also returns the conversation objects seen (keyed by id) and every
    successful `give`, since both the Conversations and Giving report
    sections and several notable-moment kinds need this same single pass.
    """
    facts = WorldFacts()
    moments: list[Moment] = []
    wolf_ids: set[str] = set()
    seen_first_wolf = False
    first_tick_set = False
    conversations: dict[str, ConversationRecord] = {}
    giving: list[GiveEvent] = []

    for record in iter_jsonl(path):
        if record.get("type") != "tick":
            continue
        tick = int(record.get("tick_id", 0))
        facts.ticks += 1
        if not first_tick_set:
            facts.first_tick = tick
            first_tick_set = True
        facts.last_tick = max(facts.last_tick, tick)
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
            record_conv = conversations.setdefault(
                conv_id, ConversationRecord(conv_id)
            )
            record_conv.utterance_ticks.append(tick)
            speaker = utterance.get("speaker_id", "")
            if speaker:
                record_conv.participants.add(speaker)

        for object_id in record.get("objects_removed", ()):
            if object_id in conversations:
                conversations[object_id].end_tick = tick

        for spawn in record.get("entities_spawned", ()):
            if spawn.get("entity_type") == "wolf":
                wolf_ids.add(spawn.get("entity_id", ""))
                facts.wolves_spawned += 1
                if not seen_first_wolf:
                    seen_first_wolf = True
                    moments.append(
                        Moment(tick, "first_wolf", spawn.get("entity_id", ""),
                               "first wolf appeared")
                    )

        # A damage record in the same tick names whoever landed the blow.
        attackers = {
            d.get("entity_id", ""): d.get("attacker_id", "")
            for d in record.get("damage", ())
        }

        for death in record.get("deaths", ()):
            entity = death.get("entity_id", "")
            killer = death.get("killer_id", "") or "unknown"
            facts.deaths[entity] += 1
            facts.killers[killer] += 1
            moments.append(
                Moment(tick, "death", entity, f"{entity} killed by {killer}")
            )

        for despawn in record.get("entities_despawned", ()):
            entity = despawn.get("entity_id", "")
            reason = despawn.get("reason", "")
            if entity in wolf_ids or entity.startswith("wolf"):
                facts.wolves_despawned[reason or "?"] += 1
                if reason == "killed":
                    killer = attackers.get(entity, "")
                    who = f" by {killer}" if killer else ""
                    moments.append(
                        Moment(tick, "wolf_killed", killer or entity,
                               f"{entity} killed{who}")
                    )

        for action in record.get("actions", ()):
            if not action.get("success"):
                continue
            entity = action.get("entity_id", "")
            action_type = action.get("action_type", "")
            details = action.get("details", "")
            if action_type in CRAFT_ACTIONS:
                match = CRAFTED_RE.search(details)
                kind = match.group(1) if match else details or "?"
                facts.crafts[kind] += 1
                if kind in WORKSHOP_RECIPES:
                    facts.workshop_crafts += 1
                if kind not in BULK_CRAFTS:
                    moments.append(
                        Moment(tick, "craft", entity, f"{entity} {details}")
                    )
            elif action_type in PLACE_ACTIONS:
                match = PLACED_RE.search(details)
                kind = match.group(1) if match else details or "?"
                facts.placements[kind] += 1
                if kind not in BUILDING_KINDS:
                    moments.append(
                        Moment(tick, "place", entity, f"{entity} {details}")
                    )
                elif facts.placements[kind] == 1:
                    moments.append(
                        Moment(tick, "milestone", entity,
                               f"first {kind}: {entity} {details}")
                    )
            elif action_type in REST_ACTIONS:
                facts.rests += 1
            elif action_type in EXTRACT_ACTIONS:
                match = DISMANTLED_RE.search(details)
                if match:
                    facts.dismantles[match.group(1)] += 1
            elif action_type in NOTE_ACTIONS:
                facts.notes_written += 1
                moments.append(
                    Moment(tick, "write_note", entity, f"{entity} wrote a note: {details}")
                )
            elif action_type in CONVERSE_ACTIONS:
                open_match = CONVERSE_OPEN_RE.match(details)
                join_match = CONVERSE_JOIN_RE.match(details)
                if open_match:
                    conv_id = open_match.group(1)
                    record_conv = conversations.setdefault(
                        conv_id, ConversationRecord(conv_id)
                    )
                    record_conv.opened_tick = tick
                    record_conv.opened_by = entity
                    record_conv.participants.add(entity)
                elif join_match:
                    conv_id = join_match.group(1)
                    record_conv = conversations.setdefault(
                        conv_id, ConversationRecord(conv_id)
                    )
                    record_conv.joins.append((tick, entity))
                    record_conv.participants.add(entity)
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

    for conv in conversations.values():
        if conv.joins:
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

    return facts, moments, conversations, giving


def planner_moments(agent_id: str, layout: RunLayout) -> list[Moment]:
    """Planner turn failures and history resets, which need the tick numbers."""
    agent_dir = layout.agents_dir / f"agent-{agent_id}"
    planner_path = first_existing(
        agent_dir / "planner.jsonl.gz", agent_dir / "planner.jsonl"
    )
    if planner_path is None:
        return []
    moments = []
    for row in iter_jsonl(planner_path):
        event = row.get("event")
        tick = int(row.get("tick", 0))
        if event == "turn_failed":
            error = str(row.get("error", ""))[:160]
            moments.append(
                Moment(tick, "planner_failed", agent_id, f"{agent_id} turn failed: {error}")
            )
        elif event == "history_reset":
            moments.append(
                Moment(tick, "history_reset", agent_id, f"{agent_id} planner history reset")
            )
    return moments


# --------------------------------------------------------------------------
# conversations, giving and reflexes
# --------------------------------------------------------------------------


def _agent_conversation_ends(agent_id: str, layout: RunLayout) -> Iterator[dict]:
    """`conversation_end` rows from one agent's `conversations.jsonl.gz`.

    Runs recorded before this feature shipped have no such file, so an
    absent file simply yields nothing.
    """
    path = layout.agents_dir / f"agent-{agent_id}" / "conversations.jsonl.gz"
    if not path.exists():
        return
    for row in iter_jsonl(path):
        if row.get("event") == "conversation_end":
            yield row


def summarise_conversations(
    conversations: dict[str, ConversationRecord],
    agent_ids: list[str],
    layout: RunLayout,
    viewer_url: str,
    run_id: str,
) -> dict:
    """The Conversations report section: world facts plus agent-side endings.

    `opened`/`joined`/`utterances`/duration come from the world recording
    (ground truth); `end_reasons` and `notes_written` come from each
    participant's own `conversation_end` report, since ending is a per-actor
    view (docs/09_conversation_and_reflex.md section 4.3).
    """
    end_reasons: Counter[str] = Counter()
    notes_written = 0
    for agent_id in agent_ids:
        for row in _agent_conversation_ends(agent_id, layout):
            end_reasons[row.get("end_reason", "?")] += 1
            if str(row.get("note", "")).strip():
                notes_written += 1

    records = list(conversations.values())
    opened = sum(1 for record in records if record.opened_tick >= 0)
    joined = sum(len(record.joins) for record in records)
    distinct_participants = len(
        {participant for record in records for participant in record.participants}
    )
    utterances = sum(record.utterance_count for record in records)
    durations = [record.duration_ticks for record in records if record.end_tick >= 0]
    line_counts = [record.utterance_count for record in records]

    longest = sorted(
        records, key=lambda r: (r.duration_ticks, r.utterance_count), reverse=True
    )[:3]

    return {
        "opened": opened,
        "joined": joined,
        "distinct_participants": distinct_participants,
        "utterances": utterances,
        "end_reasons": dict(end_reasons),
        "notes_written": notes_written,
        "mean_duration_ticks": statistics.mean(durations) if durations else 0,
        "mean_lines": statistics.mean(line_counts) if line_counts else 0,
        "longest": [
            {
                "conversation_id": record.conversation_id,
                "opened_by": record.opened_by,
                "opened_tick": record.opened_tick,
                "end_tick": record.end_tick,
                "duration_ticks": record.duration_ticks,
                "utterances": record.utterance_count,
                "participants": sorted(record.participants),
                "link": deep_link(
                    viewer_url, run_id, max(record.opened_tick, 0), record.opened_by
                ),
            }
            for record in longest
        ],
    }


def summarise_giving(events: list[GiveEvent]) -> dict:
    """The Giving report section: totals by kind and by giver/receiver pair."""
    kinds: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for event in events:
        kinds[event.kind] += event.amount
        pairs[(event.giver, event.receiver)] += event.amount

    return {
        "count": len(events),
        "kinds": dict(kinds.most_common()),
        "pairs": {
            f"{giver}->{receiver}": amount
            for (giver, receiver), amount in pairs.most_common()
        },
    }


def summarise_reflexes(
    agent_ids: list[str], layout: RunLayout
) -> tuple[dict, list[Moment]]:
    """The Reflexes report section, plus the moments only it can see.

    Combines `planner.jsonl.gz` (when and what a settler registered) with
    `stints.jsonl.gz` (when the reflex fired, on what trigger, over what it
    interrupted, how it ended, and the health change from its report text).
    A run recorded before `kind` existed on `stint_start` has no reflex
    stints at all, which reports cleanly as zero.
    """
    per_agent: dict[str, dict] = {}
    trigger_counts: Counter[str] = Counter()
    interrupted_counts: Counter[str] = Counter()
    end_reason_counts: Counter[str] = Counter()
    health_deltas: list[int] = []
    ticks_to_first_action: list[int] = []
    moments: list[Moment] = []

    for agent_id in agent_ids:
        registered_tick, digest = _first_reflex_registration(agent_id, layout)
        if registered_tick is not None:
            per_agent[agent_id] = {"registered_tick": registered_tick, "digest": digest}

        stints_path = first_existing(
            layout.agents_dir / f"agent-{agent_id}" / "stints.jsonl.gz",
            layout.agents_dir / f"agent-{agent_id}" / "stints.jsonl",
        )
        if stints_path is None:
            continue
        rows = list(iter_jsonl(stints_path))
        starts: dict[str, dict] = {
            row["stint_id"]: row
            for row in rows
            if row.get("event") == "stint_start" and row.get("kind") == REFLEX_KIND
        }
        if not starts:
            continue
        ticks_by_stint: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            stint_id = row.get("stint_id", "")
            if stint_id in starts and "action" in row and "top" in row:
                ticks_by_stint[stint_id].append(row)

        for row in rows:
            if row.get("event") != "stint_end":
                continue
            stint_id = row.get("stint_id", "")
            start_row = starts.get(stint_id)
            if start_row is None:
                continue
            trigger = start_row.get("trigger", "?")
            interrupted = start_row.get("interrupted", "?")
            end_reason = row.get("end_reason", "?")
            trigger_counts[trigger] += 1
            interrupted_counts[interrupted] += 1
            end_reason_counts[end_reason] += 1

            match = STINT_HEALTH_RE.search(str(row.get("report", "")))
            if match:
                health_deltas.append(int(match.group(2)) - int(match.group(1)))

            start_tick = int(start_row.get("tick", 0))
            first_action_tick = next(
                (
                    int(tick_row["tick"])
                    for tick_row in ticks_by_stint.get(stint_id, ())
                    if str(tick_row.get("action", "wait")).split(":")[0] != "wait"
                ),
                None,
            )
            if first_action_tick is not None:
                ticks_to_first_action.append(first_action_tick - start_tick)

            if end_reason == "death":
                moments.append(
                    Moment(
                        int(row.get("tick", start_tick)),
                        "reflex_death",
                        agent_id,
                        f"{agent_id} died during a reflex ({trigger})",
                    )
                )
            if interrupted == "conversation":
                moments.append(
                    Moment(
                        start_tick,
                        "reflex_during_conversation",
                        agent_id,
                        f"{agent_id}'s reflex fired mid-conversation ({trigger})",
                    )
                )

    summary = {
        "agents": per_agent,
        "firings_by_trigger": dict(trigger_counts),
        "firings_by_interrupted": dict(interrupted_counts),
        "end_reasons": dict(end_reason_counts),
        "mean_health_change": (statistics.mean(health_deltas) if health_deltas else 0),
        "mean_ticks_to_first_action": (
            statistics.mean(ticks_to_first_action) if ticks_to_first_action else 0
        ),
    }
    return summary, moments


def _first_reflex_registration(
    agent_id: str, layout: RunLayout
) -> tuple[int | None, str]:
    """The tick and instruction digest of a settler's first `set_reflex` call."""
    planner_path = first_existing(
        layout.agents_dir / f"agent-{agent_id}" / "planner.jsonl.gz",
        layout.agents_dir / f"agent-{agent_id}" / "planner.jsonl",
    )
    if planner_path is None:
        return None, ""
    for row in iter_jsonl(planner_path):
        if row.get("event") != "tool_call" or row.get("tool") != "set_reflex":
            continue
        args = row.get("args") or {}
        instruction = str(args.get("instruction", ""))
        return int(row.get("tick", 0)), instruction[:80]
    return None, ""


def select_moments(moments: list[Moment], limit: int) -> tuple[list[Moment], Counter[str]]:
    """Keep at most ``limit`` moments, dropping the most common kinds first."""
    order = {kind: i for i, kind in enumerate(MOMENT_PRIORITY)}
    ranked = sorted(
        moments, key=lambda m: (order.get(m.kind, len(order)), m.tick)
    )
    kept = ranked[:limit]
    omitted = Counter(m.kind for m in ranked[limit:])
    return sorted(kept, key=lambda m: (m.tick, m.kind)), omitted


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def aggregate(summaries: list[dict]) -> dict:
    total_actions: Counter[str] = Counter()
    total_tools: Counter[str] = Counter()
    total_ends: Counter[str] = Counter()
    for s in summaries:
        total_actions.update(s["actions"])
        total_tools.update(s["tools"])
        total_ends.update(s["end_reasons"])
    latencies = [s["latency_p50"] for s in summaries if s["latency_p50"]]
    return {
        "agents": len(summaries),
        "stint_ticks": sum(s["stint_ticks"] for s in summaries),
        "stints": sum(s["stints"] for s in summaries),
        "end_reasons": dict(total_ends),
        "planner_turns": sum(s["planner_turns"] for s in summaries),
        "planner_turn_failures": sum(s["planner_turn_failures"] for s in summaries),
        "history_resets": sum(s["history_resets"] for s in summaries),
        "budget_spent": sum(s["budget_spent"] for s in summaries),
        "budget_hard_stops": sum(s["budget_hard_stops"] for s in summaries),
        "tools": dict(total_tools.most_common()),
        "actions": dict(total_actions.most_common()),
        "latency_p50_median": statistics.median(latencies) if latencies else 0,
    }


def print_report(
    layout: RunLayout,
    summaries: list[dict],
    totals: dict,
    facts: WorldFacts | None,
    conversation_summary: dict,
    giving_summary: dict,
    reflex_summary: dict,
    moments: list[Moment],
    omitted: Counter[str],
    viewer_url: str,
) -> None:
    print(f"== run summary: {layout.run_id} ({len(summaries)} agents) ==")
    if layout.meta:
        started = layout.meta.get("started_at", "?")
        finished = layout.meta.get("finished_at") or "(unfinished)"
        print(f"config: {layout.meta.get('config_name', '?')}  "
              f"started: {started}  finished: {finished}")
    print(f"stint ticks: {totals['stint_ticks']}")
    print(f"stints: {totals['stints']} ends={totals['end_reasons']}")
    print(f"planner turns: {totals['planner_turns']}"
          f" failures={totals['planner_turn_failures']}"
          f" history_resets={totals['history_resets']}"
          f" tool_budget_spent={totals['budget_spent']}"
          f" tool_budget_hard_stops={totals['budget_hard_stops']}")
    print(f"tool calls: {totals['tools']}")
    print(f"jev actions: {totals['actions']}")
    if totals["latency_p50_median"]:
        print("jev latency p50 (median of agents): "
              f"{totals['latency_p50_median']:.0f} ms")
    print()

    header = (f"{'agent':7s} {'ticks':>5s} {'stints':>6s} {'turns':>5s} {'p50ms':>6s} "
              f"{'p95ms':>6s} {'tok':>5s} {'eject':>5s} {'dang':>5s} {'top':>4s} "
              f"{'fail':>4s} rejected")
    print(header)
    for s in summaries:
        print(
            f"{s['agent']:7s} {s['stint_ticks']:5d} {s['stints']:6d} {s['planner_turns']:5d} "
            f"{s['latency_p50']:6.0f} {s['latency_p95']:6.0f} {s['tokens_mean']:5.0f} "
            f"{s['eject_mean']:5.2f} {s['danger_mean']:5.2f} {s['top_prob_mean']:4.2f} "
            f"{s['intent_failures']:4d} {s['rejected']}"
        )
    print()
    for s in summaries:
        if s["last_thought"]:
            print(f"[{s['agent']}] {s['last_thought'][:400]}")

    if facts is not None:
        print()
        print(f"== world: ticks {facts.first_tick}..{facts.last_tick} "
              f"({facts.ticks} recorded) ==")
        print(f"deaths: {sum(facts.deaths.values())} by={dict(facts.killers)}")
        print(f"wolves: spawned={facts.wolves_spawned} "
              f"despawned={dict(facts.wolves_despawned)}")
        print(f"crafts: {dict(facts.crafts)}")
        print(f"placements: {dict(facts.placements)}")
        print(f"building: dismantles={dict(facts.dismantles)} "
              f"workshop_crafts={facts.workshop_crafts} rests={facts.rests}")
        print(f"notes written: {facts.notes_written}  utterances: {facts.utterances}"
              f"  shouts: {facts.shouts}")
    elif not layout.meta:
        world_log = layout.root / "world.log"
        if world_log.exists():
            text = read_text(world_log)
            deaths = len(re.findall(r"entity_died|death", text))
            wolves = len(re.findall(r"wolf_spawned", text))
            print()
            print(f"world.log: death mentions={deaths} wolf spawns={wolves}")

    print()
    print("== conversations ==")
    if conversation_summary["opened"] or conversation_summary["end_reasons"]:
        print(
            f"opened: {conversation_summary['opened']}  "
            f"joined: {conversation_summary['joined']}  "
            f"distinct participants: {conversation_summary['distinct_participants']}  "
            f"utterances: {conversation_summary['utterances']}"
        )
        print(f"ended by: {conversation_summary['end_reasons']}")
        print(
            f"mean length: {conversation_summary['mean_duration_ticks']:.1f} ticks, "
            f"{conversation_summary['mean_lines']:.1f} lines"
        )
        print(f"notes written: {conversation_summary['notes_written']}")
        if conversation_summary["longest"]:
            print("longest conversations:")
            for entry in conversation_summary["longest"]:
                print(
                    f"  {entry['conversation_id']} opened by {entry['opened_by']} "
                    f"at t{entry['opened_tick']}: {entry['duration_ticks']} ticks, "
                    f"{entry['utterances']} lines, with "
                    f"{', '.join(entry['participants'])}"
                )
                print(f"    {entry['link']}")
    else:
        print("none")

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "run_dir",
        nargs="?",
        default=None,
        help="run directory (default: runs/latest); a legacy logs/ dir also works",
    )
    parser.add_argument(
        "--viewer-url",
        default=DEFAULT_VIEWER_URL,
        help=f"base URL for deep links (default: {DEFAULT_VIEWER_URL})",
    )
    parser.add_argument(
        "--max-moments",
        type=int,
        default=DEFAULT_MAX_MOMENTS,
        help=f"cap on notable moments (default: {DEFAULT_MAX_MOMENTS})",
    )
    parser.add_argument(
        "--json", action="store_true", help="dump the summary and moments as JSON"
    )
    args = parser.parse_args(argv)

    run_dir = resolve_run_dir(args.run_dir)
    if not run_dir.is_dir():
        print(f"no such run directory: {run_dir}", file=sys.stderr)
        return 1

    layout = load_layout(run_dir)
    ids = agent_ids(layout)
    if not ids and layout.ticks_path is None:
        print(f"no agent traces or world recording under {run_dir}", file=sys.stderr)
        return 1

    summaries = [summarise_agent(agent_id, layout) for agent_id in ids]
    totals = aggregate(summaries)
    viewer_url = args.viewer_url.rstrip("/")

    facts: WorldFacts | None = None
    moments: list[Moment] = []
    conversations: dict[str, ConversationRecord] = {}
    giving: list[GiveEvent] = []
    if layout.ticks_path is not None:
        facts, moments, conversations, giving = scan_world_ticks(layout.ticks_path)
    for agent_id in ids:
        moments.extend(planner_moments(agent_id, layout))

    conversation_summary = summarise_conversations(
        conversations, ids, layout, viewer_url, layout.run_id
    )
    giving_summary = summarise_giving(giving)
    reflex_summary, reflex_moments = summarise_reflexes(ids, layout)
    moments.extend(reflex_moments)

    shown, omitted = select_moments(moments, max(0, args.max_moments))

    if args.json:
        payload = {
            "run_id": layout.run_id,
            "run_dir": str(run_dir.resolve()),
            "meta": layout.meta,
            "totals": totals,
            "agents": summaries,
            "world": facts.as_dict() if facts else None,
            "conversations": conversation_summary,
            "giving": giving_summary,
            "reflexes": reflex_summary,
            "moments": [m.as_dict(viewer_url, layout.run_id) for m in shown],
            "moments_omitted": dict(omitted),
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print_report(
        layout,
        summaries,
        totals,
        facts,
        conversation_summary,
        giving_summary,
        reflex_summary,
        shown,
        omitted,
        viewer_url,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
