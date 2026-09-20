"""Capture, without any network call, exactly what every eval scenario asks.

The live suite is the only place the hand-built states exist: each test builds
a world, calls `run_scenario`, and hands the result to a model. To write the
case studies (`case_studies.py`) we need the same `(state, criteria)` pair,
plus the docstring, the gold answer and the assertions, as data.

So this module runs every `test_*` coroutine in the three eval modules against
a capturing fake client. The assertions after the model call fail (the fake
answers neutrally, and that is fine) and are caught; everything up to and
including `decide` has already run by then, so the state is real.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib
import inspect
import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Coroutine, Mapping, Sequence

from agents.jev_agent.jevclient import JevDecision

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_OUT = RESULTS_DIR / "scenarios.json"

# Source order is the order the case studies are numbered in: the floor first,
# then the noul judgements, then the hard situations.
TEST_MODULES: tuple[str, ...] = (
    "evals.test_jev_basics",
    "evals.test_jev_judgement",
    "evals.test_jev_situations",
)

NEUTRAL_NOUL = 0.5


class DumpError(RuntimeError):
    """A scenario that could not be captured."""


@dataclass
class CapturingJevClient:
    """A `JevClient` that records its arguments and answers neutrally.

    Neutral means: the first option, a uniform distribution over the options,
    and 0.5 on every noul. Nothing downstream should read those numbers - they
    exist only so the test body can carry on to its assertions.
    """

    calls: list[tuple[dict[str, Any], dict[str, str]]] = field(default_factory=list)

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Record one request and answer it without asking anybody."""
        if not options:
            raise DumpError("a scenario offered no options")
        self.calls.append((dict(state), dict(options)))
        share = 1.0 / len(options)
        return JevDecision(
            action=next(iter(options)),
            probabilities={key: share for key in options},
            confidence=share,
            done=NEUTRAL_NOUL,
            stuck=NEUTRAL_NOUL,
            lost=NEUTRAL_NOUL,
            danger=NEUTRAL_NOUL,
            provider="capture",
        )

    async def aclose(self) -> None:
        """Nothing to close; the suite's fixture calls this."""


@dataclass
class CapturingRecorder:
    """Stands in for `ResultsRecorder`, keeping only the thresholds recorded."""

    thresholds: dict[str, Any] = field(default_factory=dict)
    scenario: str = ""

    def record(
        self,
        node_id: str,
        scenario: str,
        decision: JevDecision,
        *,
        thresholds: Mapping[str, Any],
    ) -> None:
        """Keep the gold answer and the option count the test recorded."""
        self.scenario = scenario
        self.thresholds = dict(thresholds)


@dataclass(frozen=True)
class StubNode:
    """The two `request.node` attributes the test bodies read."""

    nodeid: str
    name: str


@dataclass(frozen=True)
class StubRequest:
    """A stand-in for pytest's `FixtureRequest`."""

    node: StubNode


TestFunction = Callable[..., Coroutine[Any, Any, None]]


def is_xfail(function: TestFunction) -> bool:
    """Whether the test carries an `xfail` marker."""
    marks = getattr(function, "pytestmark", [])
    return any(getattr(mark, "name", "") == "xfail" for mark in marks)


def test_functions(module: ModuleType) -> list[tuple[str, TestFunction]]:
    """Every `test_*` coroutine in `module`, in source order."""
    found = [
        (name, value)
        for name, value in vars(module).items()
        if name.startswith("test_") and inspect.iscoroutinefunction(value)
    ]
    found.sort(key=lambda item: item[1].__code__.co_firstlineno)
    return found


def _records_the_row(statement: ast.stmt) -> bool:
    """Whether this statement is the `recorder.record(...)` bookkeeping call."""
    for node in ast.walk(statement):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "record":
                return True
    return False


def assertion_source(function: TestFunction) -> str:
    """The lines of the test body after the model call.

    The model call is the last statement in the body that awaits anything;
    a `recorder.record(...)` call after it is bookkeeping, not an
    expectation, so it is skipped too. What is left is what the scenario
    asserts.

    Raises:
        DumpError: the function never awaits anything.
    """
    source = textwrap.dedent(inspect.getsource(function))
    lines = source.splitlines()
    tree = ast.parse(source)
    definition = tree.body[0]
    if not isinstance(definition, (ast.AsyncFunctionDef, ast.FunctionDef)):
        raise DumpError(f"{function.__name__} is not a function definition")
    awaited = False
    body_end = 0
    for statement in definition.body:
        is_await = any(isinstance(node, ast.Await) for node in ast.walk(statement))
        awaited = awaited or is_await
        if is_await or (awaited and _records_the_row(statement)):
            body_end = statement.end_lineno or 0
    if not awaited:
        raise DumpError(f"{function.__name__} never awaits a decision")
    return textwrap.dedent("\n".join(lines[body_end:])).strip()


@dataclass(frozen=True)
class ScenarioDump:
    """One scenario, as the case-study writer needs it."""

    name: str
    nodeid: str
    file: str
    docstring: str
    xfail: bool
    expected: Any
    thresholds: dict[str, Any]
    state: dict[str, Any]
    criteria: dict[str, str]
    assertions: str

    def as_entry(self) -> dict[str, Any]:
        """The JSON object written to `scenarios.json`."""
        return {
            "name": self.name,
            "nodeid": self.nodeid,
            "file": self.file,
            "docstring": self.docstring,
            "xfail": self.xfail,
            "expected": self.expected,
            "thresholds": self.thresholds,
            "state": self.state,
            "criteria": self.criteria,
            "assertions": self.assertions,
        }


def capture(module_name: str, name: str, function: TestFunction) -> ScenarioDump:
    """Run one test against the capturing fake and keep what it sent.

    Raises:
        DumpError: the test never called `decide`, or failed for a reason
            other than its own assertions.
    """
    file_name = Path(inspect.getsourcefile(function) or "").name
    nodeid = f"evals/{file_name}::{name}"
    client = CapturingJevClient()
    recorder = CapturingRecorder()
    request = StubRequest(node=StubNode(nodeid=nodeid, name=name))
    try:
        asyncio.run(function(jev=client, recorder=recorder, request=request))
    except AssertionError:
        # Expected: the neutral answer rarely satisfies the test.
        pass
    except Exception as error:  # noqa: BLE001 - re-raised with the scenario name
        raise DumpError(f"{module_name}::{name} raised {error!r}") from error
    if not client.calls:
        raise DumpError(f"{module_name}::{name} never called decide")
    state, criteria = client.calls[0]
    return ScenarioDump(
        name=name,
        nodeid=nodeid,
        file=file_name,
        docstring=inspect.getdoc(function) or "",
        xfail=is_xfail(function),
        expected=recorder.thresholds.get("expected", None),
        thresholds=recorder.thresholds,
        state=state,
        criteria=criteria,
        assertions=assertion_source(function),
    )


def dump_all(module_names: Sequence[str] = TEST_MODULES) -> list[ScenarioDump]:
    """Capture every scenario in `module_names`, in source order."""
    dumps: list[ScenarioDump] = []
    for module_name in module_names:
        module = importlib.import_module(module_name)
        for name, function in test_functions(module):
            dumps.append(capture(module_name, name, function))
    return dumps


def write(dumps: Sequence[ScenarioDump], path: Path) -> None:
    """Write the dumps as one JSON list."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [dump.as_entry() for dump in dumps]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """The command line."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Dump every scenario and report where it went."""
    import sys

    args = parse_args(sys.argv[1:] if argv is None else argv)
    dumps = dump_all()
    write(dumps, args.out)
    print(f"{len(dumps)} scenarios -> {args.out}")
    for dump in dumps:
        marker = " (xfail)" if dump.xfail else ""
        print(f"  {dump.name}{marker}: {len(dump.criteria)} options")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
