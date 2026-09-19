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

Point it at another model with `JEV_EVAL_MODEL` (it defaults to
`jevclient.DEFAULT_MODEL`):

```bash
JEV_EVAL_MODEL=jev-2026-08 uv run pytest evals -q -m jev_live
```

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
| `scenarios.py` | `run_scenario` — the **only** place the suite calls `enumerate_options` and `build_state` |
| `test_jev_judgement.py` | The nine scenarios and their thresholds |
| `replay_states.py` | The recorded-state replay CLI |

`scenarios.py` reuses the observation builders from `tests/helpers.py` through a
small `sys.path` shim, so there is one set of synthetic-world builders in the
repo. Keeping every call to `enumerate_options`/`build_state` inside
`run_scenario` means a signature change in those two functions is a one-file fix
here.
