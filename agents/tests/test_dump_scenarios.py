"""The scenario dumper: the capturing fake, and where the assertions start."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from evals.dump_scenarios import (
    CapturingJevClient,
    CapturingRecorder,
    DumpError,
    ScenarioDump,
    assertion_source,
    capture,
    is_xfail,
    test_functions as enumerate_test_functions,
    write,
)


async def sample_scenario(jev: Any, recorder: Any, request: Any) -> None:
    """A tiny stand-in for a real eval test."""
    state = {"brief": {"instruction": "walk east"}}
    criteria = {"move_E": "step east", "wait": "do nothing"}
    decision = await jev.decide(state, criteria)
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria), "expected": "move_E"},
    )
    assert decision.action == "move_E", "the fake answers neutrally"


async def exploding_scenario(jev: Any, recorder: Any, request: Any) -> None:
    """A test that fails for a reason that is not an assertion."""
    raise ValueError("no world")


async def silent_scenario(jev: Any, recorder: Any, request: Any) -> None:
    """A test that never asks the model anything."""
    return None


@pytest.mark.xfail(reason="marker detection only", run=False)
async def marked_scenario(jev: Any, recorder: Any, request: Any) -> None:
    """An xfail-marked test."""
    return None


async def test_capturing_client_answers_neutrally() -> None:
    client = CapturingJevClient()
    decision = await client.decide({"a": 1}, {"x": "do x", "y": "do y"})
    assert decision.action == "x"
    assert decision.probabilities == {"x": 0.5, "y": 0.5}
    assert (decision.done, decision.stuck, decision.lost, decision.danger) == (
        0.5,
        0.5,
        0.5,
        0.5,
    )
    assert client.calls == [({"a": 1}, {"x": "do x", "y": "do y"})]


async def test_capturing_client_refuses_an_empty_option_list() -> None:
    with pytest.raises(DumpError):
        await CapturingJevClient().decide({}, {})


def test_capture_keeps_the_state_despite_the_failing_assertion() -> None:
    dump = capture("tests", "sample_scenario", sample_scenario)
    assert dump.state == {"brief": {"instruction": "walk east"}}
    assert dump.criteria == {"move_E": "step east", "wait": "do nothing"}
    assert dump.expected == "move_E"
    assert dump.thresholds["options"] == 2
    assert dump.docstring.startswith("A tiny stand-in")
    assert dump.xfail is False
    assert dump.assertions == (
        'assert decision.action == "move_E", "the fake answers neutrally"'
    )


def test_capture_reraises_anything_that_is_not_an_assertion() -> None:
    with pytest.raises(DumpError, match="exploding_scenario"):
        capture("tests", "exploding_scenario", exploding_scenario)


def test_capture_complains_when_the_model_was_never_asked() -> None:
    with pytest.raises(DumpError, match="never called decide"):
        capture("tests", "silent_scenario", silent_scenario)


def test_assertion_source_starts_after_the_last_await() -> None:
    source = assertion_source(sample_scenario)
    assert "await" not in source
    assert source.startswith("assert decision.action")


def test_assertion_source_needs_an_await() -> None:
    with pytest.raises(DumpError, match="never awaits"):
        assertion_source(silent_scenario)


def test_is_xfail_reads_the_marker() -> None:
    assert is_xfail(marked_scenario) is True
    assert is_xfail(sample_scenario) is False


def test_recorder_keeps_only_the_thresholds() -> None:
    recorder = CapturingRecorder()
    decision = CapturingJevClient()
    assert recorder.thresholds == {}
    recorder.record("id", "name", decision, thresholds={"expected": ["a"]})  # type: ignore[arg-type]
    assert recorder.thresholds == {"expected": ["a"]}
    assert recorder.scenario == "name"


def test_test_functions_are_returned_in_source_order() -> None:
    import evals.test_jev_basics as basics

    names = [name for name, _ in enumerate_test_functions(basics)]
    assert names[0] == "test_walks_east_to_the_tree_it_was_sent_to"
    assert len(names) == 11


def test_write_produces_one_json_object_per_scenario(tmp_path: Path) -> None:
    dump = capture("tests", "sample_scenario", sample_scenario)
    out = tmp_path / "nested" / "scenarios.json"
    write([dump], out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [entry["name"] for entry in payload] == ["sample_scenario"]
    assert payload[0]["criteria"]["move_E"] == "step east"
