"""The gzip JSONL trace writers and the log-root resolution rules."""

from __future__ import annotations

import gzip
import json
import zlib
from pathlib import Path
from typing import Any

import pytest

from agents.jev_agent.tracelog import (
    RUN_DIR_ENV,
    AgentTrace,
    JsonlGzWriter,
    resolve_log_root,
)


def read_lines(path: Path) -> list[dict[str, Any]]:
    """Every JSON record in a gzip JSONL file."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_a_writer_round_trips_through_gzip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "trace.jsonl.gz"
    writer = JsonlGzWriter(path)
    writer.write({"event": "one", "n": 1})
    writer.write({"event": "two", "n": 2})
    writer.close()

    assert read_lines(path) == [{"event": "one", "n": 1}, {"event": "two", "n": 2}]


def read_lines_tolerantly(path: Path) -> list[dict[str, Any]]:
    """Read a file whose last gzip member has no end-of-stream marker yet.

    This is how a reader must treat a trace from a process that is still
    running or was killed; see "Gzip JSONL writing" in docs/07_replay.md.
    """
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        try:
            for line in handle:
                if line.endswith("\n") and line.strip():
                    records.append(json.loads(line))
        except (EOFError, zlib.error):
            pass
    return records


def test_records_are_readable_before_the_file_is_closed(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl.gz"
    writer = JsonlGzWriter(path)
    writer.write({"event": "one"})
    try:
        # A killed process leaves exactly this state; the flush must have put
        # the record on disk already.
        assert read_lines_tolerantly(path) == [{"event": "one"}]
    finally:
        writer.close()


def test_a_second_writer_appends_without_breaking_the_file(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl.gz"
    first = JsonlGzWriter(path)
    first.write({"n": 1})
    first.close()

    second = JsonlGzWriter(path)
    second.write({"n": 2})
    second.close()

    assert read_lines(path) == [{"n": 1}, {"n": 2}]


def test_a_writer_that_cannot_open_its_file_goes_inert(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    writer = JsonlGzWriter(blocker / "agent-ada" / "trace.jsonl.gz")
    assert not writer.active
    writer.write({"n": 1})  # must not raise
    writer.close()


def test_a_disabled_trace_writes_nothing(tmp_path: Path) -> None:
    trace = AgentTrace.disabled("ada", tmp_path)
    trace.stints.write({"n": 1})
    trace.close()
    assert not trace.directory.exists()


def test_a_trace_owns_three_files_and_the_memory_path(tmp_path: Path) -> None:
    trace = AgentTrace("ada", tmp_path)
    trace.stints.write({"n": 1})
    trace.jev_states.write({"n": 2})
    trace.planner.write({"n": 3})
    trace.close()

    directory = tmp_path / "agent-ada"
    assert trace.directory == directory
    assert trace.memory_path == directory / "memory.md"
    assert read_lines(directory / "stints.jsonl.gz") == [{"n": 1}]
    assert read_lines(directory / "jev_states.jsonl.gz") == [{"n": 2}]
    assert read_lines(directory / "planner.jsonl.gz") == [{"n": 3}]


def test_an_explicit_log_root_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUN_DIR_ENV, "/runs/20260917-143000-settlement")
    assert resolve_log_root("../logs") == Path("../logs")


def test_the_run_directory_supplies_the_log_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUN_DIR_ENV, "/runs/20260917-143000-settlement")
    assert resolve_log_root() == Path("/runs/20260917-143000-settlement/agents")


def test_without_a_run_directory_the_log_root_is_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUN_DIR_ENV, raising=False)
    assert resolve_log_root() == Path("logs")
    assert resolve_log_root("") == Path("logs")
