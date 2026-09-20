# 32. Goes for food at zero with no berries in the pack

Food 0 and health 6 with a berry bush four tiles away: the stone brief is not worth dying for, and there is no `eat:` option to take the easy way.

*Test*: `evals/test_jev_situations.py::test_goes_for_food_at_zero_with_no_berries_in_the_pack`

## The situation

Dov stands at the site with food at 0 and health at 6 of 20, a pickaxe in hand and an empty pack. A rock holding two stone is one tile east; a bush with a berry on it is four tiles east. The brief says to gather stone from the rocks here and is finished at four stone, with forty ticks available. There is nothing edible in the pack, so there is no `eat:` option: eating means walking, as `step_towards:bush_1`.

## Why this is a good test

This is a survival trade-off where both sides cost something. At food 0 the settler loses one health every four ticks, so six health is roughly twenty-four ticks of life against a brief with forty. The cheap action and the right action point in opposite directions: the stone is adjacent and the food is four steps away, and every tick spent walking is a tick not mining. A failure is a settler that mines until it starves beside a rock, which the first seven-day run produced for real, and which is a large part of why this scenario set exists.

## What the model saw

- **Instruction**: Gather stone from the rocks here.
- **Success condition**: you are carrying 4 stone

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
........@o..B....
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
    "instruction": "Gather stone from the rocks here.",
    "success_condition": "you are carrying 4 stone"
  },
  "self": {
    "name": "dov",
    "position": [
      100,
      100
    ],
    "health": "6/20",
    "food": "0/100",
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
      "id": "rock_1",
      "type": "rock_medium",
      "dx": 1,
      "dy": 0,
      "remaining": 2,
      "yields": "stone"
    },
    {
      "id": "bush_1",
      "type": "bush",
      "dx": 4,
      "dy": 0,
      "berry": true
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
| `extract:rock_1` | harvest stone from the rock_medium at dx 1 dy 0, 2 units left (3 work per action with the pickaxe, 3 work per unit) |
| `move_N` | step one tile N onto known ground |
| `move_S` | step one tile S onto known ground |
| `move_SW` | step one tile SW onto known ground |
| `move_W` | step one tile W onto known ground |
| `move_NW` | step one tile NW onto known ground |
| `say:come_here` | say "Come to me." out loud to anyone nearby |
| `say:all_good` | say "All good here." out loud to anyone nearby |
| `step_towards:bush_1` | one step toward bush_1 (bush) at dx 4 dy 0, 4 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: step_towards:bush_1

The assertions the test makes:

```python
assert step_to("bush_1") in criteria, f"no walk to the bush in {sorted(criteria)}"
assert step_to("bush_1") in top_keys(decision), f"top two {top_keys(decision)}"
```

The gold answer is `step_towards:bush_1` in the top two, and the test deliberately allows the rock to sit above it. That matters more here than anywhere else in this batch: Jev's top-1 is `extract:rock_1` on all three repeats, and it passes on second place. So the scenario does not really ask "eat now", it asks "is the bush on your mind at all". For a real settler that is a warning rather than a pass. Jev takes the stone this tick, and next tick it faces the same state with a quarter less health, and nothing in the mechanism guarantees the second-ranked option ever comes up. A stricter version would require the walk as top-1, and Jev would fail it. The softer bar is defensible on the arithmetic, one more extract costs one tick and 0.25 health, but it should be read as a near miss. One detail no backend appears to have used: the rock has two stone remaining against a brief asking for four, so this brief cannot be finished from this tile either way.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:rock_1` > `step_towards:bush_1` x3 | - | 0.94 | 0.02 | 0.24 | 0.07 | 0.39 | 0 |
| gpt-5-nano | 33% | `extract:rock_1` > `wait`, `extract:rock_1` > `step_towards:bush_1`, `extract:rock_1` > `move_N` | - | 0.64 | 0.00 | 0.18 | 0.11 | 0.32 | 0 |
| qwen3.7-flash | 100% | `extract:rock_1` > `step_towards:bush_1` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.67 | 0 |
| mistral-nemo | 100% | `extract:rock_1` > `step_towards:bush_1` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.40 | 0 |
| llama-3.1-8b | 0% | `extract:rock_1` > `move_S` x2, `error` | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |
| gpt-oss-20b | 0% | `extract:rock_1` > `wait` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `extract:rock_1` > `step_towards:bush_1` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `extract:rock_1` > `move_W` x3 | - | 0.67 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `extract:rock_1` > `step_towards:bush_1` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.30 | 0 |
| gpt-4.1-mini | 0% | `extract:rock_1` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 0% | `extract:rock_1` > `wait` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-5-nano-vote | 0% | `extract:rock_1` x3 | - | 1.00 | 0.07 | 0.00 | 0.00 | 0.87 | 0 |
| gpt-4.1-nano-vote | 0% | `extract:rock_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.13 | 0 |
| gpt-4.1-nano-logprob | 0% | `extract:rock_1` > `move_W` x3 | - | 0.67 | 0.10 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 0% | `extract:rock_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| qwen3.7-flash-logprob | 67% | `extract:rock_1` > `step_towards:bush_1` x2, `error` | - | 0.95 | 0.00 | 0.04 | 0.00 | 0.87 | 1 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:rock_1` | 46 |
| `error` | 2 |

## Notes

Five backends reach 100%: Jev, qwen3.7-flash, mistral-nemo, gemini-2.5-flash-lite and gpt-oss-120b, all by the same route, `extract:rock_1` first and `step_towards:bush_1` second. Every backend that produced a row picked `extract:rock_1` as its top action, 46 of 48, so the whole scenario separates on the second-ranked option. The failures are models whose second slot goes to `wait` or a move: gpt-oss-20b, gpt-4.1-mini and gemini-2.5-flash all rank `wait` above the walk to food, meaning hunger never entered their ranking. Vote elicitation fails here by construction rather than by judgement: the vote backends return a single winning action, not a ranked distribution, so `extract:rock_1` three times leaves no second place to inspect and all three vote variants score 0%. That is a limitation of the elicitation method for this kind of test, and it is worth separating from a model being wrong. Jev's informative number is `danger` at 0.39. Starving to death over twenty-four ticks is not dying within the next few, and 0.39 is a fair reading of a settler at 6 health with no food. Compare gemini-2.5-flash at 1.00 and gpt-oss-20b at 0.00 on the identical state: the chat models are at the rails, Jev is somewhere on a line.
