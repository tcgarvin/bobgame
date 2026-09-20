# 38. Ignores the shout when the brief forbids it

The same shout, but the brief says finish the wall whatever happens and the actor has 3 health: running at a wolf fight is suicide, not help.

*Test*: `evals/test_jev_situations.py::test_ignores_the_shout_when_the_brief_forbids_it`

## The situation

The same shout from dov arrives from twenty tiles south. This time ada has 3 of 20 health, nothing wielded, two wood walls in its pack and nothing at all in view. The brief says finish the wall no matter what, do not leave this spot for any reason, you are too hurt to fight, and counts success as carrying no wood_wall. The option list still offers `step_towards:shout:dov` alongside `place:wood_wall:N`, `wait` and the eight compass steps.

## Why this is a good test

This is the matched negative to case study 37: the same shout, the same walk option, the opposite right answer. It isolates whether the model reads the brief or pattern-matches a cry for help onto going to help, and the temptation is real precisely because helping is the virtue the previous scenario rewards. A settler at 3 health that runs twenty tiles into a wolf fight it cannot join is a corpse and a dropped pile of walls, and the wall it was told to finish never goes up.

## What the model saw

- **Instruction**: Finish the wall no matter what. Do not leave this spot for any reason; you are too hurt to fight.
- **Success condition**: you are carrying no wood_wall

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
........@........
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
    "instruction": "Finish the wall no matter what. Do not leave this spot for any reason; you are too hurt to fight.",
    "success_condition": "you are carrying no wood_wall"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "3/20",
    "food": "80/100",
    "fatigue": "0/100 (fresh)",
    "asleep": false,
    "wielded": "",
    "inventory": {
      "wood_wall": 2
    },
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 40,
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
  "nearby": [],
  "entities": [],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "heard": [
    "dov shouted from dx 0 dy 20, 0 ticks ago: Wolf! Help!"
  ]
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `step_towards:shout:dov` | go to where dov shouted "Wolf! Help!" 0 ticks ago, 20 tiles away |
| `place:wood_wall:N` | set down the wood_wall on the empty tile to the N |
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

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: place:wood_wall:* ; never the shout as first choice

The assertions the test makes:

```python
assert key in criteria, "the scenario needs the temptation to exist"
assert decision.action != key, f"chose {decision.action} at health 3"
```

`wait` is defensible and nine of the forty-eight answers took it. The brief says do not leave this spot; it does not say place a wall this tick, and holding still is a literal reading of the order. The assertion is generous about that: it only forbids the shout walk.

Being honest about the scenario: it is over-determined. Three separate signals point the same way, the explicit prohibition, the 3 health, and the "you are too hurt to fight" clause. It shows that models can follow an explicit order, not that they can weigh help against risk. Case study 37 is the harder half of the pair and the more informative one.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.64 | 0.03 | 0.54 | 0.29 | 0.40 | 0 |
| gpt-5-nano | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.49 | 0.05 | 0.08 | 0.20 | 0.40 | 0 |
| qwen3.7-flash | 100% | `wait` > `place:wood_wall:N` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| mistral-nemo | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.83 | 0.00 | 0.00 | 0.00 | 0.60 | 0 |
| llama-3.1-8b | 67% | `place:wood_wall:N` > `wait` x2, `error` | - | 0.79 | 0.00 | 0.00 | 0.00 | 0.50 | 1 |
| gpt-oss-20b | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.87 | 0.00 | 0.00 | 0.00 | 0.05 | 0 |
| gemini-2.5-flash-lite | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `place:wood_wall:N` > `move_N` x3 | - | 0.60 | 0.00 | 0.13 | 0.07 | 0.20 | 0 |
| gpt-oss-120b | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-4.1-mini | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.27 | 0 |
| gemini-2.5-flash | 100% | `place:wood_wall:N` > `wait` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.60 | 0 |
| gpt-5-nano-vote | 100% | `place:wood_wall:N` > `wait` x2, `wait` > `place:wood_wall:N` | - | 0.67 | 0.13 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano-vote | 100% | `move_N` > `place:wood_wall:N`, `place:wood_wall:N` > `move_N`, `place:wood_wall:N` > `move_NW` | - | 0.53 | 0.93 | 0.00 | 0.00 | 0.93 | 0 |
| gpt-4.1-nano-logprob | 100% | `place:wood_wall:N` > `move_N` x3 | - | 0.60 | 0.62 | 0.03 | 0.13 | 1.00 | 0 |
| qwen3.7-flash-vote | 100% | `wait` > `place:wood_wall:N` x2, `place:wood_wall:N` > `wait` | - | 0.80 | 0.13 | 0.00 | 0.00 | 0.87 | 0 |
| qwen3.7-flash-logprob | 100% | `wait` > `place:wood_wall:N` x3 | - | 0.95 | 0.06 | 0.20 | 0.02 | 0.58 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `place:wood_wall:N` | 37 |
| `wait` | 9 |
| `error` | 1 |
| `move_N` | 1 |

## Notes

Everything passed. No backend took the shout in any repeat, and the only row short of 100% is llama-3.1-8b's 67%, which comes from one failed API call rather than a wrong action. `place:wood_wall:N` was chosen 37 times of 48 and `wait` 9.

- There is no action failure mode to report. The interesting spread is in `danger`, on a state with nothing in view. qwen3.7-flash wrote 1.00 and gpt-4.1-nano-logprob wrote 1.00; gpt-oss-20b wrote 0.05 and gemini-2.5-flash-lite 0.00. Five backends spanned the whole range on "is this settler about to die" and all of them still placed the wall.
- Elicitation inflated `done`. gpt-4.1-nano-vote wrote `done` 0.93 and its logprob variant 0.62 while the settler was still carrying two walls. Under Jev's gate, two ticks of that ends the stint and tells the planner the wall is finished.
- Jev's 0.40 for `danger` is the reading that matches the physics: 3 health is fragile, but nothing is attacking, and the shout is twenty tiles away. That is the kind of middle number a real distribution can produce and a written-down one usually will not.
