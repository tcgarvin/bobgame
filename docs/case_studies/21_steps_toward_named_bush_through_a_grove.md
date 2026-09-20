# 21. Steps toward named bush through a grove

The recorded starvation: food 0, a berry bush six tiles off, trees all round.  The brief names the bush by id, so a step option toward it must exist even though every nearer object is a tree, and Jev should take it.

*Test*: `evals/test_jev_judgement.py::test_steps_toward_named_bush_through_a_grove`

## The situation

Dov is at food 0 and 12 health of 20, losing a health every four ticks, carrying nine wood and wielding nothing. He is standing inside a grove: seventeen trees fill the view, several of them two tiles away. A bush with one berry on it sits six tiles due south, named `bush_1` in the brief. The brief says "Pick berries from bush_1 and eat them until your food is above 40", with a note that reads "You are at food 0 and losing health", and forty ticks.

## Why this is a good test

This is a recorded starvation. Under the old option builder the walk options were a shared top six, every one of them a tree, and the bush the brief named had no option at all, so a settler starved inside a grove. Two things are on trial. The first assertion checks the harness: a step toward the named bush must exist. The second checks the model: it has to take that step over targets that are nearer, more numerous, and the kind of thing a woodcutter usually walks toward. `step_towards:tree_0` is two tiles away, and `craft:plank` is affordable with nine wood in the pack. A failure here is death.

## What the model saw

- **Instruction**: Pick berries from bush_1 and eat them until your food is above 40.
- **Success condition**: your food is above 40
- **Notes**: You are at food 0 and losing health.

**The map Jev was shown** (17x17, `@` is the settler):

```text
.................
.................
.................
...T.....T.......
............T....
...T....T.TT.....
........TT.......
.................
........@..T.....
....T.....T.T....
.........T.......
.......T.........
.......T.........
.....T...........
........B........
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
    "instruction": "Pick berries from bush_1 and eat them until your food is above 40.",
    "success_condition": "your food is above 40"
  },
  "self": {
    "name": "dov",
    "position": [
      100,
      100
    ],
    "health": "12/20",
    "food": "0/100",
    "fatigue": "0/100 (fresh)",
    "asleep": false,
    "wielded": "",
    "inventory": {
      "wood": 9
    },
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
      "id": "tree_0",
      "type": "tree",
      "dx": 0,
      "dy": -2,
      "remaining": 4,
      "yields": "wood"
    },
    {
      "id": "bush_1",
      "type": "bush",
      "dx": 0,
      "dy": 6,
      "berry": true
    }
  ],
  "entities": [],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "notes": "You are at food 0 and losing health."
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `step_towards:bush_1` | one step toward bush_1 (bush) at dx 0 dy 6, 6 tiles away |
| `craft:plank` | craft plank using 1 wood, 2 of them |
| `craft:chest` | craft chest using 6 wood |
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
| `step_towards:tree_0` | one step toward tree_0 (tree) at dx 0 dy -2, 2 tiles away |
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 1 dy -2, 2 tiles away |

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
assert towards_bush, f"no step option towards the bush in {sorted(criteria)}"
assert decision.action in towards_bush, f"chose {decision.action}"
assert decision.lost < LOST_LOW, f"lost was {decision.lost}"
```

Nothing is ambiguous about the action. The live number is `lost`, asserted below 0.45: the bush is six tiles away and offered, which is ordinary work, but a model that reads "food 0 and losing health" as an emergency could let `lost` drift upward and it would not be crazy. The other open number is `danger`, which is not scored. Dov is at 12 health with no wolf in view, losing one health every four ticks, so death is roughly forty-eight ticks away rather than "the next few ticks". The column runs the full 0.00 to 1.00. I would call the high answers wrong on the wording and sympathetic in spirit.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `step_towards:bush_1` > `move_N` x3 | - | 0.85 | 0.02 | 0.23 | 0.05 | 0.46 | 0 |
| gpt-5-nano | 100% | `step_towards:bush_1` > `wait` x2, `step_towards:bush_1` > `move_S` | - | 0.55 | 0.00 | 0.20 | 0.07 | 0.53 | 0 |
| qwen3.7-flash | 100% | `step_towards:bush_1` > `move_S` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| mistral-nemo | 100% | `step_towards:bush_1` > `wait` x3 | - | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `step_towards:bush_1` > `move_N` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.50 | 0 |
| gpt-oss-20b | 100% | `step_towards:bush_1` > `wait` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.03 | 0 |
| gemini-2.5-flash-lite | 100% | `step_towards:bush_1` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano | 100% | `step_towards:bush_1` > `wait` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.27 | 0 |
| gpt-oss-120b | 100% | `step_towards:bush_1` > `step_towards:tree_0` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.10 | 0 |
| gpt-4.1-mini | 100% | `step_towards:bush_1` > `wait` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gemini-2.5-flash | 100% | `step_towards:bush_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-5-nano-vote | 100% | `step_towards:bush_1` x3 | - | 1.00 | 0.60 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano-vote | 100% | `step_towards:bush_1` x3 | - | 1.00 | 0.20 | 0.00 | 0.00 | 0.93 | 0 |
| gpt-4.1-nano-logprob | 0% | `step_towards:bush_1` > `move_S`, `step_towards:bush_1` > `wait` x2 | - | 0.60 | 0.00 | 0.06 | 0.97 | 1.00 | 0 |
| qwen3.7-flash-vote | 100% | `step_towards:bush_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| qwen3.7-flash-logprob | 100% | `step_towards:bush_1` > `move_S` x3 | - | 0.95 | 0.00 | 0.06 | 0.00 | 0.98 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `step_towards:bush_1` | 48 |

## Notes

Fifteen of sixteen backends pass at 100%, and all 48 top-1 picks are the bush. The single failure is gpt-4.1-nano-logprob, which chooses the right action and writes lost 0.97 at the same time. In a run that stint would end with reason `lost` one tick after correctly setting off toward the food.

- That is the sharpest illustration in this set of why the action and the numbers are scored apart, and of what logprob reading costs. The same model under json elicitation writes lost 0.00 and passes. Nothing about its behaviour changed; only how the probability was extracted.
- Jev takes the bush at confidence 0.85 with lost 0.05 and danger 0.46. It is the only backend that puts `danger` between the extremes on a settler that is genuinely dying, just slowly. Everyone else answers 0.00, 0.50, 0.90 or 1.00.
