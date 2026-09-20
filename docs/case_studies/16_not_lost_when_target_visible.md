# 16. Not lost when target visible

The same brief with reeds five tiles away is ordinary work, not lost.

*Test*: `evals/test_jev_judgement.py::test_not_lost_when_target_visible`

## The situation

The same settler with the same brief: "Walk to the river and collect fiber from reeds", success at two fiber, forty ticks. The only change to the world is that a patch of reeds now stands five tiles due east, drawn on the map as `r` and listed in `nearby` with three units of fiber left. There is still no river on the map. A `step_towards:reeds_1` option is on offer alongside the compass.

## Why this is a good test

This is the matched negative for case 15, and it checks that a model does not cry lost simply because the brief mentions a river the map does not show. The reeds are what the success condition actually needs, and they are in plain sight. The failure it guards against is the mirror image of case 15: stints that end honestly-started work early, and a planner that gets told the site has no reeds when it plainly does.

## What the model saw

- **Instruction**: Walk to the river and collect fiber from reeds
- **Success condition**: you are carrying 2 fiber

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
........@....r...
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
    "instruction": "Walk to the river and collect fiber from reeds",
    "success_condition": "you are carrying 2 fiber"
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
      "id": "reeds_1",
      "type": "reeds",
      "dx": 5,
      "dy": 0,
      "remaining": 3,
      "yields": "fiber"
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
| `step_towards:reeds_1` | one step toward reeds_1 (reeds) at dx 5 dy 0, 5 tiles away |

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
assert towards_reeds, f"no step option towards the reeds in {sorted(criteria)}"
assert decision.lost < LOST_LOW, f"lost was {decision.lost}"
assert decision.action in towards_reeds | {KEEP_GOING}, f"chose {decision.action}"
```

Nobody found this hard, so the ambiguity is theoretical rather than live. It is worth naming anyway: the brief says "walk to the river", and a strict reading of the `lost` wording, "a target that is not on the map", would justify a high answer, because the river is still nowhere. Every backend instead resolved the brief to its success condition. That is the convention this scenario quietly establishes, and it is the right one, but it is not stated anywhere in the question text.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.92 | 0.02 | 0.14 | 0.11 | 0.02 | 0 |
| gpt-5-nano | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.55 | 0.02 | 0.13 | 0.05 | 0.30 | 0 |
| qwen3.7-flash | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.85 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.68 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `step_towards:reeds_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:reeds_1` x3 | - | 1.00 | 0.53 | 0.00 | 0.00 | 0.60 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:reeds_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.60 | 0.24 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:reeds_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:reeds_1` > `move_E` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:reeds_1` | 48 |

## Notes

A sixteen-way tie at 100%, with all 48 top-1 picks going to `step_towards:reeds_1`. As a discriminator it is worthless; as a control for case 15 it is the most useful row in the pair. The same words, the same models, and eleven backends flip from wrong to right the moment one `r` appears on the map. That means their failures in case 15 were not a misreading of the question. They were a failure to notice an absence, which is a much harder thing to fix with prompt wording.

- Jev reads lost 0.11 here against 0.78 there, the separation the pair was built to measure.
- The confidence column is the only spread left, and it is a calibration observation rather than an accuracy one: the vote backends all write 1.00, Jev writes 0.92, gpt-4.1-nano writes 0.60, on a question nobody got wrong.
- gpt-5-nano-vote writes done 0.53 for a settler with an empty pack against a condition of two fiber.
