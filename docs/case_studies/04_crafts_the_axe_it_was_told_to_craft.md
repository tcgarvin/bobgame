# 04. Crafts the axe it was told to craft



*Test*: `evals/test_jev_basics.py::test_crafts_the_axe_it_was_told_to_craft`

## The situation

Ada carries 2 wood and 1 stone, wields nothing, and stands on open grass. A tree sits three tiles east. The brief says to craft an axe from the wood and stone she carries, and counts the job done when she holds an axe. The option list offers `craft:axe` at exactly 2 wood plus 1 stone, `craft:plank`, the eight moves, a step toward the tree, `wait` and two `say` lines. She has 10 ticks.

## Why this is a good test

The judgement is whether the model checks the pack against the recipe instead of assuming more gathering is needed. The tree is the trap: it is the only feature on the map, chopping wood is what a settler does, and `step_towards:tree_1` reads like progress. `craft:plank` is a second trap, a legal craft that spends the wood the axe needs. A settler that walks to the tree spends its 10 ticks arriving and chopping, ends the stint with more wood and no axe, and the planner has to work out that the materials were in the pack the whole time.

## What the model saw

- **Instruction**: Craft an axe from the wood and stone you carry.
- **Success condition**: you are carrying an axe

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
........@..T.....
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
    "instruction": "Craft an axe from the wood and stone you carry.",
    "success_condition": "you are carrying an axe"
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
      "stone": 1,
      "wood": 2
    },
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 10,
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
| `craft:axe` | craft axe using 2 wood + 1 stone |
| `craft:plank` | craft plank using 1 wood, 2 of them |
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
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 3 dy 0, 3 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: `craft:axe`

The assertions the test makes:

```python
assert decision.action == "craft:axe", f"chose {decision.action}"
```

Nothing else is defensible. The recipe line states the cost, the pack states the contents, and they match exactly. The only wrinkle is that `craft:plank` would also be a legal use of the tick, and a settler that crafted a plank would still be "making something", which is how a weak model can talk itself into the wrong key. `done` should be low: an axe is the success condition and there is no axe.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `craft:axe` > `step_towards:tree_1` x3 | 3/3 | 0.95 | 0.04 | 0.24 | 0.15 | 0.03 | 0 |
| gpt-5-nano | 100% | `craft:axe` > `move_E` x3 | 3/3 | 0.57 | 0.08 | 0.08 | 0.07 | 0.07 | 0 |
| qwen3.7-flash | 100% | `craft:axe` > `wait` x3 | 3/3 | 0.95 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 0% | `step_towards:tree_1` > `craft:axe` x3 | 0/3 | 0.43 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `craft:axe` > `move_E` x3 | 3/3 | 0.71 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `craft:axe` > `step_towards:tree_1` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `craft:axe` > `step_towards:tree_1` x3 | 3/3 | 0.74 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `craft:axe` > `move_E` x3 | 3/3 | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `craft:axe` > `wait` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `craft:axe` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `craft:axe` > `wait` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `craft:axe` x3 | 3/3 | 1.00 | 0.13 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-4.1-nano-vote | 0% | `step_towards:tree_1`, `step_towards:tree_1` > `craft:axe` x2 | 0/3 | 0.87 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `craft:axe` > `move_E` x3 | 3/3 | 0.60 | 0.00 | 0.00 | 0.17 | 0.01 | 0 |
| qwen3.7-flash-vote | 100% | `craft:axe` x3 | 3/3 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `craft:axe` > `wait` x3 | 3/3 | 0.95 | 0.04 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `craft:axe` | 42 |
| `step_towards:tree_1` | 6 |

## Notes

Thirteen backends passed on every repeat. Two failed outright: `mistral-nemo` chose `step_towards:tree_1` in all three repeats and `gpt-4.1-nano-vote` chose it in all three as well, the same mistake in both cases, gathering more wood while the recipe is already affordable. Both ranked `craft:axe` second on at least two repeats, which is worth nothing in a run: the stint acts on the top choice only, so a correct second place still walks the settler east. The more interesting failure is in the noul columns, where `qwen3.7-flash` and `qwen3.7-flash-vote` both wrote `done` 1.00 for a settler with no axe. Two consecutive ticks of that ends the stint with the job unstarted. Jev wrote 0.04.
