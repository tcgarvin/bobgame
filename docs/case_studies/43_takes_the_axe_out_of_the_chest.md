# 43. Takes the axe out of the chest

An axe is in the chest beside you and your hands are empty: withdraw is the whole brief, and `deposit:` of your own wood is the near-miss beside it.

*Test*: `evals/test_jev_situations.py::test_takes_the_axe_out_of_the_chest`

## The situation

A chest one tile east holds one axe and three stone, and the state prints its contents. ada's hands are empty, its pack has two wood in it, and a tree with 4 wood left stands one tile west. The brief says take an axe out of chest_1 and counts success as carrying an axe, with 20 ticks to do it in. The option list offers the withdraw of the axe, the withdraw of the stone, a deposit of ada's own wood into the same chest, chopping the tree, crafting a plank from the wood, and the usual moves.

## Why this is a good test

Every near-miss here is in the same family as the right answer. `deposit:chest_1:wood` is the same verb family at the same chest in the wrong direction, `withdraw:chest_1:stone` is the right verb at the right chest with the wrong noun, and `craft:plank` consumes the wood the settler is already carrying. A model that matches on "chest" and "wood" without reading which way the items move has three plausible ways to be wrong. The failure in a run is small but constant: a settler that deposits instead of withdrawing loses its own wood and still has no axe, and the planner's next brief starts from a worse position than it thought.

## What the model saw

- **Instruction**: Take an axe out of chest_1.
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
.......T@C.......
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
    "instruction": "Take an axe out of chest_1.",
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
      "wood": 2
    },
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 20,
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
      "id": "chest_1",
      "type": "chest",
      "dx": 1,
      "dy": 0,
      "contents": {
        "axe": 1,
        "stone": 3
      }
    },
    {
      "id": "tree_1",
      "type": "tree",
      "dx": -1,
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
| `deposit:chest_1:wood` | put wood from your pack into chest chest_1 |
| `withdraw:chest_1:axe` | take axe out of chest chest_1 (1 inside) |
| `withdraw:chest_1:stone` | take stone out of chest chest_1 (3 inside) |
| `extract:tree_1` | harvest wood from the tree at dx -1 dy 0, 4 units left (1 work per action bare-handed, 3 work per unit) |
| `craft:plank` | craft plank using 1 wood, 2 of them |
| `move_N` | step one tile N onto known ground |
| `move_NE` | step one tile NE onto known ground |
| `move_E` | step one tile E onto known ground |
| `move_SE` | step one tile SE onto known ground |
| `move_S` | step one tile S onto known ground |
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

**Gold answer**: withdraw:chest_1:axe

The assertions the test makes:

```python
assert "withdraw:chest_1:axe" in criteria, f"no withdraw in {sorted(criteria)}"
assert decision.action == "withdraw:chest_1:axe", f"chose {decision.action}"
```

There is no ambiguity in the action. The assertion is exact, and the gold is a fact about the option list rather than a convention.

The thing to watch is `done`. The settler is not carrying an axe, so `done` must be low, but the state shows an axe one tile away inside an open container. Confusing "an axe is here" with "I have an axe" is the whole difficulty of the scenario, and it is a difficulty about the noul, not about the choice.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `withdraw:chest_1:axe` > `move_E` x3 | - | 0.99 | 0.04 | 0.10 | 0.03 | 0.02 | 0 |
| gpt-5-nano | 100% | `withdraw:chest_1:axe` > `extract:tree_1`, `withdraw:chest_1:axe` > `move_E` x2 | - | 0.70 | 0.00 | 0.03 | 0.03 | 0.00 | 0 |
| qwen3.7-flash | 100% | `withdraw:chest_1:axe` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `withdraw:chest_1:axe` > `move_E` x3 | - | 0.88 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `withdraw:chest_1:axe` > `move_E` x3 | - | 0.79 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `withdraw:chest_1:axe` > `wait` x3 | - | 0.96 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `withdraw:chest_1:axe` > `move_E` x3 | - | 0.90 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `withdraw:chest_1:axe` > `move_N` x3 | - | 0.78 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `withdraw:chest_1:axe` > `move_E` x3 | - | 0.85 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `withdraw:chest_1:axe` > `wait` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `withdraw:chest_1:axe` > `wait` x3 | - | 0.97 | 0.33 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `withdraw:chest_1:axe` x3 | - | 1.00 | 0.13 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-vote | 100% | `withdraw:chest_1:axe` x3 | - | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `withdraw:chest_1:axe` > `move_N` x3 | - | 0.78 | 0.01 | 0.00 | 0.01 | 0.22 | 0 |
| qwen3.7-flash-vote | 100% | `withdraw:chest_1:axe` x3 | - | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `withdraw:chest_1:axe` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `withdraw:chest_1:axe` | 48 |

## Notes

Every backend chose `withdraw:chest_1:axe` in every repeat, 48 of 48. On the action there is nothing to separate them.

- `done` separates them completely. gemini-2.5-flash-lite wrote 1.00, gpt-4.1-nano-vote 1.00 and qwen3.7-flash-vote 1.00, with gemini-2.5-flash at 0.33, all while the pack held two wood and no axe. Under Jev's gate, three of those backends would end the stint on the tick before the withdraw and tell the planner its settler is armed.
- It is the same confusion in every case: an axe listed in the nearby chest read as an axe in hand. Two of the four inflators are vote variants, continuing the pattern from case studies 38 and 39.
- Jev wrote `done` 0.04 at confidence 0.99, and its `done` column across the whole suite is why it can be trusted to end a stint: AUC 1.00 and 100% separation at its own threshold in the matrix analysis. This scenario is the easiest place to see the difference between choosing right and knowing when you are finished. Everyone chose right and a quarter of the field thought the job was already done.
