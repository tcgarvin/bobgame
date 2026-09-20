# 14. Not done when inventory short

One stone of three is not finished, however close it feels.

*Test*: `evals/test_jev_judgement.py::test_not_done_when_inventory_short`

## The situation

The same settler, the same rock two tiles east, the same brief: "gather stone", "you are carrying at least 3 stone". The only change is the pack. Ada carries one stone, not three. She is healthy, fed, fresh, with a pickaxe wielded and thirty ticks left.

## Why this is a good test

This is the matched negative for case 13, and the pair is the real test. On its own it catches a model that says `done` whenever the brief looks nearly satisfiable, or whenever the same word appears in the pack and in the condition. A false `done` here would end the stint after a single tick, hand the planner a report saying the job is finished, and cost a planner turn to restart the identical brief. That is the expensive half of the system paying for a cheap model's error.

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
      "stone": 1
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
assert decision.done < DONE_LOW, f"done was {decision.done}"
```

There is nothing debatable in the situation itself: one is less than three. The ambiguity is in what passing means. Almost everything passes, which tells you models are far better at denying `done` than at asserting it, and that a scenario like this proves very little in isolation. Read it next to case 13 or not at all.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.90 | 0.02 | 0.15 | 0.07 | 0.02 | 0 |
| gpt-5-nano | 100% | `move_E` > `step_towards:rock_1` x2, `move_E` > `move_S` | - | 0.42 | 0.00 | 0.10 | 0.10 | 0.37 | 0 |
| qwen3.7-flash | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:rock_1` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `step_towards:rock_1` > `wait`, `step_towards:rock_1`, `step_towards:rock_1` > `move_E` | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.47 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `step_towards:rock_1` > `wait` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:rock_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 33% | `step_towards:rock_1` x3 | - | 1.00 | 0.27 | 0.00 | 0.00 | 0.33 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:rock_1` > `move_E` x2, `step_towards:rock_1` | - | 0.87 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.53 | 0.02 | 0.00 | 0.02 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:rock_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:rock_1` | 45 |
| `move_E` | 3 |

## Notes

Fifteen of sixteen backends pass. The only miss is gpt-5-nano-vote at done 0.27, a model that votes "yes, finished" roughly one time in four on an unambiguous no. Actions were near-uniform, 45 of 48 picks being the step toward the rock.

- The pair exposes the cheap models properly. mistral-nemo, llama-3.1-8b and gpt-4.1-nano pass this scenario with done 0.00 and fail case 13 with done 0.00. The same number both times. They are not judging the state, they are emitting a constant, and only the pair makes that visible. All three have `done` AUC 0.50 across the whole run, which is the signature of exactly that.
- Jev answers 0.02 here and 0.98 there. That separation, not this row's pass, is the result.
