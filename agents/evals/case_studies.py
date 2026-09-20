"""One markdown case study per eval scenario, filled with the run's data.

`dump_scenarios.py` captures what each scenario asked; a matrix run records
what every backend answered. This module joins the two into a folder of case
studies - the exact prompt, the gold answer, and a results table with Jev
first - leaving HTML-comment placeholders where a writer has to say what the
situation is, why it is a good test, what is ambiguous about it, and what the
answers mean.

    uv run python -m evals.case_studies --latest --out ../docs/case_studies
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.jev_agent.jevclient import (
    ACTION_QUESTION,
    DANGER_QUESTION,
    DONE_QUESTION,
    LOST_QUESTION,
    STUCK_QUESTION,
)

from .analyze_matrix import (
    AnalysisError,
    latest_matrix,
    rows_for_run,
    scenario_name,
)

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_SCENARIOS = RESULTS_DIR / "scenarios.json"
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "docs" / "case_studies"

# The baseline row: it goes first in every results table and is bolded.
BASELINE_NAME = "jev"

QUESTIONS: tuple[tuple[str, str], ...] = (
    ("action (Choice over the options above)", ACTION_QUESTION),
    ("done (Noul)", DONE_QUESTION),
    ("stuck (Noul)", STUCK_QUESTION),
    ("lost (Noul)", LOST_QUESTION),
    ("danger (Noul)", DANGER_QUESTION),
)

NOULS: tuple[str, ...] = ("done", "stuck", "lost", "danger")

ERROR_ACTION = "error"

CAVEATS = (
    "Jev returns a real probability distribution over the enumerated options. "
    "A chat model is asked to *write down* what it thinks its probabilities "
    "are, so its confidence and its `done`/`stuck`/`lost`/`danger` numbers are "
    "self-reports: accuracy compares cleanly, calibration does not.",
    "A backend that produced no row for a scenario - an exception, an "
    "unparseable reply, an endpoint that refused the request - is counted as a "
    "failure and shown as `error` in the action column.",
    "The scenarios are hand-built and optimistic: they are states a settler "
    "could plausibly be in, not a sample of the states a real run produces.",
    "Pass rates are over the repeats of one matrix run. They are small "
    "samples; a 67% is two of three.",
)


class CaseStudyError(RuntimeError):
    """A case study that cannot be built."""


# --------------------------------------------------------------------------
# Joining the scenario dumps to a matrix run's rows.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class BackendResult:
    """What one backend did on one scenario, over every repeat."""

    name: str
    backend: str
    repeats: int
    rows: tuple[Mapping[str, Any], ...]
    pass_rate: float

    @property
    def errors(self) -> int:
        """Repeats that produced no row at all."""
        return max(0, self.repeats - len(self.rows))

    def ordered_rows(self) -> list[Mapping[str, Any]]:
        """This backend's rows for the scenario, in repeat order."""
        return sorted(self.rows, key=lambda row: int(row.get("repeat", 0) or 0))

    def actions(self) -> list[str]:
        """The chosen action per repeat, errors included, repeat order."""
        chosen = [str(row.get("action", "")) for row in self.ordered_rows()]
        return chosen + [ERROR_ACTION] * self.errors

    def top_two(self) -> list[str]:
        """The two highest-ranked keys per repeat, as `first > second`.

        Several scenarios pass on the top two, so the second key is part of
        the answer rather than a detail; a repeat that produced no row is
        `error`.
        """
        rendered: list[str] = []
        for row in self.ordered_rows():
            ranked = [str(pair[0]) for pair in row.get("top", []) if pair][:2]
            if not ranked:
                ranked = [str(row.get("action", ""))]
            rendered.append(" > ".join(f"`{key}`" for key in ranked))
        return rendered + [f"`{ERROR_ACTION}`"] * self.errors

    def top_one_gold(self, expected: Any) -> str:
        """How often the top-1 action was in the gold set, `2/3`-style.

        Only `test_jev_basics.py` records the gold answer as a list of option
        keys; elsewhere it is prose or absent, and the answer is `-`.
        """
        if not isinstance(expected, list) or not expected:
            return "-"
        gold = {str(key) for key in expected}
        rows = self.ordered_rows()
        if not rows:
            return f"0/{self.repeats}"
        hits = sum(1 for row in rows if str(row.get("action", "")) in gold)
        return f"{hits}/{len(rows) + self.errors}"

    def mean(self, field: str) -> float | None:
        """Mean of one numeric column, or None when there is no row."""
        if not self.rows:
            return None
        return statistics.fmean(float(row.get(field, 0.0) or 0.0) for row in self.rows)


def collapse(items: Sequence[str]) -> str:
    """`a, a, b` as `a x2, b`, keeping first-seen order.

    The items are rendered already, because a cell holds `` `a` > `b` `` as
    readily as a bare action key.
    """
    if not items:
        return "-"
    counts = Counter(items)
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return ", ".join(
        item + (f" x{counts[item]}" if counts[item] > 1 else "") for item in seen
    )


def backend_results(
    matrix: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    scenario: str,
) -> list[BackendResult]:
    """One `BackendResult` per model in the matrix, Jev first.

    `scenario` is the bare name (no `test_` prefix), as `scenario_name` gives
    it. Models with no rows at all are kept, as all-error rows.
    """
    by_backend: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if scenario_name(row) != scenario:
            continue
        by_backend.setdefault(str(row.get("backend", "")), []).append(row)
    results: list[BackendResult] = []
    for model in matrix.get("models", []):
        backend = str(model.get("backend", ""))
        scenarios = model.get("scenarios", {})
        entry = scenarios.get(f"test_{scenario}", scenarios.get(scenario, {}))
        results.append(
            BackendResult(
                name=str(model.get("name", backend)),
                backend=backend,
                repeats=int(model.get("repeats", 0) or 0),
                rows=tuple(by_backend.get(backend, ())),
                pass_rate=float(entry.get("pass_rate", 0.0) or 0.0),
            )
        )
    results.sort(key=lambda result: (result.name != BASELINE_NAME,))
    return results


# --------------------------------------------------------------------------
# Rendering.
# --------------------------------------------------------------------------
def humanise(name: str) -> str:
    """`test_eats_when_starving` -> `Eats when starving`."""
    bare = scenario_name({"scenario": name})
    words = bare.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def writer(hint: str) -> str:
    """A placeholder telling a writer what belongs in this section."""
    return f"<!-- WRITER: {hint} -->"


def _fence(text: str, language: str = "") -> list[str]:
    """One fenced block, as lines."""
    return [f"```{language}", text.rstrip("\n"), "```", ""]


def _brief_lines(state: Mapping[str, Any]) -> list[str]:
    """The brief block as a short list."""
    brief = state.get("brief", {})
    lines = [
        f"- **Instruction**: {brief.get('instruction', '')}",
        f"- **Success condition**: {brief.get('success_condition', '')}",
    ]
    places = brief.get("places")
    if places:
        rendered = ", ".join(f"`{name}` ({where})" for name, where in places.items())
        lines.append(f"- **Named places**: {rendered}")
    notes = state.get("notes")
    if notes:
        lines.append(f"- **Notes**: {notes}")
    hails = brief.get("hails")
    if hails:
        lines.append(f"- **Hails**: {'; '.join(hails)}")
    lines.append("")
    return lines


def _state_lines(state: Mapping[str, Any]) -> list[str]:
    """The map, the facts and the rest of the state as JSON."""
    rest = {key: value for key, value in state.items() if key not in ("map", "facts")}
    lines: list[str] = ["**The map Jev was shown** (17x17, `@` is the settler):", ""]
    lines += _fence(str(state.get("map", "")), "text")
    legend = state.get("map_legend")
    if legend:
        lines += [f"Legend: {legend}", ""]
    lines += ["**The facts every state carries**:", ""]
    for fact in state.get("facts", []):
        lines.append(f"- {fact}")
    lines.append("")
    lines += ["**The rest of the state**, as sent (JSON):", ""]
    lines += _fence(json.dumps(rest, indent=2, sort_keys=False), "json")
    return lines


def _options_lines(criteria: Mapping[str, str]) -> list[str]:
    """The legal options as a two-column table."""
    lines = [
        "**The options it had to choose between**:",
        "",
        "| key | what it does |",
        "| --- | --- |",
    ]
    for key, description in criteria.items():
        lines.append(f"| `{key}` | {description} |")
    lines.append("")
    return lines


def _questions_lines() -> list[str]:
    """The five questions, verbatim from `jevclient.py`."""
    lines = [
        "### The five questions asked of every backend",
        "",
        "| question | wording |",
        "| --- | --- |",
    ]
    for label, text in QUESTIONS:
        lines.append(f"| {label} | {text} |")
    lines.append("")
    return lines


def _results_lines(results: Sequence[BackendResult], expected: Any) -> list[str]:
    """The per-backend table, then the distinct-action tally."""
    lines = [
        "| backend | pass rate | top two per repeat | top-1 = gold | confidence "
        "| done | stuck | lost | danger | errors |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        name = f"**{result.name}**" if result.name == BASELINE_NAME else result.name
        cells = [
            name,
            f"{result.pass_rate:.0%}",
            collapse(result.top_two()),
            result.top_one_gold(expected),
            _number(result.mean("confidence")),
            *[_number(result.mean(noul)) for noul in NOULS],
            str(result.errors),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    tally: Counter[str] = Counter()
    for result in results:
        tally.update(result.actions())
    lines += [
        "**What every backend chose (top-1), all repeats together**:",
        "",
        "| action | times chosen |",
        "| --- | --- |",
    ]
    for action, count in tally.most_common():
        lines.append(f"| `{action}` | {count} |")
    lines.append("")
    return lines


def _number(value: float | None) -> str:
    """A mean as two decimals, or a dash when there was no row."""
    return "-" if value is None else f"{value:.2f}"


def _expected_text(expected: Any) -> str:
    """The gold answer, whether it was a list of keys or a line of prose."""
    if isinstance(expected, list):
        return (
            ", ".join(f"`{key}`" for key in expected) if expected else "_none recorded_"
        )
    if isinstance(expected, str) and expected.strip():
        return expected
    return "_none recorded; the test's own assertions are the gold answer_"


def render_case_study(
    scenario: Mapping[str, Any],
    results: Sequence[BackendResult],
    number: int,
) -> str:
    """One case study, data filled in and prose left to a writer."""
    name = str(scenario.get("name", ""))
    docstring = str(scenario.get("docstring", "")).strip()
    summary = " ".join(line.strip() for line in docstring.splitlines()).strip()
    state = scenario.get("state", {})
    lines: list[str] = [
        f"# {number:02d}. {humanise(name)}",
        "",
        summary,
        "",
        f"*Test*: `{scenario.get('nodeid', name)}`"
        + ("  ·  **expected to fail** (`xfail`)" if scenario.get("xfail") else ""),
        "",
    ]
    lines += [
        "## The situation",
        "",
        writer(
            "what the settler faces, in plain words - where it is, what it "
            "carries, what is around it and what it has been told to do"
        ),
        "",
        "## Why this is a good test",
        "",
        writer(
            "what judgement this scenario isolates, why the wrong answers are "
            "tempting, and what a failure here would mean for a real run"
        ),
        "",
        "## What the model saw",
        "",
    ]
    lines += _brief_lines(state)
    lines += _state_lines(state)
    lines += _options_lines(scenario.get("criteria", {}))
    lines += _questions_lines()

    lines += [
        "## What we expect",
        "",
        f"**Gold answer**: {_expected_text(scenario.get('expected'))}",
        "",
        "The assertions the test makes:",
        "",
    ]
    lines += _fence(str(scenario.get("assertions", "")), "python")
    lines += [
        writer(
            "the ambiguities - which other options a reasonable settler could "
            "defend, where the gold answer is a convention rather than a fact, "
            "and what the nouls are being asked to mean here"
        ),
        "",
        "## Results",
        "",
    ]
    lines += _results_lines(results, scenario.get("expected"))
    lines += [
        "## Notes",
        "",
        writer(
            "which model did best and by how much, the common failure modes "
            "behind the wrong actions above, and what this says about Jev"
        ),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def file_name(number: int, name: str) -> str:
    """`03_eats_when_starving.md`."""
    bare = scenario_name({"scenario": name})
    safe = re.sub(r"[^a-z0-9_]+", "_", bare.lower()).strip("_")
    return f"{number:02d}_{safe}.md"


def render_index(
    matrix: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
    names: Sequence[str],
) -> str:
    """The folder's README: the index, the summary table and the caveats."""
    run_id = str(matrix.get("run_id", "unknown"))
    lines = [
        "# Jev vs chat models: scenario case studies",
        "",
        f"One case study per eval scenario ({len(scenarios)} of them), built "
        f"from matrix run `{run_id}`. Each one carries the exact state and "
        "options the models were shown, the gold answer, and what every "
        "backend actually chose.",
        "",
        "## The case studies",
        "",
        "| # | case study | scenario | source |",
        "| --- | --- | --- | --- |",
    ]
    for index, (scenario, file) in enumerate(zip(scenarios, names), start=1):
        name = str(scenario.get("name", ""))
        marker = " (xfail)" if scenario.get("xfail") else ""
        lines.append(
            f"| {index:02d} | [{humanise(name)}]({file}){marker} | `{name}` "
            f"| `{scenario.get('file', '')}` |"
        )
    lines += [
        "",
        "## Overall, across every scenario",
        "",
        "| model | backend | pass rate | errors | $/call | mean in tok |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for model in matrix.get("models", []):
        name = str(model.get("name", ""))
        label = f"**{name}**" if name == BASELINE_NAME else name
        lines.append(
            f"| {label} | `{model.get('backend', '')}` "
            f"| {float(model.get('pass_rate', 0.0)):.0%} "
            f"| {int(model.get('errors', 0))} "
            f"| ${float(model.get('mean_cost_usd', 0.0)):.6f} "
            f"| {float(model.get('mean_input_tokens', 0.0)):.0f} |"
        )
    latency = str(matrix.get("latency_caveat", ""))
    lines += ["", "## Caveats", ""]
    for caveat in CAVEATS:
        lines.append(f"- {caveat}")
    if latency:
        lines.append(f"- Latency is wall-clock over an ordinary network: {latency}.")
    lines += [
        "",
        writer("a paragraph of overall findings once the case studies are written"),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def load_matrix_report(path: Path) -> dict[str, Any]:
    """Read a `matrix-<stamp>.json` report.

    Raises:
        CaseStudyError: the file is missing or is not a matrix report.
    """
    if not path.is_file():
        raise CaseStudyError(f"no matrix report at {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or "models" not in report:
        raise CaseStudyError(f"{path} is not a matrix report")
    return report


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    """Read `scenarios.json` from `dump_scenarios.py`.

    Raises:
        CaseStudyError: the file is missing or empty.
    """
    if not path.is_file():
        raise CaseStudyError(
            f"no scenario dump at {path}; run `python -m evals.dump_scenarios` first"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise CaseStudyError(f"{path} holds no scenarios")
    return data


def write_case_studies(
    matrix: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    out_dir: Path,
) -> list[Path]:
    """Write one file per scenario plus the index, and return the paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    names: list[str] = []
    for index, scenario in enumerate(scenarios, start=1):
        name = str(scenario.get("name", ""))
        results = backend_results(matrix, rows, scenario_name({"scenario": name}))
        text = render_case_study(scenario, results, index)
        path = out_dir / file_name(index, name)
        path.write_text(text, encoding="utf-8")
        written.append(path)
        names.append(path.name)
    index_path = out_dir / "README.md"
    index_path.write_text(render_index(matrix, scenarios, names), encoding="utf-8")
    written.append(index_path)
    return written


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """The command line."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--matrix", type=Path, default=None)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Build the case-study folder. Returns a process exit code."""
    import sys

    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.matrix is None and not args.latest:
        print("pass --matrix <path> or --latest", file=sys.stderr)
        return 2
    try:
        matrix_path = (
            latest_matrix(args.results_dir) if args.matrix is None else args.matrix
        )
        matrix = load_matrix_report(matrix_path)
        scenarios = load_scenarios(args.scenarios)
        rows = rows_for_run(args.results_dir, str(matrix.get("run_id", "")))
    except (AnalysisError, CaseStudyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if not rows:
        print(
            f"warning: no result rows for run {matrix.get('run_id')!r} in "
            f"{args.results_dir}; every backend will show as an error",
            file=sys.stderr,
        )
    written = write_case_studies(matrix, rows, scenarios, args.out)
    print(f"{len(written)} files -> {args.out} (matrix {matrix_path.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
