"""Run recording: gzip JSONL writers and the world-side run recorder.

See docs/07_replay.md for the contract. A run directory holds `meta.json`,
`world/objects.jsonl.gz` (every object at tick 0) and `world/ticks.jsonl.gz`
(one `tick` or `agent_status` record per line, in the order they happened).

Files are opened once and flushed with `zlib.Z_SYNC_FLUSH` so a reader sees
everything up to the last flush even when the process is killed, which is the
normal way these experiments end.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import structlog

from .chunks import CHUNK_SIZE
from .state import World
from .tick import TickConfig, TickResult
from .viewer_payload import (
    action_payload,
    clock_payload,
    damage_payload,
    death_payload,
    entity_despawned_payload,
    entity_spawned_payload,
    entity_updates,
    move_payload,
    object_change_payload,
    object_state,
    respawn_payload,
    utterance_payload,
)

logger = structlog.get_logger()

FORMAT_VERSION = 1

# How often the world's writers force a gzip sync flush.
FLUSH_INTERVAL_S = 1.0


class JsonlGzWriter:
    """Append-only JSON-lines writer inside a single gzip stream.

    The file is opened once and kept open. `write()` appends one JSON line;
    the stream is sync-flushed at most once every `flush_interval_s` and
    always on `close()`.
    """

    def __init__(self, path: Path, flush_interval_s: float = FLUSH_INTERVAL_S):
        self.path = path
        self.flush_interval_s = flush_interval_s
        self._text = gzip.open(path, "at", encoding="utf-8")
        raw = getattr(self._text, "buffer", None)
        if not isinstance(raw, gzip.GzipFile):
            raise RuntimeError(f"Unexpected gzip stream for {path}")
        self._gzip: gzip.GzipFile = raw
        self._last_flush = time.monotonic()
        self._closed = False

    def write(self, obj: dict[str, Any]) -> None:
        """Append one record. Raises OSError if the file cannot be written."""
        if self._closed:
            raise ValueError(f"Writer for {self.path} is closed")
        self._text.write(json.dumps(obj, separators=(",", ":")) + "\n")
        now = time.monotonic()
        if now - self._last_flush >= self.flush_interval_s:
            self.flush()
            self._last_flush = now

    def flush(self) -> None:
        """Flush the text buffer and sync-flush the gzip stream."""
        self._text.flush()
        self._gzip.flush(zlib.Z_SYNC_FLUSH)

    def close(self) -> None:
        """Flush and close the stream. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        self.flush()
        self._text.close()


def read_jsonl_gz(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a gzip JSONL file, tolerating a truncated tail.

    A run is usually killed rather than stopped cleanly, so the last gzip
    block may be incomplete. Truncation is treated as end of file and logged
    once; every other error propagates.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: For read errors other than a truncated stream.
    """
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        while True:
            try:
                line = stream.readline()
            except (EOFError, zlib.error, gzip.BadGzipFile) as exc:
                logger.warning("jsonl_gz_truncated", path=str(path), error=str(exc))
                return
            except UnicodeDecodeError as exc:
                logger.warning("jsonl_gz_truncated", path=str(path), error=str(exc))
                return
            if not line:
                return
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                # Only the final partial line may be unparseable.
                logger.warning("jsonl_gz_partial_line", path=str(path), error=str(exc))
                return
            if isinstance(record, dict):
                yield record
            else:
                logger.warning("jsonl_gz_non_object_line", path=str(path))


def generate_run_id(config_name: str, now: datetime | None = None) -> str:
    """Return a run id of the form `YYYYMMDD-HHMMSS-<config_name>` (local time)."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{config_name}"


def default_run_dir(project_root: Path, run_id: str) -> Path:
    """Run directory for `run_id`: `$BOBGAME_RUN_DIR` if set, else runs/<run_id>."""
    from_env = os.environ.get("BOBGAME_RUN_DIR", "")
    if from_env:
        return Path(from_env)
    return project_root / "runs" / run_id


def file_sha256(path: Path) -> str:
    """Hex sha256 of a file, read in chunks.

    Raises:
        OSError: If the file cannot be read.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tick_record(result: TickResult, world: World, wall_ms: int) -> dict[str, Any]:
    """Build the `tick` record for a completed tick (docs/07_replay.md)."""
    return {
        "type": "tick",
        "tick_id": result.tick_id,
        "clock": clock_payload(world.clock),
        "wall_ms": wall_ms,
        "duration_ms": result.duration_ms,
        "moves": [move_payload(move) for move in result.move_results],
        "entity_updates": entity_updates(world),
        "object_changes": [
            object_change_payload(change) for change in result.object_changes
        ],
        "objects_added": [object_state(added.obj) for added in result.objects_added],
        "objects_removed": [removed.object_id for removed in result.objects_removed],
        "actions": [action_payload(action) for action in result.action_results],
        "utterances": [utterance_payload(utterance) for utterance in result.utterances],
        "damage": [damage_payload(damage) for damage in result.damage_events],
        "deaths": [death_payload(death) for death in result.deaths],
        "respawns": [respawn_payload(respawn) for respawn in result.respawns],
        "entities_spawned": [
            entity_spawned_payload(spawned) for spawned in result.entities_spawned
        ],
        "entities_despawned": [
            entity_despawned_payload(despawned)
            for despawned in result.entities_despawned
        ],
    }


class RunRecorder:
    """Writes one run directory: meta.json, objects and the tick stream.

    The recorder owns directory creation. `start()` may raise; after that a
    write error disables recording instead of taking the run down with it.
    """

    def __init__(
        self,
        run_dir: Path,
        run_id: str,
        config_name: str,
        config_path: str,
        world: World,
        tick_config: TickConfig,
        wolves: bool = False,
        map_path: str = "",
        project_root: Path | None = None,
        parent_run_id: str = "",
        resumed_from_tick: int = -1,
    ):
        """
        Args:
            run_dir: Directory to write; created if missing.
            run_id: Run id (see `generate_run_id`).
            config_name: Config name, e.g. "settlement".
            config_path: Config path relative to the project root, "" if none.
            world: The world being recorded (read for entities and objects).
            tick_config: Tick timing, copied into meta.json.
            wolves: Whether the run simulates wolves.
            map_path: Map file path relative to the project root, "" if none.
            project_root: Root the relative paths resolve against.
            parent_run_id: The run this one was resumed from, "" for a fresh run.
            resumed_from_tick: The save tick this run continues from, -1 for none.
        """
        self.run_dir = run_dir
        self.run_id = run_id
        self.config_name = config_name
        self.config_path = config_path
        self.world = world
        self.tick_config = tick_config
        self.wolves = wolves
        self.map_path = map_path
        self.project_root = project_root or Path.cwd()
        self.parent_run_id = parent_run_id
        self.resumed_from_tick = resumed_from_tick

        self._started_at = ""
        self._last_tick = -1
        self._ticks: JsonlGzWriter | None = None
        self._disabled = False

    @property
    def meta_path(self) -> Path:
        return self.run_dir / "meta.json"

    @property
    def world_dir(self) -> Path:
        return self.run_dir / "world"

    @property
    def enabled(self) -> bool:
        """False once a write error has disabled recording."""
        return not self._disabled

    def start(self) -> None:
        """Create the directory, write meta.json and the object baseline.

        Raises:
            OSError: If the run directory or its files cannot be written.
        """
        self.world_dir.mkdir(parents=True, exist_ok=True)
        self._started_at = datetime.now().astimezone().isoformat()
        self._write_meta()

        objects = JsonlGzWriter(self.world_dir / "objects.jsonl.gz")
        try:
            for obj in self.world.all_objects().values():
                objects.write(object_state(obj))
        finally:
            objects.close()

        self._ticks = JsonlGzWriter(self.world_dir / "ticks.jsonl.gz")
        logger.info(
            "run_recording_started",
            run_id=self.run_id,
            run_dir=str(self.run_dir),
            objects=self.world.object_count(),
        )

    def record_tick(self, result: TickResult) -> None:
        """Append a `tick` record for a completed tick."""
        record = tick_record(result, self.world, int(time.time() * 1000))
        if self._append(record):
            self._last_tick = result.tick_id

    def record_agent_status(self, status: dict[str, Any]) -> None:
        """Append an `agent_status` record, stamped with the world's tick.

        Args:
            status: The viewer `agent_status` message (without `tick_id`).
        """
        record = dict(status)
        record["type"] = "agent_status"
        record["tick_id"] = self.world.tick
        self._append(record)

    def close(self) -> None:
        """Close the tick stream and update meta.json with the run's end."""
        if self._ticks is not None:
            try:
                self._ticks.close()
            except OSError as exc:
                logger.error(
                    "run_recording_close_failed",
                    path=str(self.world_dir / "ticks.jsonl.gz"),
                    error=str(exc),
                )
            self._ticks = None
        if self._disabled:
            return
        try:
            self._write_meta(finished=True)
        except OSError as exc:
            logger.error(
                "run_recording_meta_failed", path=str(self.meta_path), error=str(exc)
            )

    # --- internals ---

    def _append(self, record: dict[str, Any]) -> bool:
        """Write one record; disable recording on a write error."""
        if self._disabled or self._ticks is None:
            return False
        try:
            self._ticks.write(record)
        except OSError as exc:
            self._disable(self._ticks.path, exc)
            return False
        return True

    def _disable(self, path: Path, exc: OSError) -> None:
        logger.error("run_recording_disabled", path=str(path), error=str(exc))
        self._disabled = True
        self._ticks = None

    def _map_sha256(self) -> str:
        if not self.map_path:
            return ""
        map_file = self.project_root / self.map_path
        try:
            return file_sha256(map_file)
        except OSError as exc:
            logger.warning("map_sha256_failed", path=str(map_file), error=str(exc))
            return ""

    def _write_meta(self, finished: bool = False) -> None:
        """Write meta.json. Raises OSError if it cannot be written."""
        settlement = self.world.settlement
        meta: dict[str, Any] = {
            "format_version": FORMAT_VERSION,
            "run_id": self.run_id,
            "config_name": self.config_name,
            "config_path": self.config_path or None,
            "started_at": self._started_at,
            "finished_at": (
                datetime.now().astimezone().isoformat() if finished else None
            ),
            "last_tick": self._last_tick if finished and self._last_tick >= 0 else None,
            "world_size": {"width": self.world.width, "height": self.world.height},
            "chunk_size": CHUNK_SIZE,
            "tick_duration_ms": self.tick_config.tick_duration_ms,
            "intent_deadline_ms": self.tick_config.intent_deadline_ms,
            "day_length_ticks": self.world.day_length_ticks,
            "settlement": (
                {"x": settlement.x, "y": settlement.y}
                if settlement is not None
                else None
            ),
            "wolves": self.wolves,
            "parent_run_id": self.parent_run_id or None,
            "resumed_from_tick": (
                self.resumed_from_tick if self.resumed_from_tick >= 0 else None
            ),
            "map_path": self.map_path or None,
            "map_sha256": self._map_sha256() or None,
            "entities": [
                {"entity_id": entity.entity_id, "entity_type": entity.entity_type}
                for entity in self.world.all_entities().values()
            ],
        }
        with open(self.meta_path, "w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2)
            handle.write("\n")
