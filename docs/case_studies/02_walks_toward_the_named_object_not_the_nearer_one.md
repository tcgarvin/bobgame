# 02. Walks toward the named object not the nearer one

A tree two tiles east, a rock five tiles west, and the brief names the rock.

*Test*: `evals/test_jev_basics.py::test_walks_toward_the_named_object_not_the_nearer_one`

## The situation

Ada is on open grass with a pickaxe already in hand and an empty pack. Two things are in view: tree_1, two tiles east, and rock_1, five tiles west with four units of stone in it. The brief names the rock, not the tree: walk to rock_1 and mine it, done when she carries 2 stone. She has 30 ticks and has not moved yet.

## Why this is a good test

This isolates whether the brief beats proximity. The tempting wrong answer is the tree: it is closer, it is a resource, and `step_towards:tree_1` is right there in the option list. `move_E` is tempting for the same reason with none of the reasoning. A settler that follows the nearer object walks two tiles the wrong way, chops wood it was not asked for with a pickaxe that is bad at it, and comes back with the wrong item, which is worse than coming back empty because the planner has to notice the mismatch.

## What the model saw

- **Instruction**: Walk to rock_1 and mine it for stone.
- **Success condition**: you are carrying 2 stone

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
...o....@.T......
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
    "success_condition": "you are carrying 2 stone"
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
      "id": "tree_1",
      "type": "tree",
      "dx": 2,
      "dy": 0,
      "remaining": 4,
      "yields": "wood"
    },
    {
      "id": "rock_1",
      "type": "rock_medium",
      "dx": -5,
      "dy": 0,
      "remaining": 4,
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
| `step_towards:rock_1` | one step toward rock_1 (rock_medium) at dx -5 dy 0, 5 tiles away |
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
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 2 dy 0, 2 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: `move_W`, `step_towards:rock_1`

The assertions the test makes:

```python
assert decision.action in offered, f"chose {decision.action}"
assert decision.probabilities.get(f"{STEP_KEY_PREFIX}tree_1", 0) < 0.3
```

`move_W` and `step_towards:rock_1` are both accepted; as in case 01 the rock is on a straight line, so they are the same step. This scenario carries a second assertion that the tree's probability stays under 0.3, which is the only place in the basics set where the shape of the distribution is graded rather than just its argmax. That assertion is fair for Jev, which returns a real distribution, and softer for a chat model whose per-option numbers are self-reported. The nouls should all be low for the same reason as case 01: five tiles is far, not lost.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.81 | 0.02 | 0.17 | 0.05 | 0.02 | 0 |
| gpt-5-nano | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.56 | 0.00 | 0.10 | 0.07 | 0.08 | 0 |
| qwen3.7-flash | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.94 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:rock_1` > `wait` x3 | 3/3 | 0.88 | 0.00 | 0.00 | 0.00 | 0.02 | 0 |
| llama-3.1-8b | 100% | `step_towards:rock_1` > `move_N` x3 | 3/3 | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `step_towards:rock_1` > `move_W` x2, `step_towards:rock_1` > `move_E` | 3/3 | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.82 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `step_towards:rock_1` > `step_towards:tree_1` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.05 | 0 |
| gpt-4.1-mini | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:rock_1` x3 | 3/3 | 1.00 | 0.07 | 0.00 | 0.00 | 0.40 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:rock_1` x3 | 3/3 | 1.00 | 0.07 | 0.00 | 0.00 | 0.07 | 0 |
| gpt-4.1-nano-logprob | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.60 | 0.00 | 0.01 | 0.05 | 0.86 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:rock_1` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:rock_1` > `move_W` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:rock_1` | 48 |

## Notes

Every backend chose `step_towards:rock_1` in all 48 repeats, so nobody fell for the nearer tree as a top-1. `gpt-oss-120b` came closest, ranking `step_towards:tree_1` second in all three repeats, and still under the 0.3 bar. The noul columns are where the chat models wander: `gpt-4.1-nano-logprob` wrote `danger` 0.86 and `gpt-5-nano-vote` wrote 0.40 for a settler at 20/20 health in an empty field with no wolf anywhere in the state. Jev returned 0.02. The self-reported danger number is not measuring anything here, which is exactly the caveat the design note makes, and on the drop-in thresholds a 0.86 danger is close to the hard rule that would send the settler running home.
