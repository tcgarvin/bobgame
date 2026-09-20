# 01. Walks east to the tree it was sent to

A tree four tiles east and "go to tree_1": step toward it, or east.

*Test*: `evals/test_jev_basics.py::test_walks_east_to_the_tree_it_was_sent_to`

## The situation

Ada stands on open grass in the middle of an empty view. She is at full health, food 80, fresh, carrying nothing and wielding nothing. One thing is in sight: tree_1, four tiles due east, with four units of wood left in it. The brief says to walk to tree_1 and chop it, and counts the job done when she carries 2 wood. She has 30 ticks and has used none of them.

## Why this is a good test

This is the simplest thing a per-tick chooser has to do: notice the target the brief names and move toward it. The wrong answers are cheap. `wait` is safe, nothing is attacking, and seven of the eight compass moves walk away from the tree. The two `say` options are always on the list and always available as a way to look busy. A model that picks any of those in a real run burns ticks standing still or drifting, the stint returns with no wood, and the planner spends another turn on a brief that was never hard.

## What the model saw

- **Instruction**: Walk to tree_1 and chop it for wood.
- **Success condition**: you are carrying 2 wood

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
........@...T....
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
    "instruction": "Walk to tree_1 and chop it for wood.",
    "success_condition": "you are carrying 2 wood"
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
    "wielded": "",
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
      "dx": 4,
      "dy": 0,
      "remaining": 4,
      "yields": "wood"
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
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 4 dy 0, 4 tiles away |
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

**Gold answer**: `move_E`, `step_towards:tree_1`

The assertions the test makes:

```python
assert decision.action in offered, f"chose {decision.action}"
```

`move_E` and `step_towards:tree_1` are both accepted, and here they do the same thing: the tree is due east over open ground. The gold answer is a convention in one respect. `step_towards` is preferred in practice because taking it sets the stint's travel state, so `keep_going` continues the walk on later ticks, while `move_E` has to be re-chosen every tick. All four nouls should be low. Nothing is finished, and the `stuck` and `lost` wordings say explicitly that being far from a target with a step option toward it is neither stuck nor lost, so a high `lost` here would be the model reading "four tiles away" as "unreachable".

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.93 | 0.02 | 0.21 | 0.27 | 0.02 | 0 |
| gpt-5-nano | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.62 | 0.00 | 0.02 | 0.02 | 0.07 | 0 |
| qwen3.7-flash | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.47 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.85 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:tree_1` > `wait` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:tree_1` x3 | 3/3 | 1.00 | 0.20 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:tree_1` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.70 | 0.38 | 0.01 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:tree_1` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:tree_1` > `move_E` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:tree_1` | 48 |

## Notes

Every backend passed, 48 of 48 repeats on `step_towards:tree_1`, so the interesting column is the nouls. Most chat models wrote 0.00 for all four. Jev instead returned 0.21 on `stuck` and 0.27 on `lost`, its highest `lost` of the basics set, which is a real distribution sitting well under the 0.6 gate rather than a claim that anything is wrong. The one odd number from a chat model is `gpt-4.1-nano-logprob` reading `done` at 0.38 for a settler carrying nothing at all. Two backends ranked something other than a move second, `gemini-2.5-flash` putting `wait` behind its pick, which costs nothing while the top-1 is right.
