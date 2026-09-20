# 44. Keeps going rather than abandoning the journey

Mid-walk with seven steps left and nothing wrong: `stop_going` is always on the list, and picking it would throw the journey away for nothing.

*Test*: `evals/test_jev_situations.py::test_keeps_going_rather_than_abandoning_the_journey`

## The situation

ada is one tick into a walk, carrying a pickaxe, at full health with an empty pack and 39 ticks left. The travel block says the target is rock_1 eight tiles east, the next step is east, and seven steps remain; the rock is on the edge of view with 2 stone in it. The brief says walk to rock_1 and mine it for stone, with success at 3 stone. Nothing else is in view and nothing has gone wrong. Among the options are `keep_going`, `step_towards:rock_1`, `stop_going`, and the eight compass steps.

## Why this is a good test

`stop_going` sits on the option list on every tick of every journey, and taking it throws the walk away and leaves the settler standing still having done nothing. Nothing in this state argues for it: full health, daylight, no wolves, a target in view, and one step already taken in the right direction. The scenario is a temptation test, and the tempting thing is an option that reads like a decision. A settler that abandons a walk mid-way burns the stint in an empty field and hands the planner a report about a journey that ended for no reason, which is one of the ways a real run quietly stops making progress.

## What the model saw

- **Instruction**: Walk to rock_1 and mine it for stone.
- **Success condition**: you are carrying 3 stone

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
........@.......o
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
    "instruction": "Walk to rock_1 and mine it for stone.",
    "success_condition": "you are carrying 3 stone"
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
    "inventory": {},
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 1,
    "ticks_left": 39,
    "inventory_change": {},
    "actions": {
      "step_towards": 1
    },
    "moved_from_start": "dx 1 dy 0",
    "net_tiles_moved": 1
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
  "travel": {
    "target": "rock_1 (rock_medium) at dx 8 dy 0",
    "next_step": "E",
    "steps_left": 7
  },
  "nearby": [
    {
      "id": "rock_1",
      "type": "rock_medium",
      "dx": 8,
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
| `step_towards:rock_1` | one step toward rock_1 (rock_medium) at dx 8 dy 0, 8 tiles away |
| `keep_going` | keep going toward rock_1 (rock_medium) (next step E, 7 steps left) |
| `stop_going` | abandon the journey to rock_1 (rock_medium) and stand still |
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

**Gold answer**: keep_going or step_towards:rock_1

The assertions the test makes:

```python
assert STOP_GOING in criteria, "the scenario needs the temptation to exist"
assert decision.action != STOP_GOING, f"chose {decision.action}"
assert {KEEP_GOING, step_to("rock_1"), "move_E"} & set(
    top_keys(decision)
), f"top two {top_keys(decision)}"
```

Three options are all correct and the assertion accepts all three. `keep_going` continues the code-owned journey, `step_towards:rock_1` re-issues it, and `move_E` is the same tile. Which one is chosen barely matters this tick and matters only slightly over a long walk, where `keep_going` is the cheapest way to say the same thing. So the gold is a set with a wide band, and the scenario is really a negative test dressed as a choice.

The quiet part is the nouls. One tick into a healthy walk, `done`, `stuck`, `lost` and `danger` should all be near zero, and any of them running high would end a journey that has barely started.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:rock_1` > `keep_going` x3 | - | 0.44 | 0.02 | 0.23 | 0.05 | 0.02 | 0 |
| gpt-5-nano | 100% | `step_towards:rock_1` > `keep_going` x2, `step_towards:rock_1` > `move_E` | - | 0.61 | 0.00 | 0.05 | 0.05 | 0.00 | 0 |
| qwen3.7-flash | 100% | `step_towards:rock_1` > `keep_going` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:rock_1` > `move_E` x3 | - | 0.93 | 0.00 | 0.00 | 0.00 | 0.02 | 0 |
| llama-3.1-8b | 100% | `move_E` > `step_towards:rock_1` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `step_towards:rock_1` > `keep_going`, `keep_going` > `step_towards:rock_1` x2 | - | 0.65 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:rock_1` > `keep_going` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `move_E` > `step_towards:rock_1` x3 | - | 0.47 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `move_E` > `step_towards:rock_1` x3 | - | 0.45 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `step_towards:rock_1` > `keep_going` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `keep_going` > `step_towards:rock_1` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:rock_1` x2, `step_towards:rock_1` > `keep_going` | - | 0.93 | 0.13 | 0.00 | 0.00 | 0.33 | 0 |
| gpt-4.1-nano-vote | 100% | `move_E` x2, `move_E` > `keep_going` | - | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `move_E` > `step_towards:rock_1` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.60 | 0 |
| qwen3.7-flash-vote | 100% | `keep_going` > `step_towards:rock_1`, `step_towards:rock_1` > `keep_going` x2 | - | 0.67 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:rock_1` > `keep_going` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:rock_1` | 27 |
| `move_E` | 15 |
| `keep_going` | 6 |

## Notes

Every backend passed every repeat and `stop_going` was never chosen once. The three correct keys split 27 `step_towards:rock_1`, 15 `move_E` and 6 `keep_going`, and only gemini-2.5-flash preferred `keep_going` in every repeat.

- There is no failure mode in the action column. The failures are in the numbers: gpt-4.1-nano-logprob wrote `danger` 0.60 and gpt-5-nano-vote 0.33 on a state with full health, full daylight and nothing at all in view. At 0.60 that is a reflex firing at an empty field.
- Jev wrote `danger` 0.02 and `lost` 0.05, and its confidence of 0.44 is low because three keys do the same thing. As in case study 41, spreading mass across equivalent options looks like weakness in a confidence column and is not.
- Taken with 41, 42 and 43, this is the pattern for the whole block: on the plain spatial and container scenarios every backend picks the right action, and what still separates them is whether their `done`, `lost` and `danger` numbers mean anything on a fixed threshold.
