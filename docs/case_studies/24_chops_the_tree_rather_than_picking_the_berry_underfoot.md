# 24. Chops the tree rather than picking the berry underfoot

Well fed, told to gather wood, standing on a bush with a berry: the berry is free and legal and still the wrong thing to spend the tick on.

*Test*: `evals/test_jev_situations.py::test_chops_the_tree_rather_than_picking_the_berry_underfoot`

## The situation

Ada is standing on a bush that has one berry, with an axe in hand, an empty pack and food at 80 of 100. A tree holding four wood is one tile east. The brief says to chop tree_1 and is finished when the pack holds four wood. Picking the berry is legal, costs one tick and is on the option list as `collect:bush_1`.

## Why this is a good test

This isolates whether a free and immediately rewarding action can pull a settler off its brief. The berry costs nothing, yields 20 food and is underfoot, so a model that treats "resource in reach" as a goal in itself will take it. The state argues against: food is 80 of 100, health regenerates only above 50, and the brief names the tree. In a real run a settler that grazes every bush it walks across never finishes a stint. It burns the tick budget, hands back a report with no wood in it, and the planner pays for another turn to say the same thing again.

## What the model saw

- **Instruction**: Chop tree_1 for wood.
- **Success condition**: you are carrying 4 wood

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
........@T.......
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
    "instruction": "Chop tree_1 for wood.",
    "success_condition": "you are carrying 4 wood"
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
      "id": "bush_1",
      "type": "bush",
      "dx": 0,
      "dy": 0,
      "berry": true
    },
    {
      "id": "tree_1",
      "type": "tree",
      "dx": 1,
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
| `collect:bush_1` | pick the berry off the bush on this tile |
| `extract:tree_1` | harvest wood from the tree at dx 1 dy 0, 4 units left (3 work per action with the axe, 3 work per unit) |
| `move_N` | step one tile N onto known ground |
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

**Gold answer**: extract:tree_1

The assertions the test makes:

```python
assert "collect:bush_1" in criteria, "the scenario needs the temptation to exist"
assert "extract:tree_1" in top_keys(decision), f"top two {top_keys(decision)}"
```

Thin, and the scenario is closer to a floor than a hard case. The only defence of `collect:bush_1` is opportunism: the stint has 30 ticks for a job that needs about four extracts, and one tick for 20 food is cheap insurance against a hunger the settler will eventually have. That is not a silly argument, and the assertion is generous enough to accommodate it: it only asks that `extract:tree_1` be in the top two, so a model may rank the berry second and still pass. All four nouls should sit low. Nothing is finished, nothing is impossible, nothing is missing and there is no wolf on the map.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.99 | 0.02 | 0.17 | 0.05 | 0.03 | 0 |
| gpt-5-nano | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.67 | 0.02 | 0.08 | 0.05 | 0.27 | 0 |
| qwen3.7-flash | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `extract:tree_1` > `move_N` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `extract:tree_1` > `move_S` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.88 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `extract:tree_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `extract:tree_1` > `move_W` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.73 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `extract:tree_1`, `extract:tree_1` > `collect:bush_1` x2 | - | 0.80 | 0.13 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-4.1-nano-vote | 100% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `extract:tree_1` > `move_W` x3 | - | 0.60 | 0.05 | 0.00 | 0.00 | 0.03 | 0 |
| qwen3.7-flash-vote | 100% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:tree_1` > `collect:bush_1` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:tree_1` | 48 |

## Notes

Every backend passes and all 48 top-1 picks are `extract:tree_1`, so the pass column says nothing. The interesting column is the second place. Jev, qwen3.7-flash, gpt-5-nano, gpt-oss-20b, both gemini models, gpt-4.1-mini and qwen under logprob all rank `collect:bush_1` second: they saw the temptation and declined it. Mistral-nemo, llama-3.1-8b, gpt-4.1-nano and gpt-oss-120b put an arbitrary move direction second, which reads as a default rather than a judgement. Jev's confidence of 0.99 is near the top of its range across these eleven scenarios. What this says about Jev is mostly negative and worth saying plainly: on a tick where the brief names the target and the target is adjacent, a cheap chat model is exactly as good, and the case for a specialised decision model has to be made somewhere else.
