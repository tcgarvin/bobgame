# 26. Planks do not satisfy a wood count

Five planks against "carry 5 wood": the right number, the wrong item, and the planks were made out of the wood so there is none left.

*Test*: `evals/test_jev_situations.py::test_planks_do_not_satisfy_a_wood_count`

## The situation

Ada carries five planks and nothing else, with an axe in hand and a tree holding four wood one tile east. The brief says to gather wood from the trees here and is finished when the pack holds five wood. The inventory reads `plank: 5`. Two plank recipes are on the option list, a wood wall and a wood floor, alongside the extract.

## Why this is a good test

This isolates whether `done` is answered by matching a number or by reading an item name. The count in the pack is exactly the count in the success condition, sitting next to a word that begins the same way, and the physics make the shortcut worse rather than better: planks are crafted out of wood, so five planks means the wood is already spent and the pack holds none. If `done` went high the stint would end within two ticks and the planner would be told a job was finished that has not started. The entire value of the `done` noul is that it does not lie about this.

## What the model saw

- **Instruction**: Gather wood from the trees here.
- **Success condition**: you are carrying 5 wood

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
    "instruction": "Gather wood from the trees here.",
    "success_condition": "you are carrying 5 wood"
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
      "plank": 5
    },
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
| `extract:tree_1` | harvest wood from the tree at dx 1 dy 0, 4 units left (3 work per action with the axe, 3 work per unit) |
| `craft:wood_wall` | craft wood_wall using 2 plank |
| `craft:wood_floor` | craft wood_floor using 1 plank, 2 of them |
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

**Gold answer**: done below 0.3

The assertions the test makes:

```python
assert decision.done < DONE_LOW, f"done was {decision.done}"
```

The action is uncontroversial and every backend took it, so the case rests on the number. There is one sympathetic reading of a high `done`: the brief's intent is arguably "have enough wood-stuff for what comes next", and five planks satisfy that. The test refuses the reading, and it should. The success condition is a literal inventory count, and the stint contract only works if a count means a count. The one defensible alternative action is `craft:wood_wall`, which a planner that wanted a wall would take, but this brief asks for wood. This is the negative half of a pair with case 13, done when inventory met, and should be read next to it.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:tree_1` > `move_SW`, `extract:tree_1` > `wait` x2 | - | 0.99 | 0.08 | 0.39 | 0.18 | 0.03 | 0 |
| gpt-5-nano | 100% | `extract:tree_1` > `wait` x2, `extract:tree_1` > `move_W` | - | 0.60 | 0.00 | 0.15 | 0.08 | 0.00 | 0 |
| qwen3.7-flash | 100% | `extract:tree_1` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `extract:tree_1` > `wait` x2, `extract:tree_1` > `move_N` | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `extract:tree_1` > `move_S` x3 | - | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `extract:tree_1` > `wait` x2, `extract:tree_1` > `craft:wood_floor` | - | 0.84 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `extract:tree_1` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `extract:tree_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `extract:tree_1` > `move_W` x3 | - | 0.90 | 0.00 | 0.10 | 0.00 | 0.05 | 0 |
| gpt-4.1-mini | 100% | `extract:tree_1` > `wait` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `extract:tree_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 67% | `extract:tree_1` x3 | - | 1.00 | 0.20 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-vote | 100% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `extract:tree_1` > `move_W` x3 | - | 0.60 | 0.10 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 67% | `extract:tree_1` x2, `error` | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |
| qwen3.7-flash-logprob | 100% | `extract:tree_1` > `wait` x3 | - | 0.95 | 0.01 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:tree_1` | 47 |
| `error` | 1 |

## Notes

Everything passes on the action and nearly everything on the number, so the discrimination is in how the low answer was produced. Jev writes `done` 0.08. Most chat models write 0.00, which passes and tells you nothing, because the run-level deciles show several of them writing 0.00 on essentially every tick: gpt-4.1-nano put all 132 of its `done` answers in the lowest decile and gemini-2.5-flash put 125 of 132 there. A model that cannot raise the number anywhere has not discriminated here, it has abstained. Two rows are not clean passes: qwen3.7-flash under vote errored on one repeat and scores 67%, and gpt-5-nano under vote wrote 0.20 on one repeat. The Jev detail worth keeping is its `stuck` of 0.39, the highest of any backend on this state. With the right number of the wrong item in the pack it is mildly unsure whether the brief still makes sense, which is a more informative answer than a flat zero even though nothing in the test rewards it.
