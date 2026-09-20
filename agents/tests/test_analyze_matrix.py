"""Unit tests for the matrix analysis metrics.

Everything here runs on tiny synthetic rows: no API keys, no network, no
results files. The live evals under `agents/evals/` make real calls; these
tests only exercise the arithmetic that turns their rows into a report.
"""

from __future__ import annotations

from typing import Any

import pytest

from evals.analyze_matrix import (
    LABELS,
    NOULS,
    AnalysisError,
    Labels,
    analyse,
    analyse_noul,
    balanced_accuracy,
    best_threshold,
    consistency,
    decile_histogram,
    mean_margin,
    pairwise_auc,
    pass_rate,
    render,
    render_labels,
    scenario_means,
    scenario_name,
    top_one_accuracy,
    worst_pair,
)


def make_row(
    scenario: str,
    *,
    backend: str = "jev:jev-latest",
    action: str = "wait",
    passed: bool = True,
    confidence: float = 0.9,
    expected: Any = None,
    **nouls: float,
) -> dict[str, Any]:
    """One results row with the columns the analysis reads."""
    thresholds: dict[str, Any] = {"options": 12}
    if expected is not None:
        thresholds["expected"] = expected
    row: dict[str, Any] = {
        "scenario": scenario,
        "backend": backend,
        "model": backend.split(":", 1)[-1],
        "action": action,
        "confidence": confidence,
        "passed": passed,
        "cost_usd": 0.0001,
        "thresholds": thresholds,
    }
    for noul in NOULS:
        row[noul] = nouls.get(noul, 0.0)
    return row


def test_pairwise_auc_is_one_when_every_positive_outranks_every_negative() -> None:
    assert pairwise_auc([0.55, 0.6], [0.45, 0.1]) == 1.0


def test_pairwise_auc_scores_ties_as_half_a_win() -> None:
    assert pairwise_auc([0.5], [0.5]) == 0.5
    assert pairwise_auc([0.5, 0.9], [0.5]) == 0.75


def test_pairwise_auc_is_zero_when_the_order_is_inverted() -> None:
    assert pairwise_auc([0.1], [0.9, 0.8]) == 0.0


def test_pairwise_auc_needs_both_labels() -> None:
    with pytest.raises(AnalysisError):
        pairwise_auc([0.9], [])


def test_mean_margin_is_the_difference_of_the_means() -> None:
    assert mean_margin([0.8, 0.6], [0.3, 0.1]) == pytest.approx(0.5)


def test_balanced_accuracy_counts_the_cutoff_value_as_high() -> None:
    assert balanced_accuracy([0.6], [0.2], 0.6) == 1.0
    assert balanced_accuracy([0.59], [0.2], 0.6) == 0.5


def test_best_threshold_separates_cleanly_and_reports_a_positive_gap() -> None:
    fit = best_threshold([0.8, 0.7], [0.2, 0.3])
    assert fit.balanced_accuracy == 1.0
    assert 0.3 < fit.threshold <= 0.7
    assert fit.gap == pytest.approx(0.4)


def test_best_threshold_reports_a_negative_gap_when_the_classes_overlap() -> None:
    fit = best_threshold([0.6, 0.4], [0.5, 0.2])
    assert fit.gap == pytest.approx(-0.1)
    assert fit.balanced_accuracy < 1.0


def test_best_threshold_prefers_the_cutoff_nearest_a_half_on_a_tie() -> None:
    # Flat scores: every threshold in 0.05..0.95 scores the same, so the tie
    # break should land on 0.5 rather than at an arbitrary end of the sweep.
    fit = best_threshold([0.5], [0.5])
    assert fit.threshold == pytest.approx(0.5)


def test_worst_pair_is_the_smallest_margin() -> None:
    high, low, margin = worst_pair({"a": 0.9, "b": 0.55}, {"c": 0.5, "d": 0.1})
    assert (high, low) == ("b", "c")
    assert margin == pytest.approx(0.05)


def test_decile_histogram_bins_the_endpoints() -> None:
    assert decile_histogram([0.0, 0.05, 0.5, 1.0]) == [2, 0, 0, 0, 0, 1, 0, 0, 0, 1]


def test_consistency_sees_a_changed_action_and_the_noul_spread() -> None:
    rows = [
        make_row("test_a", action="wait", confidence=0.8, done=0.2),
        make_row("test_a", action="wait", confidence=0.6, done=0.4),
        make_row("test_b", action="wait", done=0.5),
        make_row("test_b", action="move_E", done=0.5),
    ]
    result = consistency(rows)
    assert result.scenarios_compared == 2
    assert result.stable_action_fraction == pytest.approx(0.5)
    # stdev of (0.2, 0.4) is ~0.1414, of (0.5, 0.5) is 0, mean ~0.0707.
    assert result.noul_stdev["done"] == pytest.approx(0.0707, abs=1e-3)
    assert result.noul_stdev["lost"] == 0.0
    assert result.mean_confidence == pytest.approx(0.8)  # 0.8, 0.6, 0.9, 0.9


def test_consistency_without_repeats_reports_nothing_compared() -> None:
    result = consistency([make_row("test_a")])
    assert result.scenarios_compared == 0
    assert result.stable_action_fraction == 0.0


def test_scenario_name_drops_the_pytest_prefix() -> None:
    assert scenario_name({"scenario": "test_done_when_inventory_met"}) == (
        "done_when_inventory_met"
    )
    assert scenario_name({"scenario": "bare"}) == "bare"


def test_scenario_means_average_over_repeats() -> None:
    rows = [make_row("test_a", done=0.2), make_row("test_a", done=0.8)]
    assert scenario_means(rows, "done") == {"a": pytest.approx(0.5)}


def test_top_one_uses_the_gold_list_and_falls_back_to_passed() -> None:
    rows = [
        make_row("test_a", action="move_E", expected=["move_E", "step_towards:t"]),
        make_row("test_b", action="wait", expected=["move_E"]),
        make_row("test_c", action="wait", expected="the gold answer in prose"),
        make_row("test_d", action="wait", passed=False),
    ]
    result = top_one_accuracy(rows)
    assert (result.determined, result.correct) == (2, 1)
    assert (result.fallback, result.fallback_passed) == (2, 1)
    assert result.rate == pytest.approx(0.5)


def test_pass_rate_counts_only_true_verdicts() -> None:
    assert pass_rate([make_row("test_a"), make_row("test_b", passed=False)]) == 0.5


def test_analyse_noul_skips_scenarios_this_run_does_not_have() -> None:
    labels = Labels(positive=("a", "missing"), negative=("b",))
    rows = [make_row("test_a", done=0.9), make_row("test_b", done=0.1)]
    item = analyse_noul(rows, "done", labels)
    assert item.usable
    assert item.auc == 1.0
    assert item.missing_positives == ("missing",)
    assert item.missing_negatives == ()


def test_analyse_noul_is_unusable_without_both_labels() -> None:
    labels = Labels(positive=("a",), negative=("nowhere",))
    item = analyse_noul([make_row("test_a", done=0.9)], "done", labels)
    assert not item.usable
    assert item.auc is None and item.fit is None
    assert item.jev_threshold_accuracy is None


def test_analyse_groups_by_backend_and_sorts_by_pass_rate() -> None:
    rows = [
        make_row("test_done_when_inventory_met", backend="jev:jev-latest", done=0.9),
        make_row(
            "test_not_done_when_inventory_short", backend="jev:jev-latest", done=0.1
        ),
        make_row(
            "test_done_when_inventory_met",
            backend="openrouter:x",
            passed=False,
            done=0.5,
        ),
        make_row(
            "test_not_done_when_inventory_short",
            backend="openrouter:x",
            passed=False,
            done=0.5,
        ),
    ]
    analyses = analyse(rows)
    assert [item.backend for item in analyses] == ["jev:jev-latest", "openrouter:x"]
    assert analyses[0].nouls["done"].auc == 1.0
    assert analyses[1].nouls["done"].auc == 0.5
    # jev's 0.9 / 0.1 clears Jev's own 0.6 cut-off both ways.
    assert analyses[0].nouls["done"].jev_threshold_accuracy == 1.0


def test_render_produces_every_section_without_crashing_on_missing_labels() -> None:
    from evals.analyze_matrix import Source

    rows = [
        make_row("test_done_when_inventory_met", done=0.9),
        make_row("test_not_done_when_inventory_short", done=0.1),
    ]
    source = Source(label="unit", stamp="unit", rows=rows)
    report = render(source, analyse(rows))
    for heading in (
        "### 1. Action accuracy",
        "### 2. Approach A",
        "### 3. Approach B",
        "### 4. Approach C",
        "### 5. Consistency",
        "### 6. Scale",
        "## Summary table",
    ):
        assert heading in report
    # `lost` has no labelled scenario here, so it must degrade, not raise.
    assert "not enough labelled scenarios" in report


def test_label_table_covers_every_noul_and_prints() -> None:
    assert set(LABELS) == set(NOULS)
    for noul, labels in LABELS.items():
        assert labels.positive, noul
        assert labels.negative, noul
        assert not set(labels.positive) & set(labels.negative), noul
    text = render_labels()
    for noul in NOULS:
        assert f"| {noul} |" in text
