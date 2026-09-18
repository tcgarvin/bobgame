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

## Live cost in the viewer

The viewer shows the run's spend and run rate next to the clock, and each
settler's spend in the agent panel. The data rides on the existing agent
status report, so it is recorded with the run and works in replay unchanged.

### Agent -> world: `AgentStatusReport.cost_json` (field 7)

Each agent keeps a `CostLedger` (in `pricing.py`) of everything it has spent
since its process started, and serialises it into every status report:

```json
{"planner_usd": 0.0049, "converser_usd": 0.0, "jev_usd": 0.0025,
 "total_usd": 0.0074, "planner_turns": 1, "jev_calls": 60}
```

- `planner_usd` grows by `usage["cost_usd"]` after each planner turn
  (`turn_end` and `tool_budget_reached` alike).
- `converser_usd` grows by the converser's move and note usage.
- `jev_usd` grows by `jev_cost_usd(input_tokens)` after every Jev call, in
  every place Jev is called (stints, reflex stints, conversation sessions).
  Wrap the `JevClient` once (`LedgerJevClient`) rather than editing each call
  site.
- Values are rounded to 8 decimals. Amounts are cumulative for this agent
  process; a restarted agent starts again from zero, which the viewer can
  detect as a drop and handles by adding the new series on top of the last
  value seen (see below).

The status report is deduplicated against the previous one; cost is part of
that comparison, so a Jev tick that spent money always produces a report.

### World: pass-through and recording

`status_service.py` parses `cost_json` the same way as `stint_json` (invalid
JSON is logged and dropped, the report still goes out) and puts it under
`"cost"` on the `agent_status` viewer message (`null` when absent, like
`stint`). `recording.py` and the replay loader need no change: they record and
replay the dict as is.

### Viewer

`AgentStatusMessage` gains `cost: AgentCost | null`. `WorldState` keeps, per
entity, the latest cost and a small history of `(tick_id, total_usd)` points,
and exposes:

- `getAgentCost(entityId)`: the latest cost for one settler.
- `getRunCost()`: `{planner_usd, converser_usd, jev_usd, total_usd}` summed
  over every entity's latest cost, plus `usd_per_hour_recent` computed from
  the summed total over the last 150 ticks of history (using
  `tick_duration_ms` to turn ticks into hours; `null` until at least 30
  ticks of history exist) and `usd_per_hour_average` (= total over
  `tick_id * tick_duration_ms`).
- A restart (an entity's `total_usd` falling below its previous value) adds
  the previous value to a per-entity offset so sums keep rising. Seeking
  backwards in replay clears history and offsets (`WorldState` already clears
  `agentStatuses` on a seek: do the same there).

UI, in `index.html` and `OverlayUI.ts`:

- Next to the clock readout, a `cost-readout` element:
  `$0.11 · $1.36/h · planner $0.03 · Jev $0.08` (converser is shown only when
  it is non-zero, to keep the line short). It is `-` and muted until the first
  cost arrives. The rate shown is the recent rate, falling back to the average
  when the recent one is not yet available.
- In the agent panel's stats block, one line: `spend $0.0074 · planner
  $0.0049 · Jev $0.0025 (1 turn, 60 Jev calls)`.

Dollar formatting follows the same rule as the report: four decimals under
$1, two above.
