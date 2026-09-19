"""Loading a recorded run directory into memory.

`RunLoader` reads `meta.json`, the object baseline and the tick stream, plus
the light agent trace files. The heavy `jev_states.jsonl.gz` is only indexed
by byte offset here and read on demand.
"""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import structlog

from ..recording import file_sha256, read_jsonl_gz
from ..state import DEFAULT_DAY_LENGTH_TICKS

logger = structlog.get_logger()

# The run index is capped; `say` and `stint_start` entries are dropped first.
MAX_INDEX_EVENTS = 3000

# Kinds dropped first when the index is over the cap, least useful first.
DROPPABLE_KINDS = ("say", "stint_start")

# A live run is still writing its planner trace while the viewer watches it, so
# the trace is re-read when the file's size or mtime has changed - at most this
# often, because the file holds every prompt and is the big one.
PLANNER_REFRESH_INTERVAL_S = 1.0

# The journal's headings, in file order (docs/12_sleep_journal.md). Only used to
# read `memory.md` for runs recorded before the journal was traced.
JOURNAL_SECTIONS = (
    "Story so far",
    "Me",
    "Others",
    "Learnings",
    "Tomorrow",
    "Today's notes",
)

# Planner events that carry a `journal` block.
JOURNAL_EVENTS = ("turn_start", "journal_rewrite")


class RunLoadError(Exception):
    """A run directory is missing or unusable."""


@dataclass
class AgentTraces:
    """The trace files recorded for one agent."""

    entity_id: str
    stints: list[dict[str, Any]] = field(default_factory=list)
    planner: list[dict[str, Any]] = field(default_factory=list)
    memory: str = ""
    # tick -> byte offset of that tick's line in the decompressed jev file
    jev_offsets: dict[int, int] = field(default_factory=dict)
    jev_path: Path | None = None
    # Where the planner trace and the journal came from, for the live re-read.
    planner_path: Path | None = None
    memory_path: Path | None = None
    # (size, mtime_ns) of the planner trace as it was last read, and when it
    # was last stat-ed.
    planner_stat: tuple[int, int] = (0, 0)
    checked_at: float = 0.0

    def refresh(self) -> None:
        """Re-read the planner trace and the journal file if they have changed.

        A run directory being written by a live run keeps growing, and a loader
        is otherwise read once. Stat-ing is cheap, so the file is only re-read
        when its size or mtime moved, and at most every
        `PLANNER_REFRESH_INTERVAL_S`. The stint and Jev files are left as they
        were loaded: the panel's journal and planner turn are what a live run
        needs fresh.
        """
        if self.planner_path is None:
            return
        now = time.monotonic()
        if now - self.checked_at < PLANNER_REFRESH_INTERVAL_S:
            return
        self.checked_at = now
        try:
            stat = self.planner_path.stat()
        except OSError as exc:
            logger.warning(
                "planner_trace_stat_failed", path=str(self.planner_path), error=str(exc)
            )
            return
        key = (stat.st_size, stat.st_mtime_ns)
        if key == self.planner_stat:
            return
        self.planner_stat = key
        self.planner = read_trace_lines(self.planner_path)
        self.memory = read_memory_file(self.memory_path)

    def journal_at(self, tick_id: int) -> dict[str, Any]:
        """The journal as the traces show it at `tick_id`, or {}.

        The latest `turn_start` or `journal_rewrite` at or before `tick_id`
        wins; when the run has no traced journal at all, `memory.md` on disk is
        the fallback for older recordings.
        """
        found: dict[str, Any] = {}
        traced = False
        for line in self.planner:
            sections = line.get("journal")
            if not isinstance(sections, dict):
                continue
            traced = True
            tick = int(line.get("tick", -1))
            if tick > tick_id:
                break
            found = {
                "sections": {str(k): str(v) for k, v in sections.items()},
                "tick": tick,
                "source": str(line.get("event", "")),
            }
        if found or traced:
            return found
        sections = sections_from_markdown(self.memory)
        if not sections:
            return {}
        return {"sections": sections, "tick": None, "source": "memory.md"}

    def jev_state(self, tick_id: int) -> dict[str, Any]:
        """The Jev state line at `tick_id`, or {} when there is none.

        Reads the one line on demand; the heavy file is never held in memory.
        """
        offset = self.jev_offsets.get(tick_id)
        if offset is None or self.jev_path is None:
            return {}
        try:
            with gzip.open(self.jev_path, "rb") as stream:
                stream.seek(offset)
                line = stream.readline()
        except (OSError, EOFError) as exc:
            logger.warning(
                "jev_state_read_failed", path=str(self.jev_path), error=str(exc)
            )
            return {}
        if not line:
            return {}
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning(
                "jev_state_bad_line", path=str(self.jev_path), error=str(exc)
            )
            return {}
        return record if isinstance(record, dict) else {}

    def stint_at(self, tick_id: int) -> dict[str, Any]:
        """The stint_start line in effect at `tick_id`, or {}."""
        current: dict[str, Any] = {}
        for line in self.stints:
            if int(line.get("tick", -1)) > tick_id:
                break
            event = line.get("event")
            if event == "stint_start":
                current = line
            elif event == "stint_end":
                current = {}
        return current

    def record_at(self, tick_id: int) -> dict[str, Any]:
        """The per-tick Jev record at `tick_id` or the latest before it."""
        best: dict[str, Any] = {}
        for line in self.stints:
            if "event" in line:
                continue
            tick = int(line.get("tick", -1))
            if tick > tick_id:
                break
            best = line
        return best

    def planner_turn_at(self, tick_id: int) -> dict[str, Any]:
        """The planner turn in progress at `tick_id`, else the last before it.

        Returns a dict with turn, started_tick, ended_tick, prompt, thought
        and the ordered tool_call/tool_result lines, or {} when there is none.
        """
        turns: dict[int, dict[str, Any]] = {}
        order: list[int] = []
        for line in self.planner:
            turn = line.get("turn")
            if turn is None:
                continue
            turn_id = int(turn)
            if turn_id not in turns:
                turns[turn_id] = {
                    "turn": turn_id,
                    "started_tick": int(line.get("tick", 0)),
                    "ended_tick": -1,
                    "prompt": "",
                    "thought": "",
                    "events": [],
                }
                order.append(turn_id)
            entry = turns[turn_id]
            event = line.get("event")
            if event == "turn_start":
                entry["started_tick"] = int(line.get("tick", 0))
                entry["prompt"] = line.get("prompt", "")
            elif event in ("tool_call", "tool_result"):
                entry["events"].append(line)
            elif event == "turn_end":
                entry["ended_tick"] = int(line.get("tick", 0))
                entry["thought"] = line.get("thought", "")
            elif event == "turn_failed":
                entry["ended_tick"] = int(line.get("tick", 0))
                entry["thought"] = f"failed: {line.get('error', '')}"

        chosen: dict[str, Any] = {}
        for turn_id in order:
            entry = turns[turn_id]
            if entry["started_tick"] > tick_id:
                break
            chosen = entry
            if entry["ended_tick"] < 0 or entry["ended_tick"] >= tick_id:
                break
        return chosen


def read_trace_lines(path: Path) -> list[dict[str, Any]]:
    """Every JSON record in a gzip JSONL trace; [] when it cannot be read."""
    try:
        return list(read_jsonl_gz(path))
    except OSError as exc:
        logger.warning("agent_file_unreadable", path=str(path), error=str(exc))
        return []


def read_memory_file(path: Path | None) -> str:
    """The journal file's text, or "" when there is none to read."""
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("memory_unreadable", path=str(path), error=str(exc))
        return ""


def sections_from_markdown(text: str) -> dict[str, str]:
    """`memory.md` split into its `## ` sections, for runs with no traced journal."""
    if not text.strip():
        return {}
    bodies: dict[str, list[str]] = {name: [] for name in JOURNAL_SECTIONS}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            continue
        if current in bodies:
            bodies[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in bodies.items()}


class RunLoader:
    """All the recorded data for one run, loaded once and kept in memory."""

    def __init__(self, run_dir: Path):
        """
        Raises:
            RunLoadError: If the directory has no usable tick stream.
        """
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.meta = self._load_meta()

        self.tick_records: dict[int, dict[str, Any]] = {}
        self.tick_ids: list[int] = []
        self.agent_status: dict[int, list[dict[str, Any]]] = {}
        self._load_ticks()

        self.agents = self._load_agents()
        self._index: dict[str, Any] = {}

    # --- properties ---

    @property
    def first_tick(self) -> int:
        return self.tick_ids[0]

    @property
    def last_tick(self) -> int:
        return self.tick_ids[-1]

    @property
    def world_width(self) -> int:
        return int(self.meta.get("world_size", {}).get("width", 0))

    @property
    def world_height(self) -> int:
        return int(self.meta.get("world_size", {}).get("height", 0))

    @property
    def tick_duration_ms(self) -> int:
        return int(self.meta.get("tick_duration_ms", 1000))

    @property
    def day_length_ticks(self) -> int:
        """Day length recorded in meta.json; the default for older runs."""
        return int(self.meta.get("day_length_ticks") or DEFAULT_DAY_LENGTH_TICKS)

    @property
    def map_path(self) -> str:
        return self.meta.get("map_path") or ""

    def tick_record(self, tick_id: int) -> dict[str, Any]:
        """The tick record for `tick_id`.

        Raises:
            KeyError: If there is no record for that tick.
        """
        return self.tick_records[tick_id]

    def ticks_in_range(self, start: int, end: int) -> list[dict[str, Any]]:
        """Tick records with start <= tick_id <= end, in order."""
        return [
            self.tick_records[tick] for tick in self.tick_ids if start <= tick <= end
        ]

    def agent_status_up_to(self, tick_id: int) -> list[dict[str, Any]]:
        """The latest agent_status per entity at or before `tick_id`."""
        latest: dict[str, dict[str, Any]] = {}
        for tick, records in sorted(self.agent_status.items()):
            if tick > tick_id:
                break
            for status in records:
                latest[status.get("entity_id", "")] = status
        return list(latest.values())

    def agent_ids(self) -> list[str]:
        """Entity ids that have agent traces or reported a status."""
        ids = set(self.agents)
        for records in self.agent_status.values():
            for status in records:
                ids.add(status.get("entity_id", ""))
        ids.discard("")
        return sorted(ids)

    def agent_detail(self, entity_id: str, tick_id: int) -> dict[str, Any]:
        """The `agent_detail` payload for one entity at one tick.

        The traces are re-read first when the run is still being written, so a
        live run's newest turns and journal rewrites are included
        (docs/07_replay.md).
        """
        traces = self.traces_for(entity_id)
        if traces is None:
            return {
                "stint": None,
                "record": None,
                "jev_state": None,
                "criteria": None,
                "planner_turn": None,
                "memory": "",
                "journal": None,
            }
        traces.refresh()
        jev = traces.jev_state(tick_id)
        return {
            "stint": traces.stint_at(tick_id) or None,
            "record": traces.record_at(tick_id) or None,
            "jev_state": jev.get("state") or None,
            "criteria": jev.get("criteria") or None,
            "planner_turn": traces.planner_turn_at(tick_id) or None,
            "memory": traces.memory,
            "journal": traces.journal_at(tick_id) or None,
        }

    def traces_for(self, entity_id: str) -> AgentTraces | None:
        """The traces for `entity_id`, picking up a directory a live run just made.

        Returns None when the entity has no trace directory at all.
        """
        traces = self.agents.get(entity_id)
        if traces is not None:
            return traces
        if not entity_id or "/" in entity_id or entity_id in (".", ".."):
            return None
        agent_dir = self.run_dir / "agents" / f"agent-{entity_id}"
        if not agent_dir.is_dir():
            return None
        traces = self._load_agent(entity_id, agent_dir)
        self.agents[entity_id] = traces
        return traces

    def run_index(self) -> dict[str, Any]:
        """Notable moments across the run, built once and cached."""
        if not self._index:
            self._index = {
                "type": "run_index",
                "run_id": self.run_id,
                "agents": self.agent_ids(),
                "events": self._build_events(),
            }
        return self._index

    def map_matches(self, project_root: Path) -> bool:
        """Whether the map file still hashes to meta's `map_sha256`."""
        recorded = self.meta.get("map_sha256") or ""
        if not recorded or not self.map_path:
            return True
        map_file = project_root / self.map_path
        try:
            return file_sha256(map_file) == recorded
        except OSError as exc:
            logger.warning("map_hash_failed", path=str(map_file), error=str(exc))
            return False

    # --- loading ---

    def _load_meta(self) -> dict[str, Any]:
        meta_path = self.run_dir / "meta.json"
        if not meta_path.exists():
            raise RunLoadError(f"No meta.json in {self.run_dir}")
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RunLoadError(f"Unreadable meta.json in {self.run_dir}: {exc}")
        if not isinstance(meta, dict):
            raise RunLoadError(f"meta.json in {self.run_dir} is not an object")
        return meta

    def _load_ticks(self) -> None:
        ticks_path = self.run_dir / "world" / "ticks.jsonl.gz"
        if not ticks_path.exists():
            raise RunLoadError(f"No world/ticks.jsonl.gz in {self.run_dir}")
        try:
            records = read_jsonl_gz(ticks_path)
            for record in records:
                kind = record.get("type")
                tick_id = int(record.get("tick_id", -1))
                if tick_id < 0:
                    continue
                if kind == "tick":
                    if tick_id not in self.tick_records:
                        self.tick_ids.append(tick_id)
                    self.tick_records[tick_id] = _upgrade_food_keys(record)
                elif kind == "agent_status":
                    self.agent_status.setdefault(tick_id, []).append(record)
        except OSError as exc:
            raise RunLoadError(f"Unreadable tick stream in {self.run_dir}: {exc}")
        if not self.tick_ids:
            raise RunLoadError(f"No tick records in {self.run_dir}")
        self.tick_ids.sort()

    def iter_objects(self) -> Iterator[dict[str, Any]]:
        """Stream the recorded objects at tick 0.

        The baseline is not held in memory: an island run has hundreds of
        thousands of objects, and each session builds its own World from them.
        """
        objects_path = self.run_dir / "world" / "objects.jsonl.gz"
        if not objects_path.exists():
            logger.warning("run_has_no_objects_file", path=str(objects_path))
            return
        try:
            yield from read_jsonl_gz(objects_path)
        except OSError as exc:
            logger.warning(
                "run_objects_unreadable", path=str(objects_path), error=str(exc)
            )

    def _load_agents(self) -> dict[str, AgentTraces]:
        agents_dir = self.run_dir / "agents"
        if not agents_dir.is_dir():
            return {}
        agents: dict[str, AgentTraces] = {}
        for entry in sorted(agents_dir.iterdir()):
            if not entry.is_dir():
                continue
            entity_id = entry.name.removeprefix("agent-")
            agents[entity_id] = self._load_agent(entity_id, entry)
        return agents

    def _load_agent(self, entity_id: str, agent_dir: Path) -> AgentTraces:
        traces = AgentTraces(entity_id=entity_id)
        stints_path = agent_dir / "stints.jsonl.gz"
        if stints_path.exists():
            traces.stints = read_trace_lines(stints_path)
        planner_path = agent_dir / "planner.jsonl.gz"
        traces.planner_path = planner_path
        traces.memory_path = agent_dir / "memory.md"
        if planner_path.exists():
            traces.planner = read_trace_lines(planner_path)
            stat = planner_path.stat()
            traces.planner_stat = (stat.st_size, stat.st_mtime_ns)
        traces.checked_at = time.monotonic()
        traces.memory = read_memory_file(traces.memory_path)
        jev_path = agent_dir / "jev_states.jsonl.gz"
        if jev_path.exists():
            traces.jev_path = jev_path
            traces.jev_offsets = _index_jev_states(jev_path)
        return traces

    def _build_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        def add(tick_id: int, kind: str, entity_id: str, text: str) -> None:
            events.append(
                {
                    "tick_id": tick_id,
                    "kind": kind,
                    "entity_id": entity_id,
                    "text": text,
                }
            )

        for tick_id in self.tick_ids:
            record = self.tick_records[tick_id]
            for death in record.get("deaths", ()):
                killer = death.get("killer_id", "")
                add(
                    tick_id,
                    "death",
                    death.get("entity_id", ""),
                    f"killed by {killer}" if killer else "died",
                )
            for respawn in record.get("respawns", ()):
                add(tick_id, "respawn", respawn.get("entity_id", ""), "respawned")
            for spawned in record.get("entities_spawned", ()):
                if spawned.get("entity_type") == "wolf":
                    add(
                        tick_id,
                        "wolf_spawned",
                        spawned.get("entity_id", ""),
                        "wolf spawned",
                    )
            for despawned in record.get("entities_despawned", ()):
                if despawned.get("reason") == "killed":
                    add(
                        tick_id,
                        "wolf_killed",
                        despawned.get("entity_id", ""),
                        "wolf killed",
                    )
            for action in record.get("actions", ()):
                action_type = action.get("action_type", "")
                if action_type in ("craft", "place", "write_note") and action.get(
                    "success"
                ):
                    add(
                        tick_id,
                        action_type,
                        action.get("entity_id", ""),
                        action.get("details", ""),
                    )
            for utterance in record.get("utterances", ()):
                if utterance.get("channel") in ("local", "shout"):
                    add(
                        tick_id,
                        "say",
                        utterance.get("speaker_id", ""),
                        utterance.get("text", ""),
                    )

        for entity_id, traces in self.agents.items():
            for line in traces.stints:
                if line.get("event") == "stint_start":
                    brief = line.get("brief") or {}
                    text = (
                        brief.get("instruction", "")
                        if isinstance(brief, dict)
                        else str(brief)
                    )
                    add(int(line.get("tick", 0)), "stint_start", entity_id, text)
            for line in traces.planner:
                if line.get("event") == "turn_end":
                    add(
                        int(line.get("tick", 0)),
                        "planner_turn",
                        entity_id,
                        line.get("thought", ""),
                    )
                elif line.get("event") == "turn_failed":
                    add(
                        int(line.get("tick", 0)),
                        "planner_failed",
                        entity_id,
                        line.get("error", ""),
                    )

        events.sort(key=lambda item: (item["tick_id"], item["kind"]))
        for kind in DROPPABLE_KINDS:
            if len(events) <= MAX_INDEX_EVENTS:
                break
            events = [item for item in events if item["kind"] != kind]
        return events[:MAX_INDEX_EVENTS]


def _upgrade_food_keys(record: dict[str, Any]) -> dict[str, Any]:
    """Rename the legacy `hunger` stat to `food` in a tick's entity updates.

    Runs recorded before 2026-09-18 call the food stat `hunger`/`max_hunger`.
    Entity updates are forwarded to the viewer verbatim, so they are upgraded
    in place as the recording is loaded; the record is returned for chaining.
    """
    for update in record.get("entity_updates", []):
        if not isinstance(update, dict):
            continue
        for legacy, current in (("hunger", "food"), ("max_hunger", "max_food")):
            if legacy in update:
                value = update.pop(legacy)
                update.setdefault(current, value)
    return record


def _index_jev_states(path: Path) -> dict[int, int]:
    """Map tick -> byte offset of that tick's line in the decompressed file."""
    offsets: dict[int, int] = {}
    offset = 0
    try:
        with gzip.open(path, "rb") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    break
                if isinstance(record, dict) and "tick" in record:
                    offsets[int(record["tick"])] = offset
                offset += len(line)
    except (OSError, EOFError) as exc:
        logger.warning("jev_index_truncated", path=str(path), error=str(exc))
    return offsets
