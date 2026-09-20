# Jev versus chat models: the intelligence comparison

Can a cheap traditional model make the per-tick judgements Jev makes for a
settler? This note is the experiment behind the write-up. It covers what is
compared, how the two kinds of answer are made comparable, and how to run it.
Latency is deliberately out of scope until it can be measured on a trustworthy
network; cost is recorded but is secondary here.

## The two kinds of answer

Jev answers one `Choice` and four `Noul` questions per tick and returns a
probability distribution over the legal options plus one probability per
noul (`done`, `stuck`, `lost`, `danger`). The stint acts on those numbers with
fixed thresholds (0.6 twice in a row ends a stint).

A chat model answers the same five questions in one JSON reply. Its numbers
are self-reported: a model writing `"danger": 0.8` is describing, not
measuring. So three things have to be separated:

1. **Which action** it picks (apples to apples: a key is a key).
2. **Whether its judgement numbers order the world correctly** (comparable
   without trusting the scale).
3. **Whether the numbers mean what Jev's mean on Jev's thresholds** (they
   usually will not, and that is a finding, not a defect of the test).

## Backends

`agents/evals/` runs every scenario through a `JevClient`. The backend is
chosen by `JEV_EVAL_BACKEND`:

- `jev` (default): TypeSafe, model `JEV_EVAL_MODEL` (default `jev-latest`).
- `openrouter:<model id>[@<provider slug>]`: one chat completion per decide
  with the five question texts from `jevclient.py` verbatim, the state and
  options as JSON, a strict JSON schema reply, temperature 0, reasoning off
  unless `JEV_EVAL_EXTRA` says otherwise. Cost comes from OpenRouter's
  `usage.cost`; Jev's from `input_tokens x $42 per billion`.

`evals/openrouter_client.py` has the client, `evals/matrix.toml` the
candidates, `evals/run_matrix.py` the loop and the report.

## Scenario sets

- `test_jev_basics.py`: one obvious right move (the floor).
- `test_jev_judgement.py`: the four nouls and the harder choices.
- `test_jev_situations.py`: temptation and restraint, reading the state rather
  than its surface, survival trade-offs, the social channel, spatial reading.
  Each records an `expected` entry so the report can show the gold answer next
  to what each model did.
- Replay set (planned): recorded states from a real run, with Jev's answer as
  a reference column and hand labels on a subset.

Every scenario is run several times per model. The unit of result is a pass
rate, never one tick.

## Making the judgement numbers comparable

### Approach A: pairs, not thresholds

Scenarios come in pairs where one side is the positive case and the other the
matched negative (`done_when_inventory_met` / `not_done_when_inventory_short`,
`lost_when_target_out_of_view` / `not_lost_when_target_visible`). For each
model and each noul, the question is only whether the positive scores above
the negative. This is a rank test and needs no scale, so a model that says
0.55 / 0.45 and one that says 0.95 / 0.05 both pass it. Report the pairwise
accuracy and the mean margin per noul per model.

### Approach B: each model's own threshold

For each model and noul, choose the threshold that best separates its own
positives from its negatives across all scenarios, then report the separation
at that threshold. This is what deploying that model would actually require:
retuning the stint's cut-offs. A model whose best threshold sits at 0.5 with a
wide margin is usable; one whose positives and negatives overlap is not,
regardless of what it wrote.

### Approach C: Jev's thresholds, unchanged

The existing asserts (0.6 high, 0.3 low, and so on). This is the "drop-in"
test and the one the chat models are expected to fail. Report it as such.

### Approach D: better elicitation for the chat models

Self-report is the weakest way to get a probability from a chat model. Two
alternatives, both implemented as elicitation modes on the OpenRouter client
and chosen with `JEV_EVAL_ELICIT`:

- `json` (default): the single JSON reply described above.
- `vote`: ask k times (k = 5) at temperature 1 with a yes/no answer per noul
  and a single action; the probability is the vote fraction and the action
  distribution is the vote histogram. Costs k calls, which the cost column
  shows honestly.
- `logprobs`: where the endpoint supports `logprobs`, ask each noul as a
  one-token yes/no and read the probability off the token logprobs. This is
  the closest analogue to what Jev returns.

Whether a fairer elicitation closes the gap is itself a result for the
write-up: if `vote` at 5x the cost matches Jev, the honest comparison is
"Jev at 1x versus model X at 5x".

## Consistency

Run each scenario five times at the settings above. Report per model the
fraction of scenarios where the chosen action was the same in every repeat,
and the standard deviation of each noul across repeats. Jev at temperature 0
is the reference.

## Running

```bash
cd agents
set -a; . ../.env; set +a
uv run python -m evals.run_matrix --repeats 5          # everything in matrix.toml
uv run python -m evals.run_matrix --only jev --only openai/gpt-5-nano
```

Reports land in `evals/results/matrix-<stamp>.md` and `.json`.

## What would move the project off Jev

A chat model that, at comparable cost, matches Jev's action pass rate on the
basics and situations sets, orders the judgement pairs correctly, and has a
usable own-threshold separation on `done` and `danger`. Latency would then be
the remaining question, measured on a good network.
