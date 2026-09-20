# 11. Eats when told to eat



*Test*: `evals/test_jev_basics.py::test_eats_when_told_to_eat`

## The situation

Ada stands alone on open grass at full health with 2 berries in her pack and food at 45 of 100. Nothing else is in view. The brief says to eat a berry, and counts the job done when her food is above 60. A berry restores 20 food, so one is enough. The option list offers `eat:berry`, the eight moves, `wait` and two `say` lines, and the stint has 5 ticks.

## Why this is a good test

This is the shortest brief in the set and the one where doing nothing is most tempting: food 45 is not an emergency, the settler is at full health, and `wait` costs nothing visible. The judgement is simply to spend the tick on the action the brief names. A failure here in a real run is a settler sitting on food it is carrying while its food drains 1 every 4 ticks, and eventually losing health at 0 with berries in the pack, which is the single most avoidable death in the world.

## What the model saw

- **Instruction**: Eat a berry.
- **Success condition**: your food is above 60

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
    "instruction": "Eat a berry.",
    "success_condition": "your food is above 60"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "20/20",
    "food": "45/100",
    "fatigue": "0/100 (fresh)",
    "asleep": false,
    "wielded": "",
    "inventory": {
      "berry": 2
    },
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 5,
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
  "recent": []
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `eat:berry` | eat a berry to restore 20 food (food now 45) |
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

**Gold answer**: `eat:berry`

The assertions the test makes:

```python
assert decision.action == "eat:berry", f"chose {decision.action}"
```

Nothing else is defensible on a five tick budget. The only genuine subtlety is arithmetic: the success condition is food above 60 and one berry takes 45 to 65, so eating once is enough and eating twice would waste the second berry. The nouls should all be low, and `danger` in particular, since the settler is at 20 of 20 health with food well above 0 and no wolf in the state.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `eat:berry` > `wait` x2, `eat:berry` > `move_E` | 3/3 | 0.97 | 0.04 | 0.18 | 0.05 | 0.04 | 0 |
| gpt-5-nano | 100% | `eat:berry` > `move_E` x2, `eat:berry` > `wait` | 3/3 | 0.62 | 0.00 | 0.25 | 0.17 | 0.42 | 0 |
| qwen3.7-flash | 100% | `eat:berry` > `wait` x3 | 3/3 | 0.94 | 0.33 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `eat:berry` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `eat:berry` > `move_N` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `eat:berry` > `wait` x3 | 3/3 | 0.92 | 0.32 | 0.00 | 0.00 | 0.02 | 0 |
| gemini-2.5-flash-lite | 100% | `eat:berry` > `wait` x3 | 3/3 | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `eat:berry` > `move_N` x3 | 3/3 | 0.60 | 0.00 | 0.00 | 0.00 | 0.13 | 0 |
| gpt-oss-120b | 100% | `eat:berry` > `wait` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `eat:berry` > `wait` x3 | 3/3 | 0.73 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `eat:berry` > `wait` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `eat:berry` x3 | 3/3 | 1.00 | 0.20 | 0.00 | 0.00 | 0.47 | 0 |
| gpt-4.1-nano-vote | 100% | `eat:berry` x3 | 3/3 | 1.00 | 0.87 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-4.1-nano-logprob | 100% | `eat:berry` > `move_N` x3 | 3/3 | 0.60 | 0.00 | 0.61 | 0.79 | 1.00 | 0 |
| qwen3.7-flash-vote | 100% | `eat:berry` x3 | 3/3 | 1.00 | 0.13 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `eat:berry` > `wait` x3 | 3/3 | 0.94 | 0.02 | 0.10 | 0.00 | 0.42 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `eat:berry` | 48 |

## Notes

Every backend passed, 48 of 48 on `eat:berry`, so again the nouls carry the result. This scenario produced the worst noul row in the basics set: `gpt-4.1-nano-logprob` wrote `stuck` 0.61, `lost` 0.79 and `danger` 1.00 for a healthy settler with berries in her pack and a legal eat in front of her. On Jev's unchanged thresholds two of those fire the stint-ending rules and the third trips the run-away rule. It is not an isolated slip either: in the run-wide analysis that backend's worst-ordered `stuck` and `danger` pairs are both this scenario, which is why its `stuck` AUC is 0.29, below chance. `qwen3.7-flash-logprob` also wrote `danger` 0.42 and `gpt-5-nano` 0.42 here. Jev wrote 0.04 danger and 0.18 stuck, and took the berry at 0.97 confidence.
