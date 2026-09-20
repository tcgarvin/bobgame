"""Fixtures for the live Jev evals: a real client, and a results file per run.

The suite exists to catch prompt degradation and model-version drift, so every
scenario's numbers are written down whether it passed or failed - a run that
only says "9 passed" tells you nothing about the probability that slid from
0.81 to 0.62 this month.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Mapping

import pytest

from agents.jev_agent.jevclient import JevDecision

from .backends import (
    REPEAT_VARIABLE,
    RUN_ID_VARIABLE,
    BackendSpec,
    ClosableJevClient,
    build_client,
    row_fields,
    spec_from_env,
)

RESULTS_DIR = Path(__file__).resolve().parent / "results"

_RECORDER_KEY = pytest.StashKey["ResultsRecorder"]()
_SPEC_KEY = pytest.StashKey[BackendSpec]()


def eval_spec(config: pytest.Config) -> BackendSpec:
    """The backend under evaluation, parsed once per session.

    Parsed in `pytest_configure` so a malformed `JEV_EVAL_BACKEND` fails the
    session with one clear message rather than every test with the same one.
    """
    return config.stash[_SPEC_KEY]


def eval_repeat() -> int:
    """Which repeat of the suite this is (`JEV_EVAL_REPEAT`, default 0)."""
    value = os.environ.get(REPEAT_VARIABLE, "").strip()
    if not value:
        return 0
    return int(value)


def eval_run_id() -> str:
    """This session's run id: `JEV_EVAL_RUN_ID` when set, else a fresh one.

    The matrix runner sets it so every row of one matrix shares an id; a bare
    `pytest evals` run makes its own, so two runs of the same model on the
    same day are still told apart.
    """
    return os.environ.get(RUN_ID_VARIABLE, "") or uuid.uuid4().hex[:12]


@dataclass
class ResultsRecorder:
    """Collects one row per scenario and writes them as JSONL at the end.

    Rows are buffered rather than appended immediately because the pass/fail
    verdict only exists after the test's assertions have run; the report hook
    below fills it in.
    """

    model: str
    backend: str
    run_id: str
    repeat: int
    path: Path
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Set by the `jev` fixture to the live client's own columns, read at record
    # time because the client only learns what its endpoint refuses mid-call.
    row_extras: Callable[[], dict[str, Any]] = dict

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
            "backend": self.backend,
            "run_id": self.run_id,
            "repeat": self.repeat,
            "provider": decision.provider,
            "action": decision.action,
            "confidence": round(decision.confidence, 4),
            "top": [[key, round(value, 4)] for key, value in decision.top(5)],
            "done": round(decision.done, 4),
            "stuck": round(decision.stuck, 4),
            "lost": round(decision.lost, 4),
            "danger": round(decision.danger, 4),
            "latency_ms": decision.latency_ms,
            "input_tokens": decision.input_tokens,
            "output_tokens": decision.output_tokens,
            "cost_usd": decision.cost_usd,
            "thresholds": dict(thresholds),
            "passed": None,
            **self.row_extras(),
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


def pytest_configure(config: pytest.Config) -> None:
    """Resolve the backend once, so a bad `JEV_EVAL_BACKEND` fails loudly."""
    config.stash[_SPEC_KEY] = spec_from_env()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Mark this directory's tests live, and skip them all without an API key."""
    here = Path(__file__).resolve().parent
    key_variable = eval_spec(config).api_key_variable
    missing_key = not os.environ.get(key_variable)
    skip = pytest.mark.skip(
        reason=(
            f"{key_variable} is not set; the evals make real API calls "
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
    """The session's results file: `results/<UTC timestamp>-<backend>.jsonl`."""
    spec = eval_spec(request.config)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recorder = ResultsRecorder(
        model=spec.model,
        backend=spec.label,
        run_id=eval_run_id(),
        repeat=eval_repeat(),
        path=RESULTS_DIR / f"{stamp}-{spec.slug}.jsonl",
    )
    request.config.stash[_RECORDER_KEY] = recorder
    yield recorder
    recorder.write()


@pytest.fixture
async def jev(
    request: pytest.FixtureRequest, recorder: ResultsRecorder
) -> AsyncIterator[ClosableJevClient]:
    """A live client for the backend under evaluation: Jev, or OpenRouter.

    Function-scoped: a session-scoped async fixture would outlive the event loop
    pytest-asyncio gives each test.
    """
    client = build_client(eval_spec(request.config))
    recorder.row_extras = lambda: row_fields(client)
    try:
        yield client
    finally:
        await client.aclose()
