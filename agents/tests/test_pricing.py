"""Cost accounting: what a call cost and how a turn's usage block is built."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from agents.jev_agent.pricing import (
    JEV_USD_PER_MILLION_INPUT_TOKENS,
    jev_cost_usd,
    pricing_payload,
    usage_from_messages,
)
from agents.jev_agent.tracelog import AgentTrace


def response(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cost: float | None = None,
    cached_tokens: int | None = None,
) -> ModelResponse:
    """One model response with the usage and provider details OpenRouter sends."""
    details: dict[str, object] = {"downstream_provider": "alibaba"}
    if cost is not None:
        details["cost"] = cost
    if cached_tokens is not None:
        details["cached_tokens"] = cached_tokens
    return ModelResponse(
        parts=[TextPart(content="ok")],
        usage=RequestUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
        ),
        provider_details=details,
    )


def request(text: str = "go") -> ModelMessage:
    """A user message, which carries no usage of its own."""
    return ModelRequest(parts=[UserPromptPart(content=text)])


# -- jev cost ----------------------------------------------------------------


def test_jev_cost_is_input_tokens_at_the_configured_price() -> None:
    assert jev_cost_usd(1_000_000) == JEV_USD_PER_MILLION_INPUT_TOKENS
    assert jev_cost_usd(420) == round(420 * JEV_USD_PER_MILLION_INPUT_TOKENS / 1e6, 8)


def test_a_jev_call_with_no_tokens_costs_nothing() -> None:
    assert jev_cost_usd(0) == 0.0


# -- usage blocks ------------------------------------------------------------


def test_usage_sums_tokens_requests_and_cost_over_the_responses() -> None:
    messages = [
        request(),
        response(input_tokens=1000, output_tokens=20, cache_read_tokens=800, cost=0.01),
        request("and again"),
        response(
            input_tokens=2000, output_tokens=30, cache_read_tokens=1900, cost=0.02
        ),
    ]

    usage = usage_from_messages(messages)

    assert usage == {
        "input_tokens": 3000,
        "output_tokens": 50,
        "cached_tokens": 2700,
        "requests": 2,
        "cost_usd": 0.03,
    }


def test_a_provider_cached_token_count_wins_over_the_usage_figure() -> None:
    messages = [
        response(
            input_tokens=100,
            output_tokens=1,
            cache_read_tokens=10,
            cached_tokens=40,
            cost=0.001,
        )
    ]

    assert usage_from_messages(messages)["cached_tokens"] == 40


def test_a_response_without_a_cost_marks_the_turn_as_missing_one() -> None:
    messages = [
        response(input_tokens=100, output_tokens=5, cost=0.004),
        response(input_tokens=200, output_tokens=5),
    ]

    usage = usage_from_messages(messages)

    assert usage["cost_usd"] == 0.004
    assert usage["cost_missing"] is True
    assert usage["requests"] == 2


def test_no_messages_means_an_empty_turn_with_no_missing_cost() -> None:
    usage = usage_from_messages([])

    assert usage == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
    }


def test_requests_without_responses_count_as_no_requests() -> None:
    assert usage_from_messages([request(), request("again")])["requests"] == 0


# -- pricing.json ------------------------------------------------------------


def test_the_pricing_payload_records_the_price_and_the_planner_model() -> None:
    assert pricing_payload("openrouter:qwen/qwen3.7-flash") == {
        "jev_usd_per_million_input_tokens": JEV_USD_PER_MILLION_INPUT_TOKENS,
        "planner_model": "openrouter:qwen/qwen3.7-flash",
    }


def test_the_pricing_path_sits_beside_the_reflex_file(tmp_path: Path) -> None:
    trace = AgentTrace("ada", tmp_path)
    trace.close()

    assert trace.pricing_path == trace.reflex_path.parent / "pricing.json"
    trace.pricing_path.write_text(json.dumps(pricing_payload("test")))
    assert json.loads(trace.pricing_path.read_text())["planner_model"] == "test"
