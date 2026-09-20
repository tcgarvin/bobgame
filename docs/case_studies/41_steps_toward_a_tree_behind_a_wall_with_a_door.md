# 41. Steps toward a tree behind a wall with a door

A wall line stands between the actor and the tree, with one door in it. There is a way through, so this is ordinary walking, not lost.

*Test*: `evals/test_jev_situations.py::test_steps_toward_a_tree_behind_a_wall_with_a_door`

## The situation

A nine-tile wall line stands two tiles east of ada, running north to south, with a single door in it directly east. The tree the brief names is five tiles east, on the far side of that wall. ada carries an axe, is at full health with an empty pack, and there is no one else in view. The brief says go through the door and chop tree_1 for wood, with success at 4 wood. The options are the walk toward the tree, the eight compass steps, `wait` and the two canned lines.

## Why this is a good test

Walls block everyone and doors let settlers through, so the line of `#` glyphs on the map is exactly the sort of thing that could read as "no route" and set `lost`. The test asks for two things: an eastward move, and `lost` below 0.45. The tempting wrong answers are any step that goes around rather than through, and a high `lost`. That second one is what costs a run: a settler that decides it is lost ends the stint and hands back to the planner with a report telling it to name the target or move closer, when all the settler had to do was walk through a door it can see.

## What the model saw

- **Instruction**: Go through the door and chop tree_1 for wood.
- **Success condition**: you are carrying 4 wood

**The map Jev was shown** (17x17, `@` is the settler):

```text
.................
.................
.................
.................
..........#......
..........#......
..........#......
..........#......
........@.+..T...
..........#......
..........#......
..........#......
..........#......
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
    "instruction": "Go through the door and chop tree_1 for wood.",
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
  "nearby": [
    {
      "id": "door_1",
      "type": "door",
      "dx": 2,
      "dy": 0
    },
    {
      "id": "tree_1",
      "type": "tree",
      "dx": 5,
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
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 5 dy 0, 5 tiles away |
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

**Gold answer**: step_towards:tree_1 or move_E, lost below 0.45

The assertions the test makes:

```python
assert step_to("tree_1") in criteria, f"no walk to the tree in {sorted(criteria)}"
assert (
    step_to("tree_1") in top_keys(decision) or decision.action == "move_E"
), f"top two {top_keys(decision)}"
assert decision.lost < LOST_LOW, f"lost was {decision.lost}"
```

Two options are equally correct and the assertion says so with an `or`. `step_towards:tree_1` is the code-owned walk and `move_E` is the same tile this tick. Jev flipped between them across repeats, taking `move_E` first in one and `step_towards:tree_1` first in the other two. Both are right, and the flip is a tie in the distribution between two keys with the same effect, not a change of mind. It is worth naming because the consistency statistic counts it as one.

Across the whole matrix the split is 31 `step_towards:tree_1` to 17 `move_E`, so the two-way tie is not peculiar to Jev.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `move_E` > `step_towards:tree_1`, `step_towards:tree_1` > `move_E` x2 | - | 0.49 | 0.02 | 0.18 | 0.05 | 0.03 | 0 |
| gpt-5-nano | 100% | `move_E` > `step_towards:tree_1` x2, `step_towards:tree_1` > `move_E` | - | 0.47 | 0.07 | 0.07 | 0.05 | 0.12 | 0 |
| qwen3.7-flash | 100% | `move_E` > `step_towards:tree_1` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:tree_1` > `move_E` x3 | - | 0.45 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `step_towards:tree_1` > `move_N` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `move_E` > `step_towards:tree_1` x2, `step_towards:tree_1` > `move_E` | - | 0.82 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:tree_1` > `move_E` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:tree_1` > `move_E` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `step_towards:tree_1` > `move_E` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `move_E` > `step_towards:tree_1` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `move_E` > `step_towards:tree_1` x3 | - | 0.50 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:tree_1` x3 | - | 1.00 | 0.13 | 0.00 | 0.00 | 0.40 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `step_towards:tree_1` > `move_E` x3 | - | 0.60 | 0.06 | 0.00 | 0.16 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:tree_1`, `step_towards:tree_1` > `move_E` x2 | - | 0.87 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `move_E` > `step_towards:tree_1` x3 | - | 0.41 | 0.00 | 0.01 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:tree_1` | 31 |
| `move_E` | 17 |

## Notes

Every backend passed every repeat, including llama-3.1-8b, which errors elsewhere in this block. No backend set `lost` anywhere near the gate: the highest is gpt-4.1-nano-logprob at 0.16, and Jev sat at 0.05. Nobody stepped away from the door.

- There is no failure mode to report, which makes this a floor rather than a discriminator. What it does establish is worth establishing: a wall of `#` on the map does not make models declare the brief impossible, and the `+` is read as a way through.
- Jev's confidence of 0.49 is its lowest in this block, and that is the right shape for a two-way tie between actions that do the same thing. The mass is split, not uncertain. A confidence column read on its own would make this look like Jev's weakest answer of the eleven, and it is one of its cleanest.
