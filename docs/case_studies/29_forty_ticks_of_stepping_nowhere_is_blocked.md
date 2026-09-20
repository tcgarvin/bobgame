# 29. Forty ticks of stepping nowhere is blocked

`so_far` says forty steps and no net movement, and `travel.next_step` says blocked: the state contains the whole failure and nothing here will fix it.

*Test*: `evals/test_jev_situations.py::test_forty_ticks_of_stepping_nowhere_is_blocked`

## The situation

Ada stands in the middle of a known clearing with impassable ground ringing it on every side. The brief sent it to the far bank, fifteen tiles south, to gather three fiber. `so_far` records 40 ticks used, 28 `step_towards` actions, 12 moves and net movement of zero, with 20 ticks left. The travel block names the far bank and reports `next_step: blocked`. A tree sits three tiles northeast, inside the ring.

## Why this is a good test

Everything needed to make the call is in the state and none of it is on the current tile. The model has to combine three facts, forty ticks spent, nothing moved, and the path finder saying blocked, and conclude that no action available this tick helps. The tempting answers are all local and all look like doing something: step north onto perfectly walkable ground, step toward the tree, or `stop_going`. A failure is the stint that never ends, a settler pacing a clearing until the tick budget runs out and handing back a report in which nothing appears to have gone wrong. That failure mode is why the `lost` noul was added in the first place.

## What the model saw

- **Instruction**: Walk to the reeds on the far bank and gather fiber.
- **Success condition**: you are carrying 3 fiber

**The map Jev was shown** (17x17, `@` is the settler):

```text
#################
#...............#
#...............#
#...............#
#...............#
#..........T....#
#...............#
#...............#
#.......@.......#
#...............#
#...............#
#...............#
#...............#
#...............#
#...............#
#...............#
#################
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
    "instruction": "Walk to the reeds on the far bank and gather fiber.",
    "success_condition": "you are carrying 3 fiber"
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
    "ticks_used": 40,
    "ticks_left": 20,
    "inventory_change": {},
    "actions": {
      "step_towards": 28,
      "move": 12
    },
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
  "travel": {
    "target": "the far bank at dx 0 dy 15",
    "next_step": "blocked",
    "steps_left": null
  },
  "nearby": [
    {
      "id": "tree_1",
      "type": "tree",
      "dx": 3,
      "dy": -3,
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
| `stop_going` | abandon the journey to the far bank and stand still |
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
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 3 dy -3, 3 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: max(stuck, lost) above 0.5

The assertions the test makes:

```python
blocked = max(decision.stuck, decision.lost)
assert blocked > STUCK_OR_LOST_HIGH, f"stuck {decision.stuck}, lost {decision.lost}"
```

The gold answer is a noul and not an action, and the test takes either `stuck` or `lost` above 0.5. That hedge is doing real work, because the two questions genuinely overlap here. Is the far bank missing from this state, which is `lost`, or has the brief become impossible, which is `stuck`? Both readings are defensible and the two clean passes land on different ones: Jev on `lost` 0.80 with `stuck` 0.49, gemini-2.5-flash on `stuck` 1.00 with `lost` 0.00. Because no assertion is made about the action, `move_N` is neither right nor wrong, which is worth remembering when reading the tally below. The other honest caveat is that the ring is an artefact. Real terrain rarely encloses a settler completely, and the closed wall exists so that "there is no way through" can be stated at all.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `move_N` > `wait` x3 | - | 0.53 | 0.02 | 0.49 | 0.80 | 0.04 | 0 |
| gpt-5-nano | 0% | `stop_going` > `move_E`, `move_E` > `move_S`, `move_N` > `move_E` | - | 0.35 | 0.10 | 0.18 | 0.13 | 0.32 | 0 |
| qwen3.7-flash | 0% | `move_N` > `move_NE` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `move_N` > `move_NE` x2, `move_N` > `step_towards:tree_1` | - | 0.50 | 0.00 | 0.00 | 0.00 | 0.07 | 0 |
| llama-3.1-8b | 0% | `move_N` > `move_NE` x2, `error` | - | 0.45 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |
| gpt-oss-20b | 0% | `move_N` > `move_NE` x2, `move_N` > `move_E` | - | 0.47 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 0% | `move_S` > `move_SE` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `move_N` > `move_NW` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 0% | `move_N` > `move_NE` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 0% | `step_towards:tree_1` > `move_N` x2, `step_towards:tree_1` > `move_S` | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `stop_going` > `wait` x3 | - | 0.80 | 0.00 | 1.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `move_E` > `step_towards:tree_1` x2, `move_E` > `move_W` | - | 0.67 | 0.00 | 0.00 | 0.00 | 0.33 | 0 |
| gpt-4.1-nano-vote | 0% | `move_N` > `move_S`, `move_N` > `move_NW`, `move_N` > `step_towards:tree_1` | - | 0.60 | 0.13 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `move_N` > `move_NW` x2, `move_N` > `move_NE` | - | 0.40 | 0.00 | 0.14 | 0.97 | 0.05 | 0 |
| qwen3.7-flash-vote | 0% | `step_towards:tree_1` > `move_E`, `move_E` > `move_S`, `move_N` > `step_towards:tree_1` | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 0% | `move_N` > `move_NE` x3 | - | 0.47 | 0.00 | 0.08 | 0.05 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `move_N` | 31 |
| `move_E` | 5 |
| `stop_going` | 4 |
| `step_towards:tree_1` | 4 |
| `move_S` | 3 |
| `error` | 1 |

## Notes

Three backends pass: Jev, gemini-2.5-flash, and gpt-4.1-nano under logprob at `lost` 0.97. This is the hardest of the eleven. The failure is uniform and stark: eleven of the sixteen backends report both `stuck` and `lost` at exactly 0.00 while stepping somewhere, with `move_N` taking 31 of the 48 top-1 picks. They answer "is this impossible" from the current tile, find walkable ground to the north, say no, and never look at `so_far` at all. Elicitation is the one lever that moved anything, and it moved it a long way: gpt-4.1-nano reports `lost` 0.00 in json and 0.00 under vote, then 0.97 under logprob, the largest single swing in these eleven files. Reading a yes or no off the token probability extracted a judgement that asking for a number did not. Jev is the only backend that gets there unaided, and the only one whose `lost` behaves like a scale: 0.80 here against 0.06 in case 25 and 0.14 in case 30. Across the full run its `lost` AUC is 1.00 where most chat backends sit at 0.50, meaning they never move the number at all. Note that Jev still picks `move_N`: it keeps trying while reporting the impasse, which is what the stint wants, since the report goes back to the planner after two consecutive ticks above 0.6.
