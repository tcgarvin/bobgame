"""Rendering one case study from a scenario dump and a matrix run's rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evals.case_studies import (
    CaseStudyError,
    backend_results,
    collapse,
    file_name,
    humanise,
    load_matrix_report,
    load_scenarios,
    render_case_study,
    render_index,
    write_case_studies,
)

SCENARIO: dict[str, Any] = {
    "name": "test_eats_when_starving",
    "nodeid": "evals/test_jev_judgement.py::test_eats_when_starving",
    "file": "test_jev_judgement.py",
    "docstring": "A settler on 8 food with berries in the pack should eat one.",
    "xfail": False,
    "expected": "eat:berry",
    "thresholds": {"options": 3, "expected": "eat:berry"},
    "state": {
        "brief": {
            "instruction": "gather wood",
            "success_condition": "you carry 5 wood",
            "places": {"river": "dx 3 dy -2"},
        },
        "self": {"food": "8/100"},
        "facts": ["Food falls 1 every 4 ticks.", "A wolf has 16 health."],
        "map": ".@.\n...",
        "notes": "the grove is east",
    },
    "criteria": {
        "eat:berry": "eat one berry from your pack",
        "wait": "do nothing this tick",
        "move_E": "step one tile E",
    },
    "assertions": 'assert "eat:berry" in top_two, f"top two were {top_two}"',
}

MATRIX: dict[str, Any] = {
    "run_id": "abc123",
    "latency_caveat": "unreliable network, informational only",
    "models": [
        {
            "name": "jev",
            "backend": "jev:jev-latest",
            "repeats": 2,
            "pass_rate": 0.9,
            "errors": 0,
            "mean_cost_usd": 0.00006,
            "mean_input_tokens": 1500.0,
            "scenarios": {"test_eats_when_starving": {"pass_rate": 1.0, "calls": 2}},
        },
        {
            "name": "gpt-4.1-nano+vote",
            "backend": "openrouter+vote:openai/gpt-4.1-nano",
            "repeats": 2,
            "pass_rate": 0.5,
            "errors": 1,
            "mean_cost_usd": 0.0002,
            "mean_input_tokens": 2000.0,
            "scenarios": {"test_eats_when_starving": {"pass_rate": 0.5, "calls": 1}},
        },
        {
            "name": "absent-model",
            "backend": "openrouter:vendor/absent",
            "repeats": 2,
            "pass_rate": 0.0,
            "errors": 4,
            "scenarios": {},
        },
    ],
}


def _row(
    backend: str,
    repeat: int,
    action: str,
    passed: bool,
    second: str = "wait",
) -> dict[str, Any]:
    return {
        "top": [[action, 0.7], [second, 0.2]],
        "scenario": "test_eats_when_starving",
        "backend": backend,
        "run_id": "abc123",
        "repeat": repeat,
        "action": action,
        "confidence": 0.8,
        "done": 0.1,
        "stuck": 0.2,
        "lost": 0.3,
        "danger": 0.4,
        "passed": passed,
    }


ROWS = [
    _row("jev:jev-latest", 0, "eat:berry", True),
    _row("jev:jev-latest", 1, "eat:berry", True),
    _row("openrouter+vote:openai/gpt-4.1-nano", 0, "wait", False),
]


def test_humanise_and_file_name() -> None:
    assert humanise("test_eats_when_starving") == "Eats when starving"
    assert file_name(3, "test_eats_when_starving") == "03_eats_when_starving.md"


def test_collapse_counts_repeated_items_as_given() -> None:
    assert collapse(["`a`", "`a`", "`b`"]) == "`a` x2, `b`"
    assert collapse(["`a` > `b`"]) == "`a` > `b`"
    assert collapse([]) == "-"


def test_backend_results_put_jev_first_and_keep_missing_backends() -> None:
    results = backend_results(MATRIX, ROWS, "eats_when_starving")
    assert [result.name for result in results] == [
        "jev",
        "gpt-4.1-nano+vote",
        "absent-model",
    ]
    assert results[0].actions() == ["eat:berry", "eat:berry"]
    assert results[0].errors == 0
    assert results[1].actions() == ["wait", "error"]
    assert results[1].errors == 1
    assert results[2].actions() == ["error", "error"]
    assert results[2].mean("confidence") is None


def test_top_two_shows_both_ranked_keys_and_errors() -> None:
    results = backend_results(MATRIX, ROWS, "eats_when_starving")
    assert results[0].top_two() == ["`eat:berry` > `wait`"] * 2
    assert results[1].top_two() == ["`wait` > `wait`", "`error`"]
    assert results[2].top_two() == ["`error`", "`error`"]


def test_top_one_gold_is_a_dash_without_a_gold_option_list() -> None:
    results = backend_results(MATRIX, ROWS, "eats_when_starving")
    assert results[0].top_one_gold("eat:berry") == "-"
    assert results[0].top_one_gold(None) == "-"
    assert results[0].top_one_gold([]) == "-"


def test_top_one_gold_counts_the_hits_over_every_repeat() -> None:
    results = backend_results(MATRIX, ROWS, "eats_when_starving")
    gold = ["eat:berry"]
    assert results[0].top_one_gold(gold) == "2/2"
    assert results[1].top_one_gold(gold) == "0/2"
    assert results[2].top_one_gold(gold) == "0/2"


def test_render_case_study_has_every_section_and_the_data() -> None:
    results = backend_results(MATRIX, ROWS, "eats_when_starving")
    text = render_case_study(SCENARIO, results, 12)

    assert text.startswith("# 12. Eats when starving\n")
    for heading in (
        "## The situation",
        "## Why this is a good test",
        "## What the model saw",
        "### The five questions asked of every backend",
        "## What we expect",
        "## Results",
        "## Notes",
    ):
        assert heading in text, heading
    assert text.count("<!-- WRITER:") == 4

    # The prompt, as the model saw it.
    assert "gather wood" in text
    assert "`river` (dx 3 dy -2)" in text
    assert "the grove is east" in text
    assert "```text\n.@.\n...\n```" in text
    assert "- Food falls 1 every 4 ticks." in text
    assert '"food": "8/100"' in text
    assert "| `eat:berry` | eat one berry from your pack |" in text
    assert "Is the brief's success condition met right now" in text

    # The gold answer and the assertions.
    assert "**Gold answer**: eat:berry" in text
    assert 'assert "eat:berry" in top_two' in text

    # The results tables.
    assert (
        "| **jev** | 100% | `eat:berry` > `wait` x2 | - | 0.80 | 0.10 | 0.20 "
        "| 0.30 | 0.40 | 0 |" in text
    )
    assert "| gpt-4.1-nano+vote | 50% | `wait` > `wait`, `error` | - | 0.80" in text
    assert "| absent-model | 0% | `error` x2 | - | - | - | - | - | - | 2 |" in text
    assert "| `error` | 3 |" in text
    assert "| `eat:berry` | 2 |" in text


def test_render_case_study_marks_an_xfail_and_a_list_gold_answer() -> None:
    scenario = dict(SCENARIO, xfail=True, expected=["eat:berry", "move_E"])
    results = backend_results(MATRIX, ROWS, "eats_when_starving")
    text = render_case_study(scenario, results, 1)
    assert "**expected to fail** (`xfail`)" in text
    assert "**Gold answer**: `eat:berry`, `move_E`" in text
    # The gold column is decidable here, so it counts hits per backend.
    assert "| **jev** | 100% | `eat:berry` > `wait` x2 | 2/2 |" in text
    assert "| gpt-4.1-nano+vote | 50% | `wait` > `wait`, `error` | 0/2 |" in text


def test_render_index_lists_every_case_study_and_the_summary() -> None:
    text = render_index(MATRIX, [SCENARIO], ["12_eats_when_starving.md"])
    assert "[Eats when starving](12_eats_when_starving.md)" in text
    assert "| **jev** | `jev:jev-latest` | 90% | 0 | $0.000060 | 1500 |" in text
    assert "## Caveats" in text
    assert "unreliable network" in text


def test_write_case_studies_writes_a_file_per_scenario_and_an_index(
    tmp_path: Path,
) -> None:
    written = write_case_studies(MATRIX, ROWS, [SCENARIO], tmp_path / "out")
    assert [path.name for path in written] == [
        "01_eats_when_starving.md",
        "README.md",
    ]
    assert "Eats when starving" in written[0].read_text(encoding="utf-8")


def test_load_scenarios_complains_about_an_empty_dump(tmp_path: Path) -> None:
    path = tmp_path / "scenarios.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CaseStudyError):
        load_scenarios(path)
    with pytest.raises(CaseStudyError):
        load_scenarios(tmp_path / "missing.json")


def test_load_matrix_report_rejects_something_that_is_not_one(tmp_path: Path) -> None:
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    with pytest.raises(CaseStudyError):
        load_matrix_report(path)
