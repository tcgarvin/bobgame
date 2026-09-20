# 13. Done when inventory met

Three stone in the pack against "at least 3 stone" is plainly finished.

*Test*: `evals/test_jev_judgement.py::test_done_when_inventory_met`

## The situation

Ada is on open ground with a pickaxe wielded and three stone in her pack. A medium rock stands two tiles east with two units left in it. She is at full health, food 80, fatigue zero, and thirty ticks are on the clock. The brief says "gather stone", and its success condition is "you are carrying at least 3 stone".

## Why this is a good test

This is the plainest possible reading of the `done` noul: count what is in the pack and compare it to a number written in the brief. Nothing else in the state matters. The temptation is the instruction rather than the condition, because "gather stone" is an open-ended order and there is a rock right there with stone still in it. The test scores only `done`, so the action is free. A failure here costs a whole stint: the settler works the full thirty ticks on a job that was finished before it started, and the planner spends one of its twenty tool calls to discover that.

## What the model saw

- **Instruction**: gather stone
- **Success condition**: you are carrying at least 3 stone

**The map Jev was shown** (17x17, `@` is the settler):

```text
.................
.................
.................
.................
.................
.................
.................
.................
........@.o......
.................
.................
.................
.................
.................
.................
.................
.................
```

Legend: . walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown

**The facts every state carries**:

- Food falls 1 every 4 ticks. At food 0 you lose 1 health every 4 ticks until you eat. Eating a berry restores 20 food; berries come from bushes marked B on the map (b is a bush with no berry). Health regenerates 1 per 5 ticks only while food is above 50 and you are not tired.
- A wolf has 16 health and bites an adjacent settler for 3 every tick. Every settler attacking the same wolf hits it on the same tick.
- Fatigue rises 1 every 4 ticks by day and every 3 ticks at night. From 60 you are tired: the work a tool adds per extract action is halved and your attacks hit for 1 less. At 100 you collapse where you stand and sleep until fatigue falls to 70. Sleeping on a bed recovers 1 fatigue per tick at night and 1 fatigue per 2 ticks by day; on the ground 1 fatigue per 2 ticks at night and 1 fatigue per 4 ticks by day.
- A day is 300 ticks: the first two thirds are light and the rest is night.

**The rest of the state**, as sent (JSON):

```json
{
  "brief": {
    "instruction": "gather stone",
    "success_condition": "you are carrying at least 3 stone"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "20/20",
    "food": "80/100",
    "fatigue": "0/100 (fresh)",
    "asleep": false,
    "wielded": "pickaxe",
    "inventory": {
      "stone": 3
    },
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 30,
    "inventory_change": {},
    "actions": {},
    "moved_from_start": "dx 0 dy 0",
    "net_tiles_moved": 0
  },
  "clock": {
    "day": 0,
    "tick_of_day": "40/300",
    "night": false
  },
  "settlement": {
    "dx": 0,
    "dy": 0
  },
  "travel": null,
  "nearby": [
    {
      "id": "rock_1",
      "type": "rock_medium",
      "dx": 2,
      "dy": 0,
      "remaining": 2,
      "yields": "stone"
    }
  ],
  "entities": [],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": []
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `craft:road` | craft road using 2 stone, 4 of them |
| `move_N` | step one tile N onto known ground |
| `move_NE` | step one tile NE onto known ground |
| `move_E` | step one tile E onto known ground |
| `move_SE` | step one tile SE onto known ground |
| `move_S` | step one tile S onto known ground |
| `move_SW` | step one tile SW onto known ground |
| `move_W` | step one tile W onto known ground |
| `move_NW` | step one tile NW onto known ground |
| `say:come_here` | say "Come to me." out loud to anyone nearby |
| `say:all_good` | say "All good here." out loud to anyone nearby |
| `step_towards:rock_1` | one step toward rock_1 (rock_medium) at dx 2 dy 0, 2 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: _none recorded; the test's own assertions are the gold answer_

The assertions the test makes:

```python
assert decision.done > DONE_HIGH, f"done was {decision.done}"
```

Two things make this softer than it looks. First, "at least 3" is exactly met and not exceeded, so a model reading the spirit of "gather stone" rather than the letter of the success condition has somewhere to stand. The noul wording asks only about the success condition, and that is the convention. Second, the action column is not a failure signal here: 38 of 48 top-1 picks are `step_towards:rock_1`, and that is fine, because the stint needs `done` at 0.6 on two consecutive ticks before it ends. Working while done is high is legal. Only the number is on trial.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:rock_1` > `wait` x3 | - | 0.75 | 0.98 | 0.12 | 0.05 | 0.03 | 0 |
| gpt-5-nano | 0% | `step_towards:rock_1` > `move_E` x3 | - | 0.57 | 0.00 | 0.08 | 0.05 | 0.15 | 0 |
| qwen3.7-flash | 100% | `step_towards:rock_1` > `wait` x3 | - | 0.92 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `step_towards:rock_1` > `move_E` x2, `step_towards:rock_1` > `wait` | - | 0.73 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 0% | `step_towards:rock_1` > `move_E` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `wait` > `step_towards:rock_1` x3 | - | 0.77 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.70 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `step_towards:rock_1` > `move_E` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `wait` > `step_towards:rock_1` x3 | - | 0.50 | 0.99 | 0.01 | 0.01 | 0.02 | 0 |
| gpt-4.1-mini | 100% | `wait` > `step_towards:rock_1`, `wait` > `say:all_good` x2 | - | 0.70 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:rock_1` > `wait` x2, `wait` | - | 0.95 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `step_towards:rock_1`, `step_towards:rock_1` > `move_E` x2 | - | 0.87 | 0.20 | 0.00 | 0.00 | 0.27 | 0 |
| gpt-4.1-nano-vote | 0% | `step_towards:rock_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 0% | `step_towards:rock_1` > `move_E` x3 | - | 0.40 | 0.52 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:rock_1` > `wait` x3 | - | 0.63 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:rock_1` > `wait` x3 | - | 0.95 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:rock_1` | 38 |
| `wait` | 10 |

## Notes

Eight backends pass alongside Jev, which answered done 0.98 in every repeat. The failures are one clean mode: done exactly 0.00 with a perfectly sensible action. gpt-5-nano, mistral-nemo, llama-3.1-8b and gpt-4.1-nano all answer the instruction instead of the success condition, and they do it with no hedging at all.

- gpt-4.1-nano-logprob is the informative near-miss at 0.52. It knows, and lands just under Jev's 0.6 gate. That is precisely the recalibration approach B is about: in the whole-run analysis its own best `done` threshold is 0.50, and even there its positives and negatives overlap.
- Vote hurt again: gpt-4.1-nano-vote writes 0.00 and gpt-5-nano-vote 0.20 on a brief that is satisfied.
- Jev's 0.98 here against 0.02 on the matched negative in case 14 is a margin of 0.96 with no tuning, and it is why its `done` AUC over the full suite is 1.00.
