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

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic_ai.messages import ModelMessage, ModelResponse

from .jevclient import JevClient, JevDecision

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


@dataclass
class CostLedger:
    """What one agent process has spent since it started, in US dollars.

    Mutable on purpose: there is one ledger per agent, every model call adds to
    it, and `as_json()` is serialised into every status report so the viewer can
    show live spend (docs/11_cost_accounting.md, "Live cost in the viewer").
    Amounts are cumulative for this process, so a restarted agent starts at zero.
    """

    planner_usd: float = 0.0
    converser_usd: float = 0.0
    journal_usd: float = 0.0
    jev_usd: float = 0.0
    planner_turns: int = 0
    journal_rewrites: int = 0
    jev_calls: int = 0

    def add_planner(self, usage: Mapping[str, Any]) -> None:
        """Record one planner turn from its `usage` block."""
        self.planner_usd += _usage_cost(usage)
        self.planner_turns += 1

    def add_converser(self, usage: Mapping[str, Any]) -> None:
        """Record one converser call (a move or a closing note)."""
        self.converser_usd += _usage_cost(usage)

    def add_journal(self, usage: Mapping[str, Any]) -> None:
        """Record one journal rewrite (docs/12_sleep_journal.md)."""
        self.journal_usd += _usage_cost(usage)
        self.journal_rewrites += 1

    def add_jev(self, input_tokens: int) -> None:
        """Record one Jev call from its input token count."""
        self.jev_usd += jev_cost_usd(input_tokens)
        self.jev_calls += 1

    def total_usd(self) -> float:
        """Everything spent so far."""
        return self.planner_usd + self.converser_usd + self.journal_usd + self.jev_usd

    def as_json(self) -> str:
        """The ledger as the JSON the status report carries."""
        return json.dumps(
            {
                "planner_usd": round(self.planner_usd, COST_DECIMALS),
                "converser_usd": round(self.converser_usd, COST_DECIMALS),
                "journal_usd": round(self.journal_usd, COST_DECIMALS),
                "jev_usd": round(self.jev_usd, COST_DECIMALS),
                "total_usd": round(self.total_usd(), COST_DECIMALS),
                "planner_turns": self.planner_turns,
                "journal_rewrites": self.journal_rewrites,
                "jev_calls": self.jev_calls,
            }
        )


def _usage_cost(usage: Mapping[str, Any]) -> float:
    """The dollar cost in a `usage` block, 0.0 when it carries none."""
    cost = usage.get("cost_usd", 0.0)
    if isinstance(cost, (int, float)):
        return float(cost)
    return 0.0


class LedgerJevClient:
    """A `JevClient` that adds every call it forwards to a `CostLedger`.

    Wrapping the client once is how every Jev call - ordinary stints, reflex
    stints and conversation sessions - is counted without touching each call
    site. The wrapped client's owner keeps it and closes it; this wrapper owns
    nothing, so it has nothing to close.
    """

    def __init__(self, client: JevClient, ledger: CostLedger) -> None:
        self._client = client
        self._ledger = ledger

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Delegate the call, then bill it."""
        decision = await self._client.decide(state, options)
        self._ledger.add_jev(decision.input_tokens)
        return decision
