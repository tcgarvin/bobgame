# 19. Stuck when recipe needs station

A copper pickaxe needs a workshop table, and there is none anywhere.

*Test*: `evals/test_jev_judgement.py::test_stuck_when_recipe_needs_station`  ·  **expected to fail** (`xfail`)

## The situation

Ada stands on empty ground with an axe wielded, two planks and two copper ingots in her pack. Nothing is nearby, no other entity is in view, and the state says she is at no workshop table, no furnace and no anvil. The brief is "craft a copper_pickaxe here", with "you are carrying a copper_pickaxe" as the success condition and twenty ticks. She has the exact materials the recipe wants. The option list offers `craft:wood_wall` and `craft:wood_floor`, the compass, a wait and two says. There is no copper pickaxe option, because that recipe needs a workshop table and there is none.

## Why this is a good test

The materials are right and the brief is impossible anyway, and the only evidence for that is a missing entry in the option list. This tests whether a model reads its options as information about the world rather than as a menu to pick from. The tempting answers are both there: `craft:wood_floor` is a craft, and the brief said craft, and wandering off looks like searching for the table the brief insists is "here". A failure spends twenty ticks going nowhere and hands the planner a report that does not name the reason, so the planner retries instead of building a table.

## What the model saw

- **Instruction**: craft a copper_pickaxe here
- **Success condition**: you are carrying a copper_pickaxe

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
    "instruction": "craft a copper_pickaxe here",
    "success_condition": "you are carrying a copper_pickaxe"
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
    "wielded": "axe",
    "inventory": {
      "copper_ingot": 2,
      "plank": 2
    },
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 20,
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
| `craft:wood_wall` | craft wood_wall using 2 plank |
| `craft:wood_floor` | craft wood_floor using 1 plank, 2 of them |
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

**Gold answer**: _none recorded; the test's own assertions are the gold answer_

The assertions the test makes:

```python
blocked = max(decision.stuck, decision.lost)
assert blocked > STUCK_OR_LOST_HIGH, f"stuck {decision.stuck}, lost {decision.lost}"
```

This is the scenario I would argue with, and it is marked xfail because Jev itself fails it. The assertion takes `max(stuck, lost)` above 0.5, which is honest about the fact that the two nouls overlap completely here: a missing station is both "the brief has become impossible" and "something it needs is missing from this state". Jev has a real position rather than a confused one, at stuck 0.31 and lost 0.35 to 0.47 across repeats. It is simply not willing to spend 0.5, which is plausible, because in a settled world a workshop table is usually a short walk away and this state cannot prove there is none anywhere. The word "here" in the instruction carries most of the weight and neither noul question mentions it. The xfail is kept as a marker for the next wording or model change, not as a verdict.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 0% | `wait` > `move_E` x3 | - | 0.37 | 0.02 | 0.31 | 0.42 | 0.02 | 0 |
| gpt-5-nano | 0% | `wait` > `move_E`, `craft:wood_floor` > `craft:wood_wall`, `move_E` > `move_N` | - | 0.39 | 0.02 | 0.10 | 0.10 | 0.17 | 0 |
| qwen3.7-flash | 33% | `wait` > `move_E` x3 | - | 0.96 | 0.00 | 0.00 | 0.33 | 0.00 | 0 |
| mistral-nemo | 0% | `move_N` > `move_NE` x3 | - | 0.73 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 0% | `craft:wood_floor` > `move_N` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `wait` x2, `wait` > `move_E` | - | 0.97 | 0.00 | 0.00 | 1.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `wait` > `move_N` x3 | - | 0.56 | 0.00 | 0.00 | 1.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `move_N` > `move_W` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 0% | `wait` > `say:all_good` x3 | - | 0.50 | 0.00 | 0.20 | 0.30 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `wait` > `say:all_good` x3 | - | 0.70 | 0.00 | 0.00 | 1.00 | 0.00 | 0 |
| gemini-2.5-flash | 67% | `wait` x3 | - | 1.00 | 0.00 | 0.67 | 0.67 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `wait`, `wait` > `craft:wood_floor` x2 | - | 0.67 | 0.07 | 0.00 | 0.00 | 0.13 | 0 |
| gpt-4.1-nano-vote | 0% | `move_N` > `move_NW`, `move_N` > `move_W`, `move_W` > `move_NW` | - | 0.73 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 0% | `move_N` > `move_W` x3 | - | 0.40 | 0.38 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `wait` x3 | - | 1.00 | 0.00 | 1.00 | 0.47 | 0.00 | 0 |
| qwen3.7-flash-logprob | 0% | `wait` > `move_E` x3 | - | 0.96 | 0.00 | 0.09 | 0.27 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `wait` | 30 |
| `move_N` | 11 |
| `craft:wood_floor` | 5 |
| `move_E` | 1 |
| `move_W` | 1 |

## Notes

Jev fails, 0 of 3, as the marker says. Four backends pass outright: gpt-oss-20b and gemini-2.5-flash-lite at lost 1.00, gpt-4.1-mini at lost 1.00, and qwen3.7-flash-vote at stuck 1.00. gemini-2.5-flash passes two repeats of three at 0.67 on both.

- Every backend that gets this right answers through `lost`, not `stuck`, including the one that splits. The models that agree with the scenario disagree with the noul it is named after, which is itself an argument for rewording rather than retuning.
- The failure mode among the rest is the cheap-model constant seen in cases 13 and 14: mistral-nemo, llama-3.1-8b and gpt-4.1-nano write 0.00 on both nouls and walk north. gpt-4.1-nano-logprob writes done 0.38 for a settler carrying no pickaxe at all.
- Thirty of the 48 top-1 picks are `wait`, which is the right instinct with the wrong number attached. A settler that waits and reports nothing wrong waits for twenty ticks. Jev is the neatest example: it waits at confidence 0.37, which is its way of saying it has nothing worth doing, but the stint gate reads the nouls at 0.6, not the confidence.
