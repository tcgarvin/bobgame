"""The OpenRouter eval backend: parsing, fallbacks and refusals.

Every call here goes through `httpx.MockTransport`, so these are fast and
offline; the live comparison itself lives in `evals/`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from evals.openrouter_client import (
    FORMAT_MODES,
    OpenRouterError,
    OpenRouterJevClient,
)

OPTIONS = {
    "wait": "do nothing",
    "eat:berry": "eat a berry",
    "step_towards:tree_1": "walk",
}
STATE: dict[str, Any] = {"self": {"food": 10}}


def reply(content: str, *, cost: float = 0.00012) -> dict[str, Any]:
    """A chat-completions body carrying `content` as the assistant message."""
    return {
        "provider": "OpenAI",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 40, "cost": cost},
    }


def answer(ranked: list[dict[str, Any]], **judgements: float) -> str:
    """The JSON reply the prompt asks for."""
    body = {"ranked": ranked, "done": 0.1, "stuck": 0.2, "lost": 0.3, "danger": 0.4}
    body.update(judgements)
    return json.dumps(body)


def client_for(
    handler: Any, *, provider: str = "", extra: dict[str, Any] | None = None
) -> OpenRouterJevClient:
    """A client whose requests are answered by `handler`."""
    return OpenRouterJevClient(
        "openai/gpt-4.1-nano",
        api_key="test-key",
        provider=provider,
        extra=extra,
        transport=httpx.MockTransport(handler),
    )


async def test_happy_path_parses_ranking_and_usage() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=reply(
                answer(
                    [
                        {"action": "eat:berry", "probability": 0.7},
                        {"action": "wait", "probability": 0.3},
                    ]
                )
            ),
        )

    client = client_for(handler, provider="groq")
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert decision.action == "eat:berry"
    assert decision.confidence == pytest.approx(0.7)
    assert decision.probabilities["wait"] == pytest.approx(0.3)
    assert (decision.done, decision.stuck) == (0.1, 0.2)
    assert (decision.lost, decision.danger) == (0.3, 0.4)
    assert decision.input_tokens == 1200
    assert decision.output_tokens == 40
    assert decision.cost_usd == pytest.approx(0.00012)
    assert decision.provider == "OpenAI"
    assert decision.latency_ms >= 0

    body = bodies[0]
    assert body["temperature"] == 0
    assert body["usage"] == {"include": True}
    assert body["reasoning"] == {"enabled": False}
    assert body["provider"] == {"order": ["groq"], "allow_fallbacks": False}
    assert body["response_format"]["type"] == "json_schema"


async def test_probabilities_are_normalised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=reply(
                answer(
                    [
                        {"action": "wait", "probability": 6},
                        {"action": "eat:berry", "probability": 2},
                    ]
                )
            ),
        )

    client = client_for(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert sum(decision.probabilities.values()) == pytest.approx(1.0)
    assert decision.probabilities["wait"] == pytest.approx(0.75)


async def test_invalid_action_falls_back_to_the_next_legal_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=reply(
                answer(
                    [
                        {"action": "fly_to_the_moon", "probability": 0.8},
                        {"action": "wait", "probability": 0.2},
                    ]
                )
            ),
        )

    client = client_for(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert decision.action == "wait"
    assert "fly_to_the_moon" not in decision.probabilities
    assert decision.confidence == pytest.approx(1.0)


async def test_strict_schema_refusal_falls_back_to_json_object() -> None:
    formats: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        formats.append(body.get("response_format", {}).get("type", "none"))
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(
                404, json={"error": {"message": "No endpoints found"}}
            )
        return httpx.Response(
            200, json=reply(answer([{"action": "wait", "probability": 1.0}]))
        )

    client = client_for(handler)
    decision = await client.decide(STATE, OPTIONS)
    assert decision.action == "wait"
    assert formats == ["json_schema", "json_object"]
    assert client.format_mode == "json_object"

    # The weaker setting sticks, so the next call does not pay for the refusal.
    await client.decide(STATE, OPTIONS)
    await client.aclose()
    assert formats == ["json_schema", "json_object", "json_object"]


async def test_reasoning_refusal_drops_the_field_and_is_recorded() -> None:
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append("reasoning" in body)
        if "reasoning" in body:
            return httpx.Response(
                400, json={"error": {"message": "reasoning is not supported"}}
            )
        return httpx.Response(
            200, json=reply(answer([{"action": "wait", "probability": 1.0}]))
        )

    client = client_for(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert decision.action == "wait"
    assert seen == [True, False]
    assert client.reasoning_disabled is False


async def test_plain_text_reply_is_mined_for_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = (
            "Sure! Here is the answer:\n```json\n"
            + answer([{"action": "eat:berry", "probability": 1.0}])
            + "\n```"
        )
        return httpx.Response(200, json=reply(content))

    client = client_for(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert decision.action == "eat:berry"


async def test_no_legal_action_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=reply(answer([{"action": "dance", "probability": 1.0}]))
        )

    client = client_for(handler)
    with pytest.raises(OpenRouterError, match="none of the ranked actions"):
        await client.decide(STATE, OPTIONS)
    await client.aclose()


async def test_every_format_refused_raises() -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        attempts.append(body.get("response_format", {}).get("type", "none"))
        return httpx.Response(400, json={"error": {"message": "nope"}})

    client = client_for(handler)
    with pytest.raises(OpenRouterError, match="OpenRouter refused the request"):
        await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert attempts == ["json_schema", "json_object", "none"]
    assert len(FORMAT_MODES) == 3


async def test_server_error_is_retried_once() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="upstream is sulking")
        return httpx.Response(
            200, json=reply(answer([{"action": "wait", "probability": 1.0}]))
        )

    client = client_for(handler)
    decision = await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert decision.action == "wait"
    assert len(calls) == 2


async def test_extra_overrides_the_default_body() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json=reply(answer([{"action": "wait", "probability": 1.0}]))
        )

    client = client_for(handler, extra={"reasoning": {"effort": "low"}})
    await client.decide(STATE, OPTIONS)
    await client.aclose()

    assert bodies[0]["reasoning"] == {"effort": "low"}


async def test_no_options_is_refused_before_any_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made without options")

    client = client_for(handler)
    with pytest.raises(ValueError, match="at least one option"):
        await client.decide(STATE, {})
    await client.aclose()
