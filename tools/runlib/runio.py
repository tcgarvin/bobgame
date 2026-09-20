"""Finding a run directory and reading the files in it.

The layout is the one `dev.sh` writes and `docs/07_replay.md` documents:
``runs/<run_id>/`` with ``meta.json``, ``world/`` and ``agents/``.
"""

from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

DEFAULT_VIEWER_URL = "http://localhost:5173"

# Runs recorded before 2026-09-18 call the food stat `hunger`
# (world/src/world/replay/loader.py::_upgrade_food_keys). Newest name first.
FOOD_KEYS = ("food", "hunger")


class RunLayoutError(Exception):
    """A directory is not a run directory this tooling can read."""


def entity_food(update: Mapping[str, Any], default: int = -1) -> int:
    """The food stat of one ``entity_update``, under either recorded name.

    ``default`` is returned when the update carries neither key, which is the
    case for a record written before the stat existed.
    """
    for key in FOOD_KEYS:
        if key in update:
            return int(update[key])
    return default


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield JSON objects from a ``.jsonl.gz`` (or plain ``.jsonl``) file.

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


def read_text(path: Path) -> str:
    """A text file's contents, or "" when it was never written."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class RunLayout:
    """Where the recorded files live for one run."""

    run_id: str
    root: Path
    agents_dir: Path
    ticks_path: Path | None
    meta: dict

    def agent_dir(self, agent_id: str) -> Path:
        return self.agents_dir / f"agent-{agent_id}"


def resolve_run_dir(raw: str | None) -> Path:
    """The run directory named on the command line, or ``runs/latest``."""
    if raw is not None:
        return Path(raw)
    latest = Path("runs/latest")
    if latest.exists():
        return latest.resolve()
    return latest


def load_layout(run_dir: Path) -> RunLayout:
    """Describe a run directory.

    Raises :class:`RunLayoutError` when the directory holds no ``meta.json``,
    which every run recorded by the world server writes first
    (docs/07_replay.md).
    """
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        raise RunLayoutError(
            f"{run_dir} is not a run directory: no meta.json "
            "(expected runs/<run_id>/, see docs/07_replay.md)"
        )
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RunLayoutError(f"{meta_path} is not valid JSON: {error}") from error
    ticks_path = run_dir / "world" / "ticks.jsonl.gz"
    return RunLayout(
        run_id=meta.get("run_id", run_dir.resolve().name),
        root=run_dir,
        agents_dir=run_dir / "agents",
        ticks_path=ticks_path if ticks_path.exists() else None,
        meta=meta,
    )


def agent_ids(layout: RunLayout) -> list[str]:
    """Every agent id with a trace directory, sorted."""
    if not layout.agents_dir.is_dir():
        return []
    return sorted(
        path.name.removeprefix("agent-")
        for path in layout.agents_dir.glob("agent-*")
        if path.is_dir()
    )


def objects_path(layout: RunLayout) -> Path | None:
    """The object baseline recorded at tick 0, when the run has one."""
    path = layout.root / "world" / "objects.jsonl.gz"
    return path if path.exists() else None
