"""What one run's language-model calls cost, in US dollars.

Contract: [docs/11_cost_accounting.md](../../../../docs/11_cost_accounting.md).

Two sources, two rules. OpenRouter reports the real dollar cost of every
response (tiered prompt prices and discounted cache reads make
`tokens x list price` wrong), so the planner and the converser read
`ModelResponse.provider_details["cost"]`. TypeSafe reports input tokens only,
so Jev's cost is computed here at a constant price, and that constant is
written into every run so a later analysis of an old run uses the price the
run was billed at.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic_ai.messages import ModelMessage, ModelResponse

# $42 per billion input tokens, the price Jev is billed at.
JEV_USD_PER_MILLION_INPUT_TOKENS = 0.042

# Dollar amounts are rounded before they are written, so a trace line does not
# carry sixteen digits of float noise. Eight decimals is a hundredth of a cent
# per millionth of a dollar: far finer than any single call costs.
COST_DECIMALS = 8


def jev_cost_usd(input_tokens: int) -> float:
    """What one Jev call costs, from its input token count."""
    return round(input_tokens * JEV_USD_PER_MILLION_INPUT_TOKENS / 1e6, COST_DECIMALS)


def usage_from_messages(messages: Sequence[ModelMessage]) -> dict[str, Any]:
    """The `usage` block for the model responses in `messages`.

    `messages` is what one run added (`result.new_messages()`, or the captured
    run messages past the history that went in); the `ModelResponse`s in it are
    the requests the run made. A response whose provider did not report a cost
    sets `cost_missing`, so a roll-up can say its total is a lower bound.
    """
    usage: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "requests": 0,
        "cost_usd": 0.0,
    }
    cost_total = 0.0
    cost_missing = False
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        usage["requests"] += 1
        usage["input_tokens"] += message.usage.input_tokens
        usage["output_tokens"] += message.usage.output_tokens
        details: Mapping[str, Any] = message.provider_details or {}
        usage["cached_tokens"] += _cached_tokens(message, details)
        cost = details.get("cost")
        if isinstance(cost, (int, float)):
            cost_total += float(cost)
        else:
            cost_missing = True
    usage["cost_usd"] = round(cost_total, COST_DECIMALS)
    if cost_missing:
        usage["cost_missing"] = True
    return usage


def _cached_tokens(response: ModelResponse, details: Mapping[str, Any]) -> int:
    """Cached prompt tokens: the provider's own count, else the usage figure.

    pydantic-ai's OpenRouter model puts `cost` in `provider_details` but leaves
    the cache read count in `RequestUsage.cache_read_tokens`; other providers
    may report `cached_tokens` directly, so that wins when it is there.
    """
    cached = details.get("cached_tokens")
    if isinstance(cached, int):
        return cached
    return response.usage.cache_read_tokens


def pricing_payload(planner_model: str) -> dict[str, Any]:
    """The JSON written to `pricing.json` when an agent starts."""
    return {
        "jev_usd_per_million_input_tokens": JEV_USD_PER_MILLION_INPUT_TOKENS,
        "planner_model": planner_model,
    }
