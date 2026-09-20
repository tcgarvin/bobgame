"""Run the live eval suite against a list of models and compare the results.

The suite itself asks one model; this runs it once per (model, repeat) with the
environment variables `backends.py` reads, then reads back every results file
the run wrote and turns them into one report: accuracy per scenario, overall
accuracy, cost per call, latency, tokens, and where each model disagrees with
Jev.

Usage:

    cd agents
    set -a; . ../.env; set +a
    uv run python -m evals.run_matrix --repeats 3
    uv run python -m evals.run_matrix --only jev --repeats 1 \\
        --pytest-args "-k walks_east"

Every pytest run is allowed to fail: a model that gets a scenario wrong is the
measurement, not an error. Scenarios that produced no row at all (an exception,
a reply that could not be parsed) are counted as failures *and* tallied
separately as errors.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Final, Iterable, Mapping, Sequence

from .backends import (
    ELICITATION_MODES,
    JEV_BACKEND,
    OPENROUTER_BACKEND,
    REPEAT_VARIABLE,
    RUN_ID_VARIABLE,
    BackendSpec,
)

HERE: Final = Path(__file__).resolve().parent
DEFAULT_MATRIX: Final = HERE / "matrix.toml"
RESULTS_DIR: Final = HERE / "results"
BASELINE_NAME: Final = "jev"
DEFAULT_REPEATS: Final = 3
# Latency here is wall-clock time to a third-party endpoint over whatever
# network this machine has. It is worth printing and not worth trusting.
LATENCY_CAVEAT: Final = "unreliable network, informational only"


class MatrixError(RuntimeError):
    """A matrix file that cannot be run."""


@dataclass(frozen=True)
class Candidate:
    """One row of the matrix: a name, a backend spec, and whether to run it."""

    name: str
    spec: BackendSpec
    enabled: bool


@dataclass
class Aggregate:
    """Everything one candidate's rows add up to."""

    name: str
    backend: str
    repeats: int = 0
    passed: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    seen: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    costs: list[float] = field(default_factory=list)
    latencies: list[int] = field(default_factory=list)
    input_tokens: list[int] = field(default_factory=list)
    output_tokens: list[int] = field(default_factory=list)
    exit_codes: list[int] = field(default_factory=list)

    def pass_rate(self, scenario: str) -> float:
        """How often `scenario` passed, counting a missing row as a failure."""
        if self.repeats == 0:
            return 0.0
        return self.passed[scenario] / self.repeats

    def errors(self, scenarios: Sequence[str]) -> int:
        """Scenarios that produced no row at all, over every repeat."""
        return max(0, len(scenarios) * self.repeats - sum(self.seen.values()))

    def overall(self, scenarios: Sequence[str]) -> float:
        """The share of all (scenario, repeat) pairs that passed."""
        total = len(scenarios) * self.repeats
        if total == 0:
            return 0.0
        return sum(self.passed[name] for name in scenarios) / total


def load_matrix(path: Path) -> list[Candidate]:
    """Read `matrix.toml` into candidates, in file order.

    Raises:
        MatrixError: the file is missing, has no `[[model]]` entries, or an
            entry is missing a name, a model or a known backend.
    """
    if not path.is_file():
        raise MatrixError(f"no matrix file at {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = data.get("model")
    if not isinstance(entries, list) or not entries:
        raise MatrixError(f"{path} has no [[model]] entries")
    candidates: list[Candidate] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise MatrixError(f"{path}: [[model]] #{index + 1} is not a table")
        candidates.append(_candidate(entry, path, index))
    return candidates


def _candidate(entry: Mapping[str, Any], path: Path, index: int) -> Candidate:
    """One `[[model]]` table as a `Candidate`."""
    where = f"{path}: [[model]] #{index + 1}"
    name = str(entry.get("name", "")).strip()
    model = str(entry.get("model", "")).strip()
    backend = str(entry.get("backend", OPENROUTER_BACKEND)).strip()
    if not name:
        raise MatrixError(f"{where} has no name")
    if not model:
        raise MatrixError(f"{where} ({name}) has no model id")
    if backend not in (JEV_BACKEND, OPENROUTER_BACKEND):
        raise MatrixError(f"{where} ({name}) has unknown backend {backend!r}")
    elicitation = str(entry.get("elicitation", "")).strip()
    if elicitation not in ELICITATION_MODES:
        raise MatrixError(
            f"{where} ({name}) has unknown elicitation {elicitation!r}; "
            f"expected one of {ELICITATION_MODES}"
        )
    extra = entry.get("extra", {})
    if not isinstance(extra, dict):
        raise MatrixError(f"{where} ({name}): 'extra' must be a table")
    return Candidate(
        name=name,
        spec=BackendSpec(
            kind=backend,
            model=model,
            provider=str(entry.get("provider", "")).strip(),
            extra=dict(extra),
            elicitation=elicitation,
        ),
        enabled=bool(entry.get("enabled", True)),
    )


def select(
    candidates: Sequence[Candidate], only: Sequence[str], skip: Sequence[str]
) -> list[Candidate]:
    """Apply `--only` and `--skip` to the matrix, keeping file order.

    Raises:
        MatrixError: a filter names a candidate the matrix does not have, or
            nothing is left to run.
    """
    names = {candidate.name for candidate in candidates}
    for unknown in sorted((set(only) | set(skip)) - names):
        raise MatrixError(f"no candidate named {unknown!r} in the matrix")
    chosen = [
        candidate
        for candidate in candidates
        if (candidate.name in only if only else candidate.enabled)
        and candidate.name not in skip
    ]
    if not chosen:
        raise MatrixError("the filters left no candidates to run")
    return chosen


def run_candidate(
    candidate: Candidate, repeat: int, run_id: str, pytest_args: Sequence[str]
) -> int:
    """Run the suite once for one candidate and repeat. Returns pytest's code."""
    environment = dict(os.environ)
    environment.update(candidate.spec.environment())
    environment[REPEAT_VARIABLE] = str(repeat)
    environment[RUN_ID_VARIABLE] = run_id
    command = [
        "uv",
        "run",
        "pytest",
        "evals",
        "-q",
        "-p",
        "no:cacheprovider",
        *pytest_args,
    ]
    print(
        f"\n=== {candidate.name} (repeat {repeat + 1}) : {candidate.spec.label}",
        flush=True,
    )
    completed = subprocess.run(command, env=environment, cwd=HERE.parent, check=False)
    return completed.returncode


def read_rows(run_id: str) -> list[dict[str, Any]]:
    """Every results row written under `run_id`, from every results file.

    Raises:
        MatrixError: a results file holds a line that is not a JSON object.
    """
    rows: list[dict[str, Any]] = []
    for path in sorted(RESULTS_DIR.glob("*.jsonl")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise MatrixError(f"{path}:{number}: {error}") from error
            if not isinstance(row, dict):
                raise MatrixError(f"{path}:{number}: not a JSON object")
            if row.get("run_id") == run_id:
                rows.append(row)
    return rows


def aggregate(
    candidates: Sequence[Candidate],
    rows: Iterable[Mapping[str, Any]],
    repeats: int,
    exit_codes: Mapping[str, list[int]],
) -> tuple[list[Aggregate], list[str]]:
    """Fold the rows into one aggregate per candidate, plus the scenario list."""
    by_backend = {candidate.spec.label: candidate for candidate in candidates}
    totals = {
        candidate.name: Aggregate(
            name=candidate.name,
            backend=candidate.spec.label,
            repeats=repeats,
            exit_codes=list(exit_codes.get(candidate.name, [])),
        )
        for candidate in candidates
    }
    scenarios: set[str] = set()
    for row in rows:
        candidate = by_backend.get(str(row.get("backend", "")))
        if candidate is None:
            continue
        total = totals[candidate.name]
        scenario = str(row.get("scenario", ""))
        scenarios.add(scenario)
        total.seen[scenario] += 1
        if row.get("passed") is True:
            total.passed[scenario] += 1
        total.costs.append(float(row.get("cost_usd", 0.0) or 0.0))
        total.latencies.append(int(row.get("latency_ms", 0) or 0))
        total.input_tokens.append(int(row.get("input_tokens", 0) or 0))
        total.output_tokens.append(int(row.get("output_tokens", 0) or 0))
    return list(totals.values()), sorted(scenarios)


def render(
    totals: Sequence[Aggregate], scenarios: Sequence[str], run_id: str
) -> tuple[str, dict[str, Any]]:
    """The Markdown report and the machine-readable dump of one matrix run."""
    baseline = next((t for t in totals if t.name == BASELINE_NAME), None)
    lines = [
        f"# Jev eval matrix - {run_id}",
        "",
        f"{len(scenarios)} scenarios, "
        f"{totals[0].repeats if totals else 0} repeats per model.",
        "",
        "## Summary",
        "",
        "| model | backend | pass rate | errors | $/call | $/100 calls "
        f"| latency ms ({LATENCY_CAVEAT}) | in tok | out tok |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    payload: dict[str, Any] = {
        "run_id": run_id,
        "scenarios": list(scenarios),
        "latency_caveat": LATENCY_CAVEAT,
        "models": [],
    }
    for total in totals:
        cost = mean(total.costs) if total.costs else 0.0
        latency = mean(total.latencies) if total.latencies else 0.0
        lines.append(
            f"| {total.name} | {total.backend} | {total.overall(scenarios):.0%} "
            f"| {total.errors(scenarios)} | ${cost:.6f} | ${cost * 100:.4f} "
            f"| {latency:.0f} "
            f"| {mean(total.input_tokens) if total.input_tokens else 0:.0f} "
            f"| {mean(total.output_tokens) if total.output_tokens else 0:.0f} |"
        )
        payload["models"].append(
            {
                "name": total.name,
                "backend": total.backend,
                "repeats": total.repeats,
                "pass_rate": total.overall(scenarios),
                "errors": total.errors(scenarios),
                "mean_cost_usd": cost,
                "cost_usd_per_100_calls": cost * 100,
                "mean_latency_ms": latency,
                "mean_input_tokens": (
                    mean(total.input_tokens) if total.input_tokens else 0.0
                ),
                "mean_output_tokens": (
                    mean(total.output_tokens) if total.output_tokens else 0.0
                ),
                "exit_codes": total.exit_codes,
                "scenarios": {
                    name: {
                        "pass_rate": total.pass_rate(name),
                        "calls": total.seen[name],
                    }
                    for name in scenarios
                },
            }
        )

    lines += [
        "",
        "## Scenarios",
        "",
        "| scenario | " + " | ".join(t.name for t in totals) + " |",
        "| --- |" + " --- |" * len(totals),
    ]
    for scenario in scenarios:
        cells = " | ".join(f"{total.pass_rate(scenario):.0%}" for total in totals)
        lines.append(f"| {scenario} | {cells} |")

    lines += ["", "## Disagreements with Jev", ""]
    if baseline is None:
        lines.append("No `jev` row in this run, so there is nothing to compare to.")
        payload["disagreements"] = []
    else:
        disagreements = _disagreements(totals, baseline, scenarios)
        payload["disagreements"] = disagreements
        if not disagreements:
            lines.append("Every model matched Jev on every scenario.")
        for item in disagreements:
            lines.append(
                f"- `{item['scenario']}`: jev {item['jev_pass_rate']:.0%}, "
                f"{item['model']} {item['pass_rate']:.0%}"
            )

    lines += [
        "",
        "## Caveat",
        "",
        "Jev returns a real distribution over the enumerated options. The chat "
        "models are asked to write down what they think their probabilities "
        "are, so their confidences are self-reports and are not comparable as "
        "calibration. Latency is wall-clock over an ordinary network: "
        f"{LATENCY_CAVEAT}.",
        "",
    ]
    return "\n".join(lines), payload


def _disagreements(
    totals: Sequence[Aggregate], baseline: Aggregate, scenarios: Sequence[str]
) -> list[dict[str, Any]]:
    """Every (model, scenario) whose pass rate differs from Jev's."""
    found: list[dict[str, Any]] = []
    for total in totals:
        if total.name == baseline.name:
            continue
        for scenario in scenarios:
            mine = total.pass_rate(scenario)
            theirs = baseline.pass_rate(scenario)
            if abs(mine - theirs) > 1e-9:
                found.append(
                    {
                        "scenario": scenario,
                        "model": total.name,
                        "pass_rate": mine,
                        "jev_pass_rate": theirs,
                    }
                )
    return found


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--skip", action="append", default=[])
    parser.add_argument(
        "--pytest-args",
        default="",
        help='extra arguments for each pytest run, e.g. "-k walks_east"',
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the matrix and write the report. Returns a process exit code."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.repeats < 1:
        raise MatrixError("--repeats must be at least 1")
    candidates = select(load_matrix(args.matrix), args.only, args.skip)
    pytest_args = args.pytest_args.split()
    run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"

    exit_codes: dict[str, list[int]] = defaultdict(list)
    for candidate in candidates:
        for repeat in range(args.repeats):
            exit_codes[candidate.name].append(
                run_candidate(candidate, repeat, run_id, pytest_args)
            )

    totals, scenarios = aggregate(
        candidates, read_rows(run_id), args.repeats, exit_codes
    )
    report, payload = render(totals, scenarios, run_id)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    markdown_path = RESULTS_DIR / f"matrix-{run_id}.md"
    json_path = RESULTS_DIR / f"matrix-{run_id}.json"
    markdown_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("\n" + report)
    print(f"written: {markdown_path}\n         {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
