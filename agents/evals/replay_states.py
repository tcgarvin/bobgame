"""Re-ask the current Jev questions about states a real run already recorded.

The eval scenarios are hand-built and therefore optimistic. This CLI takes the
states from a run that actually went wrong - `jev_states.jsonl.gz` holds the
exact state and criteria of every Jev call - and asks today's questions about
them, so "would the new `lost` question have caught tick 500?" has an answer
instead of an opinion.

    uv run python -m evals.replay_states \\
        ../runs/<run>/agents/agent-dov/jev_states.jsonl.gz --from 500 --to 530
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from agents.jev_agent.jevclient import DEFAULT_MODEL, JevDecision, TypeSafeJevClient

API_KEY_VARIABLE = "TYPESAFE_API_KEY"
MODEL_VARIABLE = "JEV_EVAL_MODEL"
REPLAY_TIMEOUT_SECONDS = 30.0
ACTION_COLUMN = 28


@dataclass(frozen=True)
class RecordedState:
    """One recorded Jev call: the state and the options it chose between."""

    tick: int
    state: Mapping[str, Any]
    criteria: Mapping[str, str]


def read_states(path: Path) -> Iterator[RecordedState]:
    """Every recorded call in `path`, oldest first.

    The writer flushes each line, so the last one can be half-written when the
    run was killed; a truncated tail is the end of the file, not an error.
    """
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        while True:
            try:
                line = handle.readline()
            except (EOFError, zlib.error):
                return
            if not line:
                return
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return
            yield RecordedState(
                tick=int(record["tick"]),
                state=record["state"],
                criteria=record["criteria"],
            )


def select(
    states: Iterator[RecordedState], *, first: int, last: int, every: int
) -> list[RecordedState]:
    """The recorded calls in `[first, last]`, thinned to every `every`-th one."""
    if every < 1:
        raise ValueError("--every must be at least 1")
    chosen: list[RecordedState] = []
    for index, state in enumerate(s for s in states if first <= s.tick <= last):
        if index % every == 0:
            chosen.append(state)
    return chosen


def format_row(tick: int, decision: JevDecision) -> str:
    """One line of the output table."""
    action = decision.action[:ACTION_COLUMN]
    return (
        f"{tick:>6}  {action:<{ACTION_COLUMN}}  {decision.confidence:>5.2f}  "
        f"{decision.done:>5.2f}  {decision.stuck:>5.2f}  {decision.lost:>5.2f}  "
        f"{decision.danger:>5.2f}  {decision.latency_ms:>6}"
    )


def header() -> str:
    """The table header, matching `format_row`."""
    return (
        f"{'tick':>6}  {'action':<{ACTION_COLUMN}}  {'conf':>5}  {'done':>5}  "
        f"{'stuck':>5}  {'lost':>5}  {'dngr':>5}  {'ms':>6}\n"
        + "-" * (6 + 2 + ACTION_COLUMN + 2 + 5 * 7 + 8)
    )


async def replay(path: Path, *, first: int, last: int, every: int, model: str) -> int:
    """Ask the live model about each selected state and print the table."""
    selected = select(read_states(path), first=first, last=last, every=every)
    if not selected:
        print(f"no recorded states in ticks {first}-{last} of {path}", file=sys.stderr)
        return 1
    client = TypeSafeJevClient(model=model, timeout=REPLAY_TIMEOUT_SECONDS)
    try:
        print(f"{path}  model={model}  {len(selected)} states")
        print(header())
        for recorded in selected:
            decision = await client.decide(recorded.state, recorded.criteria)
            print(format_row(recorded.tick, decision), flush=True)
    finally:
        await client.aclose()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """The CLI surface."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="a jev_states.jsonl.gz file")
    parser.add_argument(
        "--from", dest="first", type=int, default=0, help="first tick to replay"
    )
    parser.add_argument(
        "--to", dest="last", type=int, default=10**9, help="last tick to replay"
    )
    parser.add_argument(
        "--every", type=int, default=1, help="replay every Nth selected state"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get(MODEL_VARIABLE, DEFAULT_MODEL),
        help=f"model to ask (default: ${MODEL_VARIABLE} or {DEFAULT_MODEL})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: validate the environment, then replay."""
    args = parse_args(argv)
    if not os.environ.get(API_KEY_VARIABLE):
        raise SystemExit(f"{API_KEY_VARIABLE} is not set; this makes real API calls")
    if not args.path.is_file():
        raise SystemExit(f"no such recorded-state file: {args.path}")
    return asyncio.run(
        replay(
            args.path,
            first=args.first,
            last=args.last,
            every=args.every,
            model=args.model,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
