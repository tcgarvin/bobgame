# Live Jev evals

A functional suite that calls the **real** TypeSafe Jev API with hand-built
states and checks that the answers still mean what they meant last month. It
exists to catch two things the unit tests cannot see:

- **prompt degradation** — a change to `jevstate.py`, `options.py` or the
  question wording in `jevclient.py` that quietly makes Jev worse;
- **model-version drift** — `jev-latest` moving under us.

These are not unit tests. They cost money (a fraction of a cent per call),
need the network, and are allowed to be flaky at the margins. They live outside
`testpaths`, so `uv run pytest -q` never collects them.

## What is in it

- `test_jev_basics.py`: one obvious right move per scenario (a tree to the
  east and "go to the tree"; ingredients in the pack and "craft an axe";
  a pile underfoot and "pick up the axe"). The floor: a failure here means
  the model has stopped reading the state, not that it drifted.
- `test_jev_judgement.py`: the judgement questions (`done`, `lost`, `danger`,
  `stuck`) and the harder choices, with thresholds recorded alongside.

## Running

```bash
cd agents
set -a; . ../.env; set +a          # TYPESAFE_API_KEY
uv run pytest evals -q -m jev_live
```

Without `TYPESAFE_API_KEY` every test in this directory is skipped with a
reason, rather than failing.

Point it at another Jev model with `JEV_EVAL_MODEL` (it defaults to
`jevclient.DEFAULT_MODEL`):

```bash
JEV_EVAL_MODEL=jev-2026-08 uv run pytest evals -q -m jev_live
```

## Backends: Jev, or an ordinary chat model

The same scenarios can be asked of a chat model on OpenRouter, which is how the
"is Jev actually better?" comparison is made. One environment variable picks
the backend (`evals/backends.py`):

| Variable | Meaning |
| --- | --- |
| `JEV_EVAL_BACKEND` | unset or `jev` -> TypeSafe; `openrouter[+vote\|+logprob]:<model id>[@<provider slug>]` -> OpenRouter, with an optional elicitation mode |
| `JEV_EVAL_MODEL` | the Jev model, for the `jev` backend only |
| `JEV_EVAL_EXTRA` | a JSON object merged into the OpenRouter request body, e.g. `{"reasoning": {"effort": "minimal"}}` |
| `JEV_EVAL_REPEAT` | which repeat of the suite this is; goes in every row (default 0) |
| `JEV_EVAL_RUN_ID` | ties one matrix's rows together; a fresh id per session when unset |

```bash
set -a; . ../.env; set +a          # TYPESAFE_API_KEY, OPENROUTER_API_KEY
JEV_EVAL_BACKEND=openrouter:openai/gpt-4.1-nano uv run pytest evals -q
JEV_EVAL_BACKEND=openrouter:openai/gpt-oss-20b@groq \
  JEV_EVAL_EXTRA='{"reasoning":{"effort":"low"}}' uv run pytest evals -q
```

A provider slug pins routing (`allow_fallbacks: false`), so a run measures the
endpoint it names. The suite is skipped with a clear reason when the key the
chosen backend needs is missing.

`OpenRouterJevClient` (`evals/openrouter_client.py`) makes one HTTP call per
`decide`: a system prompt carrying the five question strings **verbatim from
`jevclient.py`**, the state as JSON, the options as JSON, temperature 0, and a
request for `{"ranked": [{"action", "probability"} x5], "done", "stuck",
"lost", "danger"}`. It asks for a strict JSON schema, falls back to
`{"type": "json_object"}` and then to plain text with JSON extraction when an
endpoint refuses (Groq's Llama endpoints answer "No endpoints found" to a
strict schema behind a provider pin), sends `reasoning: {"enabled": false}`
unless `JEV_EVAL_EXTRA` overrides it, drops that field and records
`reasoning_disabled: false` if the endpoint rejects it, and asks for
`usage: {"include": true}` so the reply carries its dollar cost. An action that
is not one of the option keys is dropped in favour of the next ranked one that
is; if nothing legal comes back the call raises rather than quietly choosing
something.

## Elicitation: three ways to get a probability out of a chat model

The numbers above are self-reports, which is the weakest part of the
comparison, so the backend string carries an optional **elicitation mode**
(`evals/elicitation.py`, docs/13 "Approach D"). It says how the probability is
got, not which model is asked:

| Backend | Calls per decide | Where the judgement numbers come from |
| --- | --- | --- |
| `openrouter:<model>` | 1 | the model writes its own probabilities |
| `openrouter+vote:<model>` | 5 | five samples at temperature 1, each one action and four yes/no answers; the probability is the vote fraction and the action distribution the vote histogram |
| `openrouter+logprob:<model>` | 5 | the action from the usual ranked JSON, each noul asked as a single `yes`/`no` token with `logprobs: true, top_logprobs: 5, max_tokens: 1` and read as P(yes)/(P(yes)+P(no)) |

```bash
JEV_EVAL_BACKEND=openrouter+vote:openai/gpt-4.1-nano uv run pytest evals -q
JEV_EVAL_BACKEND=openrouter+logprob:openai/gpt-4.1-nano uv run pytest evals -q
```

Both modes reuse `OpenRouterJevClient` for the request itself, so provider
pinning, the reasoning setting, `usage: {"include": true}` and the retries are
unchanged; cost and tokens are the **sum** over the calls a decide makes and
latency is the wall clock of the whole gather, so the cost column stays honest
about a 5x answer. The samples ride in the provider column
(`OpenAI/vote5`, `OpenAI/logprob`) because `JevDecision` is frozen and shared
with the live agent. A vote sample whose action is not a legal option, or whose
reply is not usable JSON, is dropped from the histogram (its yes/no answers
still count); a decide where every sample is illegal raises.

**Not every endpoint returns logprobs.** As of 2026-09-18, `openai/gpt-4.1-nano`
and `qwen/qwen3.7-flash` do; `google/gemini-2.5-flash-lite` and
`openai/gpt-oss-20b@groq` answer `logprobs: true` with no token logprobs, and
OpenAI refuses the field outright on reasoning models such as
`openai/gpt-5-nano` ("logprobs are not supported with reasoning models"). The
client raises `ElicitationError` naming the model in every one of those cases
rather than returning a 0.0 that would read as a confident "no".

In `matrix.toml` a candidate picks the mode with `elicitation = "vote"` or
`elicitation = "logprob"`; the six variant rows are `enabled = false`, because
they cost five calls each. Run a slice with
`--only gpt-4.1-nano --only gpt-4.1-nano-vote`.

**The caveat that belongs in any write-up**: Jev returns a real probability
distribution over the enumerated options. A chat model is asked to *write down*
what it thinks its probabilities are, so its `confidence`, `done`, `stuck`,
`lost` and `danger` are self-reports. Accuracy (did it pick the right action?)
compares cleanly; calibration does not.

## The matrix runner

`evals/run_matrix.py` runs the whole suite once per (model, repeat) and turns
the results files into one report:

```bash
cd agents
set -a; . ../.env; set +a
uv run python -m evals.run_matrix --repeats 3
uv run python -m evals.run_matrix --only jev --repeats 1 --pytest-args "-k walks_east"
```

- The candidates are `evals/matrix.toml`: a `name`, a `backend` (`jev` or
  `openrouter`), a `model` id, an optional `provider` slug, an optional `extra`
  table and an `enabled` flag. `jev` is the first row on purpose - it is the
  baseline the disagreements are measured against.
- `--only <name>` and `--skip <name>` (both repeatable) filter it;
  `--pytest-args` is passed through to every pytest run.
- Every pytest run is allowed to fail: a wrong answer is the measurement. A
  scenario that produced no row at all (an exception, an unparseable reply) is
  counted as a failure *and* tallied as an **error**.
- The report is written to `evals/results/matrix-<stamp>.md` and `.json` and
  printed: per-model pass rate, errors, mean cost per call, cost per 100 calls,
  mean latency (**unreliable network, informational only**), mean input and
  output tokens, a scenario x model pass-rate table, and a "disagreements"
  section listing every scenario where a model's pass rate differs from Jev's.

The whole matrix costs real money on eleven models. Run a slice first.

## Results

Every run appends one JSON line per scenario to
`evals/results/<UTC timestamp>-<model>.jsonl`, **whether the scenario passed or
failed** — the numbers are the point, not the green tick. Each line carries the
scenario name, the model, the chosen action and its confidence, the top five
probabilities, `done`/`stuck`/`lost`/`danger`, latency, input tokens, and the
pass/fail verdict.

The directory is gitignored (except `.gitkeep`): results are local evidence,
not repository history.

Compare two runs:

```bash
cd agents/evals/results
python - <<'EOF'
import json, sys
def rows(path):
    return {json.loads(l)["scenario"]: json.loads(l) for l in open(path)}
a, b = rows(sys.argv[1]), rows(sys.argv[2])
for name in sorted(a.keys() | b.keys()):
    x, y = a.get(name), b.get(name)
    if x is None or y is None:
        print(f"{name}: only in one run"); continue
    print(f"{name}: action {x['action']} -> {y['action']}  "
          + "  ".join(f"{k} {x[k]:.2f}->{y[k]:.2f}"
                      for k in ("done", "stuck", "lost", "danger")))
EOF
```

(or `jq -c '{scenario, action, done, stuck, lost, danger}' <file>` and `diff`.)

## Analysing a matrix run

The matrix report answers "which action did it pick". `analyze_matrix.py`
answers the other three questions in
[docs/13_jev_vs_chat_evals.md](../../docs/13_jev_vs_chat_evals.md) — whether the
judgement numbers *order* the world correctly (approach A), what threshold each
model would need (B), and what its numbers do on Jev's own cut-offs (C) — plus
consistency across repeats and a decile histogram that shows whether a model
uses the 0..1 range or clusters on 0.1 / 0.5 / 0.9.

```bash
cd agents
uv run python -m evals.analyze_matrix --latest                       # newest matrix-*.json
uv run python -m evals.analyze_matrix evals/results/matrix-<stamp>.json
uv run python -m evals.analyze_matrix <run_id>                       # rows by run id
uv run python -m evals.analyze_matrix evals/results/<stamp>-jev_jev-latest.jsonl
uv run python -m evals.analyze_matrix --labels                       # just the label table
```

It prints the Markdown and writes `results/analysis-<stamp>.md` and `.json`.

Approaches A, B and C need to know which scenarios *should* score high on each
noul and which should score low. That label table is hand-made from the scenario
docstrings and assertions and lives in one place, `LABELS` at the top of
`analyze_matrix.py` — edit it there when a scenario is added or its meaning
changes, never in the metrics. Scenarios a given run did not produce are simply
left out, and each table shows the `n+`/`n-` it actually had. The metrics
themselves are pure functions, unit-tested offline in
`tests/test_analyze_matrix.py`.

Top-1 correctness is only decidable where the row recorded a gold *option list*
(`thresholds.expected` in `test_jev_basics.py`); `test_jev_situations.py` records
its gold answer in prose and `test_jev_judgement.py` records none, so those rows
fall back to the test's own `passed` verdict and the report says how many did.

## Case studies: one markdown page per scenario

The matrix and the analysis are aggregates. To argue about a *single* scenario -
what the settler faced, what it was asked, what each model chose - there is a
folder of case studies, one page per scenario, generated in two steps.

```bash
cd agents
uv run python -m evals.dump_scenarios                       # -> evals/results/scenarios.json
uv run python -m evals.case_studies --latest --out ../docs/case_studies
```

`dump_scenarios.py` makes **no network call**. It imports the three test
modules, runs every `test_*` coroutine against a capturing fake - a `jev` that
records `(state, options)` and answers neutrally (first option, uniform
probabilities, 0.5 on every noul), a recorder that keeps the `thresholds`, and a
stub `request` - and catches the `AssertionError` the neutral answer provokes.
Everything up to and including `decide` has really run by then, so the captured
state is exactly what the live suite sends. Anything that is *not* an
`AssertionError` is re-raised naming the scenario, and a test that never asked
the model is an error rather than an empty row. The assertions are read back
out of the source: everything after the last statement that awaits (and after
the `recorder.record` call that follows it). Each entry carries `name`,
`nodeid`, `file`, `docstring`, `xfail`, `expected`, `thresholds`, `state`,
`criteria` and `assertions`.

`case_studies.py` joins that dump to a matrix run (`--matrix <path>` or
`--latest`, plus the run's rows from `results/`) and writes
`<NN>_<scenario>.md` per scenario - numbered in source order, basics then
judgement then situations - and a `README.md` index with the overall summary
table and the caveats. Each page has, in order: the summary line from the
docstring, **The situation** and **Why this is a good test** (writer
placeholders), **What the model saw** (the brief, the ASCII map in its own
fenced block, the facts as bullets, the rest of the state as JSON, the options
as a table, and the five questions verbatim from `jevclient.py`), **What we
expect** (the gold answer, the assertion source, and a writer placeholder for
the ambiguities), **Results** (one row per backend with Jev first and bolded:
pass rate, the top two ranked keys per repeat as `first > second` collapsed to
`first > second xN`, a `top-1 = gold` count where the gold answer is a list of
option keys (`-` where it is prose or absent), mean confidence and mean
`done`/`stuck`/`lost`/`danger`, errors; then a tally of every distinct top-1
action chosen) and **Notes** (a writer placeholder for which model did best and
the common failure modes).

A backend that produced no row for a scenario shows as `error` with its count,
and a model missing from the rows entirely still gets a row. The elicitation
variants appear as their own rows, labelled with `+vote` / `+logprob`. The
prose sections are HTML comments (`<!-- WRITER: ... -->`) so an unwritten page
still renders.

## Tuning thresholds

The thresholds are constants at the top of `test_jev_judgement.py`. Change them
deliberately, with two result files to argue from — never to make a red
assertion go green. A scenario that fails is telling you something.

## Replaying a real run

The hand-built scenarios are optimistic. To ask today's questions about states a
real run recorded — for instance, "would the `lost` question have caught the
thirty ticks dov spent stepping north and south?" — use:

```bash
cd agents
uv run python -m evals.replay_states \
    ../runs/<run_id>/agents/agent-dov/jev_states.jsonl.gz --from 500 --to 530 --every 5
```

It prints one row per replayed state: tick, chosen action, confidence, `done`,
`stuck`, `lost`, `danger` and latency. A truncated last line in the gzip stream
(a run that was killed) is treated as end of file.

## Layout

| File | What it is |
| --- | --- |
| `conftest.py` | API-key skip, the `jev` client fixture, the results recorder |
| `backends.py` | `JEV_EVAL_BACKEND` parsing and the client each backend builds |
| `openrouter_client.py` | `OpenRouterJevClient`: the same five questions, asked of a chat model |
| `elicitation.py` | `VoteJevClient` and `LogprobJevClient`: the same model, asked a fairer way |
| `run_matrix.py` | The model-by-model runner and the comparison report |
| `analyze_matrix.py` | Approaches A/B/C, consistency and scale over a matrix run's rows |
| `matrix.toml` | The candidate models |
| `scenarios.py` | `run_scenario` — the **only** place the suite calls `enumerate_options` and `build_state` |
| `test_jev_judgement.py` | The nine scenarios and their thresholds |
| `replay_states.py` | The recorded-state replay CLI |
| `dump_scenarios.py` | Captures every scenario's state, options, gold answer and assertions, offline |
| `case_studies.py` | One markdown case study per scenario, from a dump and a matrix run |

`scenarios.py` reuses the observation builders from `tests/helpers.py` through a
small `sys.path` shim, so there is one set of synthetic-world builders in the
repo. Keeping every call to `enumerate_options`/`build_state` inside
`run_scenario` means a signature change in those two functions is a one-file fix
here.
