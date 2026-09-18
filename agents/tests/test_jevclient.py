"""The System One call: the four questions, and how their answers are read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pytest
from typesafe_sdk import ChoiceAnswer, NoulAnswer
from typesafe_sdk._core.response_types import Usage

from agents.jev_agent.jevclient import JevDecision, TypeSafeJevClient


@dataclass
class FakeResponse:
    """The parts of a System One response the client reads."""

    answers: Mapping[str, Any]
    usage: Usage


class RecordingSystemOne:
    """Stands in for the TypeSafe client and records the questions asked."""

    def __init__(self, *, done: float, stuck: float, danger: float) -> None:
        self.questions: dict[str, Any] = {}
        self._answers = {
            "action": ChoiceAnswer(
                choice="extract:tree_1",
                confidence=0.9,
                probabilities={"extract:tree_1": 0.9, "wait": 0.1},
            ),
            "done": NoulAnswer(noul=done),
            "stuck": NoulAnswer(noul=stuck),
            "danger": NoulAnswer(noul=danger),
        }

    async def system_one(
        self, state: Mapping[str, Any], questions: Mapping[str, Any]
    ) -> FakeResponse:
        self.questions = dict(questions)
        return FakeResponse(answers=self._answers, usage=Usage(input_tokens=512))


def client_with(**noul: float) -> tuple[TypeSafeJevClient, RecordingSystemOne]:
    """A client whose transport is the recording fake."""
    client = TypeSafeJevClient.__new__(TypeSafeJevClient)
    fake = RecordingSystemOne(**noul)  # type: ignore[arg-type]
    client._client = fake  # type: ignore[assignment]
    return client, fake


async def test_the_four_questions_are_asked_every_tick() -> None:
    client, fake = client_with(done=0.1, stuck=0.2, danger=0.3)
    await client.decide({"self": {}}, {"wait": "do nothing"})
    assert sorted(fake.questions) == ["action", "danger", "done", "stuck"]


async def test_done_and_stuck_are_parsed_and_eject_is_the_higher_of_them() -> None:
    client, _ = client_with(done=0.82, stuck=0.11, danger=0.04)
    decision = await client.decide({"self": {}}, {"wait": "do nothing"})
    assert decision.action == "extract:tree_1"
    assert decision.done == pytest.approx(0.82)
    assert decision.stuck == pytest.approx(0.11)
    assert decision.eject == pytest.approx(0.82)
    assert decision.danger == pytest.approx(0.04)
    assert decision.input_tokens == 512


async def test_a_high_stuck_alone_drives_eject() -> None:
    client, _ = client_with(done=0.05, stuck=0.77, danger=0.0)
    decision = await client.decide({"self": {}}, {"wait": "do nothing"})
    assert decision.eject == pytest.approx(0.77)


async def test_no_options_is_a_programming_error() -> None:
    client, _ = client_with(done=0.0, stuck=0.0, danger=0.0)
    with pytest.raises(ValueError):
        await client.decide({"self": {}}, {})


def test_eject_defaults_to_zero_with_neither_answer() -> None:
    assert JevDecision(action="wait").eject == 0.0
