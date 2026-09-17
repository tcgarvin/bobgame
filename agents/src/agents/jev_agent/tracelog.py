"""Gzip JSONL trace files for one agent process.

Every agent writes three files under `<log_root>/agent-<id>/`, described in
[docs/07_replay.md](../../../../docs/07_replay.md): the light `stints.jsonl.gz`,
the heavy `jev_states.jsonl.gz`, and `planner.jsonl.gz`. Each file is opened
once and flushed with `zlib.Z_SYNC_FLUSH` after every line, so a reader sees
everything written up to the moment the process was killed.

Tracing is never allowed to take the agent down: a writer that cannot open or
write its file complains once and then discards its lines.
"""

from __future__ import annotations

import gzip
import json
import os
import zlib
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import structlog

logger = structlog.get_logger(__name__)

RUN_DIR_ENV = "BOBGAME_RUN_DIR"
DEFAULT_LOG_ROOT = Path("logs")

STINTS_FILE = "stints.jsonl.gz"
JEV_STATES_FILE = "jev_states.jsonl.gz"
PLANNER_FILE = "planner.jsonl.gz"
MEMORY_FILE = "memory.md"


class TraceWriter(Protocol):
    """One append-only stream of JSON records."""

    def write(self, payload: Mapping[str, Any]) -> None:
        """Append one JSON object as a line."""

    def close(self) -> None:
        """Release the underlying file."""


class NullWriter:
    """A `TraceWriter` that discards everything, for tests and disabled tracing."""

    def write(self, payload: Mapping[str, Any]) -> None:
        """Discard the record."""

    def close(self) -> None:
        """Nothing to release."""


class JsonlGzWriter:
    """One gzip JSONL file, opened once and flushed after every line.

    The file is a single gzip member: appending by reopening per line would
    produce one member per line and compress nothing. After an OSError the
    writer goes inert - it logs once and every later write is a no-op.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        # None means "inert": the file could not be opened or has failed once.
        self._file: gzip.GzipFile | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = gzip.GzipFile(filename=str(path), mode="ab")
        except OSError as error:
            self._go_inert(error)

    @property
    def active(self) -> bool:
        """False once the writer has given up on its file."""
        return self._file is not None

    def write(self, payload: Mapping[str, Any]) -> None:
        """Append one JSON object as a line and flush it out of the deflate buffer."""
        handle = self._file
        if handle is None:
            return
        line = json.dumps(payload, default=str) + "\n"
        try:
            handle.write(line.encode("utf-8"))
            # Z_SYNC_FLUSH ends the current deflate block without ending the
            # member, so a killed process still leaves a readable file.
            handle.flush(zlib.Z_SYNC_FLUSH)
        except OSError as error:
            self._go_inert(error)

    def close(self) -> None:
        """Close the file; later writes are no-ops."""
        handle = self._file
        self._file = None
        if handle is None:
            return
        try:
            handle.close()
        except OSError as error:
            logger.warning("trace_close_failed", path=str(self.path), error=str(error))

    def _go_inert(self, error: OSError) -> None:
        self._file = None
        logger.warning("trace_write_failed", path=str(self.path), error=str(error))


def _null_writer(path: Path) -> TraceWriter:
    """A writer that discards its records, whatever path it was given."""
    return NullWriter()


class AgentTrace:
    """The three trace files (and the memory file's home) for one entity."""

    def __init__(self, entity_id: str, log_root: Path, *, enabled: bool = True) -> None:
        self.entity_id = entity_id
        self.directory = agent_directory(entity_id, log_root)
        make: Callable[[Path], TraceWriter]
        if enabled:
            make = JsonlGzWriter
        else:
            make = _null_writer
        self.stints: TraceWriter = make(self.directory / STINTS_FILE)
        self.jev_states: TraceWriter = make(self.directory / JEV_STATES_FILE)
        self.planner: TraceWriter = make(self.directory / PLANNER_FILE)

    @classmethod
    def disabled(
        cls, entity_id: str = "", log_root: Path = DEFAULT_LOG_ROOT
    ) -> "AgentTrace":
        """A trace that writes nothing, for tests that do not care about files."""
        return cls(entity_id, log_root, enabled=False)

    @property
    def memory_path(self) -> Path:
        """Where the planner keeps its persistent notes."""
        return self.directory / MEMORY_FILE

    def close(self) -> None:
        """Close all three files."""
        for writer in (self.stints, self.jev_states, self.planner):
            writer.close()


def agent_directory(entity_id: str, log_root: Path = DEFAULT_LOG_ROOT) -> Path:
    """`<log_root>/agent-<id>`, where every file for one agent lives."""
    return log_root / f"agent-{entity_id}"


def resolve_log_root(explicit: str = "") -> Path:
    """An explicit `--log-root`, else `$BOBGAME_RUN_DIR/agents`, else `logs`."""
    if explicit:
        return Path(explicit)
    run_dir = os.environ.get(RUN_DIR_ENV, "")
    if run_dir:
        return Path(run_dir) / "agents"
    return DEFAULT_LOG_ROOT
