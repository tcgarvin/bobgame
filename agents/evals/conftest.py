"""Fixtures for the live Jev evals: a real client, and a results file per run.

The suite exists to catch prompt degradation and model-version drift, so every
scenario's numbers are written down whether it passed or failed - a run that
only says "9 passed" tells you nothing about the probability that slid from
0.81 to 0.62 this month.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Mapping

import pytest

from agents.jev_agent.jevclient import DEFAULT_MODEL, JevDecision, TypeSafeJevClient

API_KEY_VARIABLE = "TYPESAFE_API_KEY"
MODEL_VARIABLE = "JEV_EVAL_MODEL"
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# The stint's own budget is under a second; an eval is not racing a tick, and a
# timeout here would read as a model regression when it is only a slow network.
EVAL_TIMEOUT_SECONDS = 30.0

_RECORDER_KEY = pytest.StashKey["ResultsRecorder"]()


def eval_model() -> str:
    """The model under evaluation: `JEV_EVAL_MODEL`, else the agent's default."""
    return os.environ.get(MODEL_VARIABLE, DEFAULT_MODEL)


@dataclass
class ResultsRecorder:
    """Collects one row per scenario and writes them as JSONL at the end.

    Rows are buffered rather than appended immediately because the pass/fail
    verdict only exists after the test's assertions have run; the report hook
    below fills it in.
    """

    model: str
    path: Path
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(
        self,
        node_id: str,
        scenario: str,
        decision: JevDecision,
        *,
        thresholds: Mapping[str, Any],
    ) -> None:
        """Buffer one scenario's outcome, keyed by the test that produced it."""
        self.rows[node_id] = {
            "scenario": scenario,
            "model": self.model,
            "action": decision.action,
            "confidence": round(decision.confidence, 4),
            "top": [[key, round(value, 4)] for key, value in decision.top(5)],
            "done": round(decision.done, 4),
            "stuck": round(decision.stuck, 4),
            "lost": round(decision.lost, 4),
            "danger": round(decision.danger, 4),
            "latency_ms": decision.latency_ms,
            "input_tokens": decision.input_tokens,
            "thresholds": dict(thresholds),
            "passed": None,
        }

    def set_verdict(self, node_id: str, passed: bool) -> None:
        """Mark a buffered row passed or failed. Unknown ids are not ours."""
        row = self.rows.get(node_id)
        if row is not None:
            row["passed"] = passed

    def write(self) -> None:
        """Write every buffered row, oldest first."""
        if not self.rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for row in self.rows.values():
                handle.write(json.dumps(row, sort_keys=True) + "\n")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Mark this directory's tests live, and skip them all without an API key."""
    here = Path(__file__).resolve().parent
    missing_key = not os.environ.get(API_KEY_VARIABLE)
    skip = pytest.mark.skip(
        reason=(
            f"{API_KEY_VARIABLE} is not set; the Jev evals make real API calls "
            "(run with: set -a; . ../.env; set +a)"
        )
    )
    for item in items:
        if Path(str(item.fspath)).parent != here:
            continue
        item.add_marker(pytest.mark.jev_live)
        if missing_key:
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, None, None]:
    """Attach each test's verdict to the row its body recorded."""
    outcome = yield
    report = outcome.get_result()  # type: ignore[attr-defined]
    if report.when != "call":
        return
    recorder = item.config.stash.get(_RECORDER_KEY, None)
    if recorder is not None:
        recorder.set_verdict(item.nodeid, bool(report.passed))


@pytest.fixture(scope="session")
def recorder(request: pytest.FixtureRequest) -> Iterator[ResultsRecorder]:
    """The session's results file: `results/<UTC timestamp>-<model>.jsonl`."""
    model = eval_model()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = model.replace("/", "_")
    recorder = ResultsRecorder(
        model=model, path=RESULTS_DIR / f"{stamp}-{safe_model}.jsonl"
    )
    request.config.stash[_RECORDER_KEY] = recorder
    yield recorder
    recorder.write()


@pytest.fixture
async def jev() -> AsyncIterator[TypeSafeJevClient]:
    """A real TypeSafe client for the model under evaluation.

    Function-scoped: a session-scoped async fixture would outlive the event loop
    pytest-asyncio gives each test.
    """
    client = TypeSafeJevClient(model=eval_model(), timeout=EVAL_TIMEOUT_SECONDS)
    try:
        yield client
    finally:
        await client.aclose()
