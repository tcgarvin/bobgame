# Cost accounting

Every run records what its language-model calls cost, in US dollars, at the
granularity of one call. Roll-ups (per turn, per stint, per settler, per run)
are computed from those records by `tools/analyze_run.py`, never stored.

## Sources of truth

**OpenRouter (planner, converser, conversation note).** Every request asks for
usage accounting (`usage: {"include": true}` in the body; pydantic-ai's
`OpenRouterModelSettings(openrouter_usage={"include": True})`). OpenRouter then
returns `usage.cost` in dollars plus cached-token counts on every response, and
pydantic-ai copies them into `ModelResponse.provider_details` (`cost`,
`cached_tokens`, `upstream_inference_cost`, ...). This is the only correct
number: Qwen 3.7 Flash has tiered prices that step at 32k and 256k prompt
tokens and discounted cache reads, so `tokens x list price` is wrong.

**TypeSafe (Jev).** The SDK reports `usage.input_tokens` only. Cost is computed
locally at a configured price. The price is a constant in
`agents/src/agents/jev_agent/pricing.py`:

```python
JEV_USD_PER_MILLION_INPUT_TOKENS = 0.042   # $42 per billion input tokens
```

and the value in force is written into the run so later analysis of an old run
uses the price that run was billed at, not today's constant.

## Trace records

All amounts are floats in US dollars, key `cost_usd`. Records that made no
model call carry no `cost_usd` (same rule as `latency_ms`: absent, never zero).

### `agents/agent-<id>/stints.jsonl.gz`

Every Jev tick row (the ones that already carry `input_tokens` and
`latency_ms`) gains:

- `cost_usd`: `input_tokens * JEV_USD_PER_MILLION_INPUT_TOKENS / 1e6`.

### `agents/agent-<id>/planner.jsonl.gz`

`turn_end.usage` becomes:

```json
"usage": {
  "input_tokens": 449468,
  "output_tokens": 1861,
  "cached_tokens": 380000,
  "requests": 31,
  "cost_usd": 0.0412
}
```

- `requests`: number of model requests the turn made (one per tool round plus
  the final answer). Summed from the `ModelResponse`s in `result.new_messages()`.
- `cached_tokens`: sum of `provider_details["cached_tokens"]` over those
  responses (0 when absent).
- `cost_usd`: sum of `provider_details["cost"]` over those responses. If a
  response carries no `cost` (an endpoint that does not report it), the turn
  record also gets `"cost_missing": true` so the roll-up can say the run's
  planner total is a lower bound.

`tool_budget_reached` turns (ended by `UsageLimitExceeded`) still made
requests. They record the same `usage` block, computed from the captured
`run_messages`.

### `agents/agent-<id>/conversations.jsonl.gz`

Each `turn` record gains `usage` in the same shape as the planner's
(`input_tokens`, `output_tokens`, `cached_tokens`, `requests`, `cost_usd`) for
the converser call that produced the move. A cancelled or timed-out turn that
got no response has no `usage`. The record written when the closing note is
produced (`conversation_end` or wherever the note is traced today) gains the
same `usage` block for the note call.

### `agents/agent-<id>/pricing.json`

Written once when the agent starts, next to `reflex.json`:

```json
{"jev_usd_per_million_input_tokens": 0.042, "planner_model": "openrouter:qwen/qwen3.7-flash"}
```

## Roll-ups (`tools/analyze_run.py`)

The text report gains a "cost" section and the `--json` dump a `cost` object:

- run total, split `planner` / `converser` (moves + notes) / `jev`;
- per settler: the same split, plus planner cost per turn (mean, max) and
  the most expensive single turn with a deep link;
- rates: dollars per 100 world ticks and dollars per wall-clock hour
  (`meta.json` has `started_at`, `finished_at`, `last_tick`);
- `planner_cost_is_lower_bound: true` when any turn had `cost_missing`;
- planner efficiency: mean requests per turn and cached-token share, since
  the tool-call fan-out (every tool round re-sends the whole context) is where
  the money goes.

Old runs without `cost_usd` on their rows report Jev cost from `input_tokens`
at the price in `pricing.json`, or at the current constant when that file is
missing, and report planner cost as unavailable rather than zero.

Formatting: dollars are printed with four decimals below $1 and two above.
