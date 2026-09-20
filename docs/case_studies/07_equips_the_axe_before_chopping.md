# 07. Equips the axe before chopping

Carrying an axe, wielding nothing, next to a tree: wield it first.

*Test*: `evals/test_jev_basics.py::test_equips_the_axe_before_chopping`

## The situation

Ada stands next to tree_1, one tile east, with an axe in her pack and nothing in her hands. The brief says to equip the axe and then chop the tree, and counts the job done at 3 wood. Both `equip:axe` and `extract:tree_1` are legal this tick, and the extract option states its own cost: 1 work per action bare-handed against 3 work per unit. She has 30 ticks.

## Why this is a good test

This is the only basics scenario with a real ordering judgement. Chopping bare-handed works, so `extract:tree_1` is not illegal, it is just slow: three ticks per unit of wood instead of one, or nine extra ticks for the three units the brief wants, against one tick spent wielding. The judgement is whether the model reads the work numbers in the option text and spends a tick to save nine. A settler that skips the equip still finishes, which is why this failure is nasty in a run: it looks like success, it just quietly costs a third of the stint.

## What the model saw

- **Instruction**: Equip your axe, then chop tree_1 for wood.
- **Success condition**: you are carrying 3 wood

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
    "instruction": "Equip your axe, then chop tree_1 for wood.",
    "success_condition": "you are carrying 3 wood"
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
    "inventory": {
      "axe": 1
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
| `extract:tree_1` | harvest wood from the tree at dx 1 dy 0, 4 units left (1 work per action bare-handed, 3 work per unit) |
| `equip:axe` | wield the axe you are carrying |
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

**Gold answer**: `equip:axe`

The assertions the test makes:

```python
assert decision.action == "equip:axe", f"chose {decision.action}"
```

`extract:tree_1` is genuinely defensible on a short brief, and a settler that chopped first and never equipped would still hit the success condition within the 30 ticks available. The gold answer is a convention backed by arithmetic rather than a hard rule, and the brief tips the scale by naming the equip step explicitly. That last point matters: if the instruction had only said "chop tree_1", the test would be much weaker. The nouls should all be low, and `done` in particular, since the pack holds an axe and no wood.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `equip:axe` > `extract:tree_1` x3 | 3/3 | 0.73 | 0.02 | 0.20 | 0.04 | 0.03 | 0 |
| gpt-5-nano | 67% | `equip:axe` > `extract:tree_1` x2, `error` | 2/3 | 0.60 | 0.00 | 0.07 | 0.03 | 0.00 | 1 |
| qwen3.7-flash | 100% | `equip:axe` > `extract:tree_1` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `extract:tree_1` > `equip:axe` x3 | 0/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 0% | `extract:tree_1` > `move_S` x3 | 0/3 | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `equip:axe` > `extract:tree_1` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `equip:axe` > `extract:tree_1` x3 | 3/3 | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `equip:axe` > `extract:tree_1` x2, `equip:axe` > `move_W` | 3/3 | 0.40 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `equip:axe` > `extract:tree_1` x3 | 3/3 | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `equip:axe` > `wait` x3 | 3/3 | 0.77 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `equip:axe` > `extract:tree_1` x3 | 3/3 | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `equip:axe` > `extract:tree_1` x2, `equip:axe` | 3/3 | 0.80 | 0.27 | 0.00 | 0.00 | 0.13 | 0 |
| gpt-4.1-nano-vote | 100% | `equip:axe` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `equip:axe` > `extract:tree_1` x2, `equip:axe` > `move_W` | 3/3 | 0.40 | 0.15 | 0.00 | 0.01 | 0.05 | 0 |
| qwen3.7-flash-vote | 100% | `equip:axe` x2, `equip:axe` > `extract:tree_1` | 3/3 | 0.93 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `equip:axe` > `extract:tree_1` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `equip:axe` | 41 |
| `extract:tree_1` | 6 |
| `error` | 1 |

## Notes

This is the first basics scenario that separates the field. `mistral-nemo` and `llama-3.1-8b` failed 0 of 3, both choosing `extract:tree_1`, the same mistake: acting on the goal directly and ignoring the cost line in the option text. `mistral-nemo` at least ranked `equip:axe` second, which buys nothing at run time. `gpt-5-nano` returned one error and so scored 67% on two good repeats; that is a missing row, not a wrong answer. Everything else passed. Jev's confidence was 0.73, its lowest in the basics set, and it put `extract:tree_1` second on every repeat, which is the right shape for a scenario where the loser is defensible and merely slow.
