# 42. Steps toward the named river not the pond it can see

Water three tiles east is not the river the brief named twenty-five tiles west; a named place is a coordinate, not the nearest thing that looks like it.

*Test*: `evals/test_jev_situations.py::test_steps_toward_the_named_river_not_the_pond_it_can_see`

## The situation

ada is empty-handed with an empty pack and full health. Three tiles east there is a visible patch of shallow water. The brief names a place, `river`, at dx -25 dy 0, twenty-five tiles west and far out of view, and says walk to the river and gather fiber from the reeds there, with success at 3 fiber. A tree stands three tiles northeast and has its own step option. The option list carries `step_towards:river`, `step_towards:tree_1`, the eight compass steps, `wait` and the two canned lines.

## Why this is a good test

Briefs carry named places so that Jev never sees an absolute coordinate, and the contract is that a named place is a coordinate, not the nearest thing that looks like it. The pond is visible and close; the river is twenty-five ticks of walking with nothing to show on the way. A settler that goes to the water it can see finds no reeds, gathers no fiber and burns the stint. The scenario also exercises `lost`: the question's wording says a named place with a step option toward it is not missing, however far away it is, so the right answer to `lost` is low even though the target is off the map.

## What the model saw

- **Instruction**: Walk to the river and gather fiber from the reeds there.
- **Success condition**: you are carrying 3 fiber
- **Named places**: `river` (dx -25 dy 0)

**The map Jev was shown** (17x17, `@` is the settler):

```text
.................
.................
.................
.................
.................
...........T.....
.................
...........~~~...
........@..~~~...
...........~~~...
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
    "instruction": "Walk to the river and gather fiber from the reeds there.",
    "success_condition": "you are carrying 3 fiber",
    "places": {
      "river": "dx -25 dy 0"
    }
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
    "ticks_left": 80,
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
| `step_towards:river` | one step toward river at dx -25 dy 0, 25 tiles away |
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

**Gold answer**: step_towards:river

The assertions the test makes:

```python
assert step_to("river") in criteria, f"no walk to the river in {sorted(criteria)}"
assert decision.action == step_to("river"), f"chose {decision.action}"
```

Honestly, there is little room to argue. The water to the east is not reeds and the state never says it is, and `step_towards:tree_1` is plainly off-brief. The temptation is also weaker than the title suggests, because no option names the pond; the pull would have to show up as a bare `move_E`.

The soft part is `lost`. Jev wrote 0.39, its highest `lost` in this block, under the 0.45 low bar but not by much, and the test does not assert on it here. A long walk to a place that is not on the map does read a little like something missing, and that is a fair thing for the number to notice.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:river` > `move_W` x3 | - | 0.87 | 0.02 | 0.31 | 0.39 | 0.03 | 0 |
| gpt-5-nano | 100% | `step_towards:river` > `move_W` x3 | - | 0.62 | 0.02 | 0.08 | 0.07 | 0.08 | 0 |
| qwen3.7-flash | 100% | `step_towards:river` > `move_W` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:river` > `move_E` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `step_towards:river` > `move_N` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `step_towards:river` > `step_towards:tree_1` x3 | - | 0.70 | 0.00 | 0.00 | 0.33 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:river` > `move_W` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:river` > `move_E`, `step_towards:river` > `move_W` x2 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `step_towards:river` > `step_towards:tree_1` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.10 | 0 |
| gpt-4.1-mini | 100% | `step_towards:river` > `move_W` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:river` > `move_W` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:river` x3 | - | 1.00 | 0.27 | 0.00 | 0.00 | 0.07 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:river` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `step_towards:river` > `move_E` x2, `step_towards:river` > `move_W` | - | 0.60 | 0.09 | 0.00 | 0.09 | 0.01 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:river` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:river` > `move_NW`, `step_towards:river` > `move_W`, `step_towards:river` > `move_E` | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:river` | 48 |

## Notes

A clean sweep. All 48 top-1 answers are `step_towards:river`, and no backend put the pond direction or the tree first. Jev answered at 0.87 confidence.

- There is no failure mode in the action column, so nothing separates Jev from the field here. The scenario belongs in the set as the spatial control that shows the `places` mechanism is as legible to a chat model as it is to Jev.
- The only numbers that move are `lost`: gpt-oss-20b wrote 0.33 and Jev 0.39, both under the 0.5 gate and both well above their own baselines. That is the two backends noticing that the target is not on the map, which is the correct thing to notice and the wrong thing to act on.
