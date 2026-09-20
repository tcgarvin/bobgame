"""The vote and logprob elicitation modes: tallying, parsing and refusals.

Every call goes through `httpx.MockTransport`, so these are fast and offline;
the live comparison lives in `evals/`.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable

import httpx
import pytest

from evals.elicitation import (
    ElicitationError,
    LogprobJevClient,
    VoteJevClient,
)

OPTIONS = {
    "wait": "do nothing",
    "eat:berry": "eat a berry",
    "step_towards:tree_1": "walk",
}
STATE: dict[str, Any] = {"self": {"food": 10}}

Handler = Callable[[httpx.Request], httpx.Response]


def vote(content: str, *, cost: float = 0.0001) -> dict[str, Any]:
    """A chat-completions body carrying `content` as the assistant message."""
    return {
        "provider": "OpenAI",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 10, "cost": cost},
    }


def sample(action: str, **nouls: str) -> str:
    """One vote sample's JSON reply."""
    body = {
        "action": action,
        "done": "no",
        "stuck": "no",
        "lost": "no",
        "danger": "no",
    }
    body.update(nouls)
    return json.dumps(body)


def ranked(content_action: str = "wait") -> dict[str, Any]:
    """The ranked-JSON reply the logprob mode's action call still uses."""
    payload = json.dumps(
        {
            "ranked": [{"action": content_action, "probability": 1.0}],
            "done": 0.0,
            "stuck": 0.0,
            "lost": 0.0,
            "danger": 0.0,
        }
    )
    return {
        "provider": "OpenAI",
        "choices": [{"message": {"role": "assistant", "content": payload}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 30, "cost": 0.0002},
    }


def logprob_reply(
    token: str, candidates: list[tuple[str, float]], *, cost: float = 0.00001
) -> dict[str, Any]:
    """A one-token reply with `candidates` as its top logprobs."""
    return {
        "provider": "OpenAI",
        "choices": [
            {
                "message": {"role": "assistant", "content": token},
                "logprobs": {
                    "content": [
                        {
                            "token": token,
                            "top_logprobs": [
                                {"token": name, "logprob": math.log(probability)}
                                for name, probability in candidates
                            ],
                        }
                    ]
                },
            }
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 1, "cost": cost},
    }


def vote_client(handler: Handler, *, votes: int = 5) -> VoteJevClient:
    """A vote client whose requests are answered by `handler`."""
    return VoteJevClient(
        "openai/gpt-4.1-nano",
        api_key="test-key",
        votes=votes,
        transport=httpx.MockTransport(handler),
    )


def logprob_client(handler: Handler) -> LogprobJevClient:
    """A logprob client whose requests are answered by `handler`."""
    return LogprobJevClient(
        "openai/gpt-4.1-nano",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )


def noul_of(request: httpx.Request) -> str:
    """Which question a logprob request is asking, read off its system prompt."""
    system = json.loads(request.content)["messages"][0]["content"]
    if "Answer five questions" in system:
        return "action"
    if "success condition met right now" in system:
        return "done"
    if "become impossible" in system:
        return "stuck"
    if "missing from this state" in system:
        return "lost"
    if "immediate danger" in system:
        return "danger"
    return "action"


async def test_vote_histogram_and_yes_fractions() -> None:
    replies = [
        sample("wait", danger="yes"),
        sample("wait", danger="yes"),
        sample("wait", danger="no"),
        sample("eat:berry", danger="yes", done="yes"),
        sample("eat:berry", danger="no"),
    ]
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=vote(replies[len(bodies) - 1]))

    client = vote_client(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert len(bodies) == 5
    assert bodies[0]["temperature"] == 1.0
    assert "response_format" not in bodies[0]
    assert decision.action == "wait"
    assert decision.probabilities == {"wait": 0.6, "eat:berry": 0.4}
    assert decision.confidence == pytest.approx(0.6)
    assert decision.danger == pytest.approx(0.6)
    assert decision.done == pytest.approx(0.2)
    assert decision.stuck == 0.0
    assert decision.provider == "OpenAI/vote5"


async def test_vote_sums_cost_and_tokens_over_the_samples() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=vote(sample("wait"), cost=0.0001))

    client = vote_client(handler, votes=3)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert decision.cost_usd == pytest.approx(0.0003)
    assert decision.input_tokens == 3000
    assert decision.output_tokens == 30
    assert decision.provider == "OpenAI/vote3"


async def test_vote_drops_illegal_and_unparseable_samples() -> None:
    replies = [
        sample("fly_to_the_moon", danger="yes"),
        "I would rather not say.",
        sample("wait", danger="no"),
    ]
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=vote(replies[len(calls) - 1]))

    client = vote_client(handler, votes=3)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    # The illegal action is dropped from the histogram, but its noul answer
    # still counts: one yes out of the two samples that parsed.
    assert decision.probabilities == {"wait": 1.0}
    assert decision.danger == pytest.approx(0.5)


async def test_vote_with_no_legal_sample_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=vote(sample("dance")))

    client = vote_client(handler, votes=3)
    with pytest.raises(ElicitationError, match="none of the 3 votes"):
        await client.decide(STATE, OPTIONS)
    await client.aclose()


async def test_vote_refuses_zero_samples() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        VoteJevClient("openai/gpt-4.1-nano", api_key="test-key", votes=0)


async def test_vote_without_options_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made without options")

    client = vote_client(handler)
    with pytest.raises(ValueError, match="at least one option"):
        await client.decide(STATE, {})
    await client.aclose()


async def test_logprob_reads_yes_no_and_sums_cost() -> None:
    answers = {
        "done": logprob_reply(" Yes", [(" Yes", 0.6), ("no", 0.2), ("maybe", 0.2)]),
        "stuck": logprob_reply("no", [("yes", 0.25), ("no", 0.75)]),
        "lost": logprob_reply("no", [("NO", 0.9), ("yes", 0.1)]),
        "danger": logprob_reply("yes", [("yes", 0.5), ("no", 0.5)]),
    }
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        noul = noul_of(request)
        if noul == "action":
            return httpx.Response(200, json=ranked("eat:berry"))
        return httpx.Response(200, json=answers[noul])

    client = logprob_client(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert len(bodies) == 5
    noul_body = next(body for body in bodies if body.get("logprobs"))
    assert noul_body["top_logprobs"] == 5
    assert noul_body["max_tokens"] == 1
    assert noul_body["temperature"] == 0

    assert decision.action == "eat:berry"
    # 0.6 yes against 0.2 no, renormalised over the two.
    assert decision.done == pytest.approx(0.75)
    assert decision.stuck == pytest.approx(0.25)
    assert decision.lost == pytest.approx(0.1)
    assert decision.danger == pytest.approx(0.5)
    assert decision.cost_usd == pytest.approx(0.0002 + 4 * 0.00001)
    assert decision.input_tokens == 1000 + 4 * 900
    assert decision.output_tokens == 30 + 4
    assert decision.provider == "OpenAI/logprob"


async def test_logprob_falls_back_to_the_generated_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if noul_of(request) == "action":
            return httpx.Response(200, json=ranked())
        return httpx.Response(
            200, json=logprob_reply("Yes", [("Absolutely", 0.6), ("Sure", 0.4)])
        )

    client = logprob_client(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert decision.done == 1.0
    assert decision.danger == 1.0


async def test_logprob_without_logprobs_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if noul_of(request) == "action":
            return httpx.Response(200, json=ranked())
        return httpx.Response(
            200,
            json={
                "provider": "Groq",
                "choices": [{"message": {"role": "assistant", "content": "yes"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0},
            },
        )

    client = logprob_client(handler)
    with pytest.raises(ElicitationError, match="does not support logprobs"):
        await client.decide(STATE, OPTIONS)
    await client.aclose()


async def test_logprob_with_an_unreadable_token_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if noul_of(request) == "action":
            return httpx.Response(200, json=ranked())
        return httpx.Response(
            200, json=logprob_reply("perhaps", [("perhaps", 0.9), ("maybe", 0.1)])
        )

    client = logprob_client(handler)
    with pytest.raises(ElicitationError, match="openai/gpt-4.1-nano"):
        await client.decide(STATE, OPTIONS)
    await client.aclose()


async def test_logprob_without_options_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made without options")

    client = logprob_client(handler)
    with pytest.raises(ValueError, match="at least one option"):
        await client.decide(STATE, {})
    await client.aclose()
