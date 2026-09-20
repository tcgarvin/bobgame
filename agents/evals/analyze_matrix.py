"""Turn a matrix run's rows into the comparison the write-up needs.

`run_matrix.py` records one row per (backend, scenario, repeat) and reports
action pass rates. That answers "which action did it pick" and nothing else.
This module answers the other three questions from
`docs/13_jev_vs_chat_evals.md`:

* **Approach A** - do the judgement numbers *order* the world correctly?
  A rank test over hand-labelled positive/negative scenarios, so a model that
  says 0.55/0.45 scores the same as one that says 0.95/0.05.
* **Approach B** - what threshold would that model need, and does it separate
  its own positives from its own negatives at all?
* **Approach C** - do the numbers mean what Jev's mean, on Jev's cut-offs?

plus consistency across repeats and a decile histogram that shows whether a
model uses the range or clusters on 0.1/0.5/0.9.

Usage:

    cd agents
    uv run python -m evals.analyze_matrix --latest
    uv run python -m evals.analyze_matrix evals/results/matrix-<stamp>.json
    uv run python -m evals.analyze_matrix 20260919T023725Z-ec38df
    uv run python -m evals.analyze_matrix --labels

It writes `evals/results/analysis-<stamp>.md` and `.json` and prints the
Markdown.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Mapping, Sequence

HERE: Final = Path(__file__).resolve().parent
RESULTS_DIR: Final = HERE / "results"

NOULS: Final[tuple[str, ...]] = ("done", "stuck", "lost", "danger")

# Jev's own cut-offs, copied from `test_jev_judgement.py`. Approach C asks what
# a chat model's self-reported numbers would do if they were dropped into the
# stint unchanged; "high" means >= the cut-off.
JEV_CUTOFFS: Final[dict[str, float]] = {
    "done": 0.6,  # DONE_HIGH
    "lost": 0.5,  # LOST_HIGH
    "danger": 0.6,  # DANGER_HIGH
    "stuck": 0.5,  # STUCK_OR_LOST_HIGH
}

# The threshold sweep for approach B.
THRESHOLD_STEPS: Final[tuple[float, ...]] = tuple(
    round(0.05 * step, 2) for step in range(1, 20)
)


# --------------------------------------------------------------------------
# The label table.
#
# HAND-MADE, from the scenario docstrings and assertions in
# `test_jev_basics.py`, `test_jev_judgement.py` and `test_jev_situations.py`.
# It is the only judgement call in this file: everything below is arithmetic.
# A POSITIVE scenario is one where the noul *should* be high, a NEGATIVE one
# where it should be low. Names are the pytest function names with the `test_`
# prefix dropped. Edit here, never in the metrics.
# --------------------------------------------------------------------------

# Every scenario in `test_jev_basics.py`: an obvious unfinished job, safe,
# unblocked, and with the target in plain view.
BASICS: Final[tuple[str, ...]] = (
    "walks_east_to_the_tree_it_was_sent_to",
    "walks_toward_the_named_object_not_the_nearer_one",
    "chops_the_tree_it_is_standing_next_to",
    "crafts_the_axe_it_was_told_to_craft",
    "picks_the_berry_off_the_bush_it_stands_on",
    "takes_the_axe_from_the_pile_it_stands_on",
    "equips_the_axe_before_chopping",
    "deposits_into_the_chest_beside_it",
    "places_the_wall_it_carries",
    "attacks_the_wolf_it_was_told_to_fight",
    "eats_when_told_to_eat",
)

# The one basics scenario that is not a danger negative: it is a lone settler
# toe to toe with a full-health wolf, which is exactly what `danger` is for.
_NOT_A_DANGER_NEGATIVE: Final[str] = "attacks_the_wolf_it_was_told_to_fight"


@dataclass(frozen=True)
class Labels:
    """The scenarios where one noul should be high, and where it should be low."""

    positive: tuple[str, ...]
    negative: tuple[str, ...]


LABELS: Final[dict[str, Labels]] = {
    "done": Labels(
        positive=(
            "done_when_inventory_met",
            "done_when_the_wall_is_standing",
            "done_when_the_condition_is_where_you_stand",
        ),
        negative=(
            "not_done_when_inventory_short",
            "planks_do_not_satisfy_a_wood_count",
            *BASICS,
        ),
    ),
    "lost": Labels(
        positive=(
            "lost_when_target_out_of_view",
            "forty_ticks_of_stepping_nowhere_is_blocked",
        ),
        negative=(
            "not_lost_when_target_visible",
            "steps_toward_a_named_place_out_of_view",
            "steps_toward_the_named_river_not_the_pond_it_can_see",
            "steps_toward_a_tree_behind_a_wall_with_a_door",
            "steps_toward_named_bush_through_a_grove",
            "keeps_going_rather_than_abandoning_the_journey",
        ),
    ),
    "danger": Labels(
        positive=(
            "danger_when_low_health_and_wolf",
            "two_wolves_adjacent_and_alone_is_danger",
            "low_health_with_an_ally_on_the_wolf",
        ),
        negative=(
            "keeps_working_with_a_wolf_seven_tiles_off",
            "night_does_not_mean_sleep_when_rested",
            "chops_the_tree_rather_than_picking_the_berry_underfoot",
            *(name for name in BASICS if name != _NOT_A_DANGER_NEGATIVE),
        ),
    ),
    "stuck": Labels(
        positive=(
            "stuck_when_recipe_needs_station",
            "iron_vein_needs_a_better_pickaxe",
        ),
        negative=(
            "gathers_the_missing_fiber_instead_of_crafting",
            *BASICS,
        ),
    ),
}


class AnalysisError(RuntimeError):
    """Rows that cannot be analysed, or a source that cannot be found."""


# --------------------------------------------------------------------------
# Pure metrics. No I/O, no rows: lists of floats in, numbers out.
# --------------------------------------------------------------------------


def pairwise_auc(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """Share of positive x negative pairs the positive wins; ties count a half.

    This is the Mann-Whitney AUC: the approach-A rank test, which needs no
    scale and so compares a 0.55/0.45 model with a 0.95/0.05 one fairly.

    Raises:
        AnalysisError: either side is empty, so no pair exists.
    """
    if not positives or not negatives:
        raise AnalysisError("pairwise accuracy needs at least one of each label")
    wins = 0.0
    for high in positives:
        for low in negatives:
            if high > low:
                wins += 1.0
            elif high == low:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def mean_margin(positives: Sequence[float], negatives: Sequence[float]) -> float:
    """Mean positive score minus mean negative score."""
    if not positives or not negatives:
        raise AnalysisError("a margin needs at least one of each label")
    return statistics.fmean(positives) - statistics.fmean(negatives)


def balanced_accuracy(
    positives: Sequence[float], negatives: Sequence[float], threshold: float
) -> float:
    """Mean of the true-positive and true-negative rates at `threshold` (>=)."""
    if not positives or not negatives:
        raise AnalysisError("balanced accuracy needs at least one of each label")
    true_positive = sum(1 for value in positives if value >= threshold) / len(positives)
    true_negative = sum(1 for value in negatives if value < threshold) / len(negatives)
    return (true_positive + true_negative) / 2.0


@dataclass(frozen=True)
class ThresholdFit:
    """The best cut-off a model could be given for one noul, and how well it does."""

    threshold: float
    balanced_accuracy: float
    gap: float


def best_threshold(
    positives: Sequence[float],
    negatives: Sequence[float],
    steps: Sequence[float] = THRESHOLD_STEPS,
) -> ThresholdFit:
    """Sweep `steps` and keep the cut-off with the best balanced accuracy.

    Ties go to the threshold nearest 0.5, which is the least surprising choice
    when a model's scores are flat. `gap` is the lowest positive minus the
    highest negative: negative means the two classes overlap and no threshold
    separates them cleanly.
    """
    if not steps:
        raise AnalysisError("a threshold sweep needs at least one step")
    scored = [
        (balanced_accuracy(positives, negatives, step), -abs(step - 0.5), step)
        for step in steps
    ]
    accuracy, _, threshold = max(scored)
    return ThresholdFit(
        threshold=threshold,
        balanced_accuracy=accuracy,
        gap=min(positives) - max(negatives),
    )


def worst_pair(
    positives: Mapping[str, float], negatives: Mapping[str, float]
) -> tuple[str, str, float]:
    """The (positive, negative, margin) pair with the smallest margin.

    Raises:
        AnalysisError: either side is empty.
    """
    if not positives or not negatives:
        raise AnalysisError("a worst pair needs at least one of each label")
    pairs = [
        (high_score - low_score, high_name, low_name)
        for high_name, high_score in positives.items()
        for low_name, low_score in negatives.items()
    ]
    margin, high_name, low_name = min(pairs)
    return high_name, low_name, margin


def decile_histogram(values: Sequence[float]) -> list[int]:
    """Counts of `values` in the ten deciles of 0..1; 1.0 lands in the last."""
    counts = [0] * 10
    for value in values:
        index = min(9, max(0, int(value * 10)))
        counts[index] += 1
    return counts


@dataclass(frozen=True)
class Consistency:
    """How stable one backend is across repeats of the same scenario."""

    scenarios_compared: int
    stable_action_fraction: float
    noul_stdev: dict[str, float]
    mean_confidence: float


def consistency(rows: Sequence[Mapping[str, Any]]) -> Consistency:
    """Action stability, per-scenario noul spread and mean confidence.

    Only scenarios with more than one row can disagree with themselves, so the
    stable fraction and the standard deviations are taken over those;
    `scenarios_compared` says how many there were.
    """
    by_scenario: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scenario[scenario_name(row)].append(row)
    repeated = {name: group for name, group in by_scenario.items() if len(group) > 1}
    stable = sum(
        1
        for group in repeated.values()
        if len({str(row.get("action", "")) for row in group}) == 1
    )
    spreads: dict[str, float] = {}
    for noul in NOULS:
        per_scenario = [
            statistics.stdev([float(row.get(noul, 0.0) or 0.0) for row in group])
            for group in repeated.values()
        ]
        spreads[noul] = statistics.fmean(per_scenario) if per_scenario else 0.0
    confidences = [float(row.get("confidence", 0.0) or 0.0) for row in rows]
    return Consistency(
        scenarios_compared=len(repeated),
        stable_action_fraction=stable / len(repeated) if repeated else 0.0,
        noul_stdev=spreads,
        mean_confidence=statistics.fmean(confidences) if confidences else 0.0,
    )


def scenario_name(row: Mapping[str, Any]) -> str:
    """The row's scenario with the pytest `test_` prefix dropped."""
    name = str(row.get("scenario", ""))
    return name[5:] if name.startswith("test_") else name


def scenario_means(rows: Iterable[Mapping[str, Any]], noul: str) -> dict[str, float]:
    """Mean `noul` per scenario, so every scenario counts once whatever repeats."""
    gathered: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        gathered[scenario_name(row)].append(float(row.get(noul, 0.0) or 0.0))
    return {name: statistics.fmean(values) for name, values in gathered.items()}


@dataclass(frozen=True)
class TopOneAccuracy:
    """Top-1 correctness, and how much of it had to fall back to `passed`."""

    determined: int
    correct: int
    fallback: int
    fallback_passed: int

    @property
    def rate(self) -> float:
        """Correct share over every row, gold-determined or fallen back."""
        total = self.determined + self.fallback
        if total == 0:
            return 0.0
        return (self.correct + self.fallback_passed) / total


def top_one_accuracy(rows: Sequence[Mapping[str, Any]]) -> TopOneAccuracy:
    """Was the chosen action in the row's recorded gold set?

    `thresholds.expected` is a list of option keys in `test_jev_basics.py`, so
    top-1 is decidable there. In `test_jev_situations.py` it is the gold answer
    in prose and in `test_jev_judgement.py` it is absent; those rows fall back
    to the test's own verdict, and the counts say how many did.
    """
    determined = correct = fallback = fallback_passed = 0
    for row in rows:
        thresholds = row.get("thresholds", {})
        expected = thresholds.get("expected") if isinstance(thresholds, dict) else None
        if isinstance(expected, list) and expected:
            determined += 1
            if str(row.get("action", "")) in {str(key) for key in expected}:
                correct += 1
        else:
            fallback += 1
            if row.get("passed") is True:
                fallback_passed += 1
    return TopOneAccuracy(determined, correct, fallback, fallback_passed)


def pass_rate(rows: Sequence[Mapping[str, Any]]) -> float:
    """Share of rows whose test passed."""
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get("passed") is True) / len(rows)


def mean_cost(rows: Sequence[Mapping[str, Any]]) -> float:
    """Mean `cost_usd` per call."""
    if not rows:
        return 0.0
    return statistics.fmean(float(row.get("cost_usd", 0.0) or 0.0) for row in rows)


# --------------------------------------------------------------------------
# Folding rows into one analysis per backend.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NoulAnalysis:
    """Approaches A, B and C for one backend and one noul."""

    noul: str
    positives: dict[str, float]
    negatives: dict[str, float]
    missing_positives: tuple[str, ...]
    missing_negatives: tuple[str, ...]
    auc: float | None
    margin: float | None
    worst: tuple[str, str, float] | None
    fit: ThresholdFit | None
    jev_threshold_accuracy: float | None
    histogram: list[int]

    @property
    def usable(self) -> bool:
        """True when both labels were present, so the numbers exist."""
        return self.auc is not None


@dataclass
class BackendAnalysis:
    """Everything one backend's rows add up to."""

    backend: str
    model: str
    rows: list[Mapping[str, Any]] = field(default_factory=list)
    nouls: dict[str, NoulAnalysis] = field(default_factory=dict)


def analyse_noul(
    rows: Sequence[Mapping[str, Any]], noul: str, labels: Labels
) -> NoulAnalysis:
    """Approaches A, B and C for one noul, skipping scenarios this run lacks."""
    means = scenario_means(rows, noul)
    positives = {name: means[name] for name in labels.positive if name in means}
    negatives = {name: means[name] for name in labels.negative if name in means}
    values = [float(row.get(noul, 0.0) or 0.0) for row in rows]
    histogram = decile_histogram(values)
    if not positives or not negatives:
        return NoulAnalysis(
            noul=noul,
            positives=positives,
            negatives=negatives,
            missing_positives=tuple(n for n in labels.positive if n not in means),
            missing_negatives=tuple(n for n in labels.negative if n not in means),
            auc=None,
            margin=None,
            worst=None,
            fit=None,
            jev_threshold_accuracy=None,
            histogram=histogram,
        )
    high = list(positives.values())
    low = list(negatives.values())
    return NoulAnalysis(
        noul=noul,
        positives=positives,
        negatives=negatives,
        missing_positives=tuple(n for n in labels.positive if n not in means),
        missing_negatives=tuple(n for n in labels.negative if n not in means),
        auc=pairwise_auc(high, low),
        margin=mean_margin(high, low),
        worst=worst_pair(positives, negatives),
        fit=best_threshold(high, low),
        jev_threshold_accuracy=balanced_accuracy(high, low, JEV_CUTOFFS[noul]),
        histogram=histogram,
    )


def analyse(rows: Sequence[Mapping[str, Any]]) -> list[BackendAnalysis]:
    """One `BackendAnalysis` per backend label, in pass-rate order."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("backend", "unknown"))].append(row)
    analyses = []
    for backend, backend_rows in grouped.items():
        analysis = BackendAnalysis(
            backend=backend,
            model=str(backend_rows[0].get("model", "")),
            rows=list(backend_rows),
        )
        for noul in NOULS:
            analysis.nouls[noul] = analyse_noul(backend_rows, noul, LABELS[noul])
        analyses.append(analysis)
    analyses.sort(key=lambda item: (-pass_rate(item.rows), item.backend))
    return analyses


# --------------------------------------------------------------------------
# Loading rows.
# --------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every JSON object in one results file.

    Raises:
        AnalysisError: a line is not a JSON object.
    """
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise AnalysisError(f"{path}:{number}: {error}") from error
        if not isinstance(row, dict):
            raise AnalysisError(f"{path}:{number}: not a JSON object")
        rows.append(row)
    return rows


def rows_for_run(results_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """Every row in `results_dir` that belongs to `run_id`."""
    rows: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*.jsonl")):
        rows.extend(row for row in read_jsonl(path) if row.get("run_id") == run_id)
    return rows


def latest_matrix(results_dir: Path) -> Path:
    """The newest `matrix-*.json` report.

    Raises:
        AnalysisError: there is none.
    """
    reports = sorted(results_dir.glob("matrix-*.json"))
    if not reports:
        raise AnalysisError(f"no matrix-*.json in {results_dir}")
    return max(reports, key=lambda path: path.stat().st_mtime)


@dataclass(frozen=True)
class Source:
    """Where the rows came from and what to stamp the analysis with."""

    label: str
    stamp: str
    rows: list[dict[str, Any]]


def load_source(spec: str, results_dir: Path) -> Source:
    """Resolve a matrix JSON path, a `.jsonl` path or a bare run id to rows.

    Raises:
        AnalysisError: the spec names nothing, or the run has no rows.
    """
    path = Path(spec)
    if path.suffix == ".jsonl":
        if not path.is_file():
            raise AnalysisError(f"no results file at {path}")
        return Source(label=str(path), stamp=_now(), rows=read_jsonl(path))
    if path.suffix == ".json":
        if not path.is_file():
            raise AnalysisError(f"no matrix report at {path}")
        report = json.loads(path.read_text(encoding="utf-8"))
        run_id = str(report.get("run_id", ""))
        if not run_id:
            raise AnalysisError(f"{path} has no run_id")
        rows = rows_for_run(results_dir, run_id)
        if not rows:
            raise AnalysisError(f"no rows in {results_dir} for run {run_id}")
        return Source(label=run_id, stamp=run_id, rows=rows)
    rows = rows_for_run(results_dir, spec)
    if not rows:
        raise AnalysisError(f"no rows in {results_dir} for run {spec!r}")
    return Source(label=spec, stamp=spec, rows=rows)


def _now() -> str:
    """A UTC stamp for an analysis that is not tied to a matrix run id."""
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"


# --------------------------------------------------------------------------
# Rendering.
# --------------------------------------------------------------------------


def render_labels() -> str:
    """The hand-made label table, as Markdown."""
    lines = [
        "## Label table (hand-made from the scenario docstrings)",
        "",
        "| noul | positives (should be high) | negatives (should be low) |",
        "| --- | --- | --- |",
    ]
    for noul in NOULS:
        labels = LABELS[noul]
        lines.append(
            f"| {noul} | {', '.join(f'`{n}`' for n in labels.positive)} "
            f"| {', '.join(f'`{n}`' for n in labels.negative)} |"
        )
    return "\n".join(lines)


def _percent(value: float | None) -> str:
    """A percentage, or a dash when the number could not be computed."""
    return "n/a" if value is None else f"{value:.0%}"


def render(source: Source, analyses: Sequence[BackendAnalysis]) -> str:
    """The whole analysis as Markdown."""
    lines = [
        f"# Judgement-number analysis - {source.label}",
        "",
        f"{len(source.rows)} rows, {len(analyses)} backend(s). "
        "Approaches A, B and C are from `docs/13_jev_vs_chat_evals.md`; the "
        "positive/negative labels are hand-made and printed at the end.",
        "",
    ]
    for analysis in analyses:
        lines.extend(_render_backend(analysis))
    lines.extend(_render_summary(analyses))
    lines += ["", render_labels(), ""]
    return "\n".join(lines)


def _render_backend(analysis: BackendAnalysis) -> list[str]:
    """One backend's sections 1-6."""
    rows = analysis.rows
    top_one = top_one_accuracy(rows)
    stability = consistency(rows)
    scenarios = {scenario_name(row) for row in rows}
    lines = [
        f"## {analysis.backend}",
        "",
        f"{len(rows)} rows over {len(scenarios)} scenarios.",
        "",
        "### 1. Action accuracy",
        "",
        f"- assertion pass rate: {pass_rate(rows):.0%} ({len(rows)} rows)",
        f"- gold action top-1: {top_one.rate:.0%} "
        f"({top_one.correct}/{top_one.determined} decided from "
        f"`thresholds.expected`, {top_one.fallback} rows fell back to `passed` "
        "because their gold answer is recorded in prose or not at all)",
        "",
        "### 2. Approach A - pairwise ordering (AUC)",
        "",
        "| noul | n+ | n- | AUC | mean margin | worst-ordered pair |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for noul in NOULS:
        item = analysis.nouls[noul]
        if not item.usable:
            lines.append(
                f"| {noul} | {len(item.positives)} | {len(item.negatives)} "
                "| n/a | n/a | not enough labelled scenarios in this run |"
            )
            continue
        assert item.worst is not None and item.margin is not None
        high, low, margin = item.worst
        lines.append(
            f"| {noul} | {len(item.positives)} | {len(item.negatives)} "
            f"| {item.auc:.2f} | {item.margin:+.3f} "
            f"| `{high}` {item.positives[high]:.2f} vs `{low}` "
            f"{item.negatives[low]:.2f} ({margin:+.2f}) |"
        )

    lines += [
        "",
        "### 3. Approach B - its own best threshold",
        "",
        "| noul | best threshold | balanced accuracy | gap (min+ - max-) |",
        "| --- | --- | --- | --- |",
    ]
    for noul in NOULS:
        item = analysis.nouls[noul]
        if item.fit is None:
            lines.append(f"| {noul} | n/a | n/a | n/a |")
            continue
        overlap = " (overlap)" if item.fit.gap < 0 else ""
        lines.append(
            f"| {noul} | {item.fit.threshold:.2f} "
            f"| {item.fit.balanced_accuracy:.0%} "
            f"| {item.fit.gap:+.3f}{overlap} |"
        )

    lines += [
        "",
        "### 4. Approach C - Jev's thresholds unchanged",
        "",
        "| noul | cut-off | balanced accuracy |",
        "| --- | --- | --- |",
    ]
    for noul in NOULS:
        item = analysis.nouls[noul]
        lines.append(
            f"| {noul} | >= {JEV_CUTOFFS[noul]:.2f} "
            f"| {_percent(item.jev_threshold_accuracy)} |"
        )

    lines += [
        "",
        "### 5. Consistency",
        "",
        "- identical top action across every repeat: "
        + (
            f"{stability.stable_action_fraction:.0%} of "
            f"{stability.scenarios_compared} repeated scenarios"
            if stability.scenarios_compared
            else "n/a (this run has one repeat per scenario)"
        ),
        "- mean per-scenario stdev: "
        + ", ".join(f"{noul} {stability.noul_stdev[noul]:.3f}" for noul in NOULS),
        f"- mean confidence: {stability.mean_confidence:.3f}",
        "",
        "### 6. Scale (deciles over all rows)",
        "",
        "| noul | 0.0-0.1 | 0.1-0.2 | 0.2-0.3 | 0.3-0.4 | 0.4-0.5 | 0.5-0.6 "
        "| 0.6-0.7 | 0.7-0.8 | 0.8-0.9 | 0.9-1.0 |",
        "| --- |" + " --- |" * 10,
    ]
    for noul in NOULS:
        counts = " | ".join(str(count) for count in analysis.nouls[noul].histogram)
        lines.append(f"| {noul} | {counts} |")
    lines.append("")
    return lines


def _render_summary(analyses: Sequence[BackendAnalysis]) -> list[str]:
    """The closing summary table, one row per backend, sorted by pass rate."""
    lines = [
        "## Summary table",
        "",
        "| backend | pass rate | AUC done | AUC stuck | AUC lost | AUC danger "
        "| own-thr bal acc done | own-thr bal acc danger | action consistency "
        "| $/100 calls |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for analysis in analyses:
        aucs = " | ".join(
            (
                "n/a"
                if analysis.nouls[noul].auc is None
                else f"{analysis.nouls[noul].auc:.2f}"
            )
            for noul in NOULS
        )
        fits = []
        for noul in ("done", "danger"):
            fit = analysis.nouls[noul].fit
            fits.append("n/a" if fit is None else f"{fit.balanced_accuracy:.0%}")
        stability = consistency(analysis.rows)
        stable = (
            f"{stability.stable_action_fraction:.0%}"
            if stability.scenarios_compared
            else "n/a"
        )
        lines.append(
            f"| {analysis.backend} | {pass_rate(analysis.rows):.0%} | {aucs} "
            f"| {fits[0]} | {fits[1]} "
            f"| {stable} "
            f"| ${mean_cost(analysis.rows) * 100:.4f} |"
        )
    return lines


def payload(source: Source, analyses: Sequence[BackendAnalysis]) -> dict[str, Any]:
    """The machine-readable dump of the same numbers."""
    return {
        "source": source.label,
        "rows": len(source.rows),
        "labels": {
            noul: {
                "positive": list(LABELS[noul].positive),
                "negative": list(LABELS[noul].negative),
            }
            for noul in NOULS
        },
        "jev_cutoffs": JEV_CUTOFFS,
        "backends": [_backend_payload(analysis) for analysis in analyses],
    }


def _backend_payload(analysis: BackendAnalysis) -> dict[str, Any]:
    """One backend's numbers as plain JSON."""
    top_one = top_one_accuracy(analysis.rows)
    stability = consistency(analysis.rows)
    return {
        "backend": analysis.backend,
        "model": analysis.model,
        "rows": len(analysis.rows),
        "scenarios": sorted({scenario_name(row) for row in analysis.rows}),
        "pass_rate": pass_rate(analysis.rows),
        "top_one": {
            "rate": top_one.rate,
            "determined": top_one.determined,
            "correct": top_one.correct,
            "fallback_rows": top_one.fallback,
            "fallback_passed": top_one.fallback_passed,
        },
        "mean_cost_usd": mean_cost(analysis.rows),
        "cost_usd_per_100_calls": mean_cost(analysis.rows) * 100,
        "consistency": {
            "scenarios_compared": stability.scenarios_compared,
            "stable_action_fraction": stability.stable_action_fraction,
            "noul_stdev": stability.noul_stdev,
            "mean_confidence": stability.mean_confidence,
        },
        "nouls": {noul: _noul_payload(analysis.nouls[noul]) for noul in NOULS},
    }


def _noul_payload(item: NoulAnalysis) -> dict[str, Any]:
    """One noul's approaches A, B, C and its histogram."""
    return {
        "positives": item.positives,
        "negatives": item.negatives,
        "missing_positives": list(item.missing_positives),
        "missing_negatives": list(item.missing_negatives),
        "auc": item.auc,
        "mean_margin": item.margin,
        "worst_pair": (
            None
            if item.worst is None
            else {
                "positive": item.worst[0],
                "negative": item.worst[1],
                "margin": item.worst[2],
            }
        ),
        "best_threshold": (
            None
            if item.fit is None
            else {
                "threshold": item.fit.threshold,
                "balanced_accuracy": item.fit.balanced_accuracy,
                "gap": item.fit.gap,
            }
        ),
        "jev_threshold_balanced_accuracy": item.jev_threshold_accuracy,
        "histogram": item.histogram,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "source",
        nargs="?",
        default="",
        help="a matrix-*.json path, a results *.jsonl path, or a run id",
    )
    parser.add_argument(
        "--latest", action="store_true", help="use the newest matrix-*.json"
    )
    parser.add_argument(
        "--labels", action="store_true", help="print the label table and stop"
    )
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Analyse one matrix run and write the report. Returns an exit code."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.labels:
        print(render_labels())
        return 0
    if args.latest:
        spec = str(latest_matrix(args.results_dir))
    elif args.source:
        spec = args.source
    else:
        raise AnalysisError("give a matrix json, a run id, or --latest")
    source = load_source(spec, args.results_dir)
    analyses = analyse(source.rows)
    report = render(source, analyses)
    args.results_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = args.results_dir / f"analysis-{source.stamp}.md"
    json_path = args.results_dir / f"analysis-{source.stamp}.json"
    markdown_path.write_text(report, encoding="utf-8")
    json_path.write_text(
        json.dumps(payload(source, analyses), indent=2) + "\n", encoding="utf-8"
    )
    print(report)
    print(f"written: {markdown_path}\n         {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
