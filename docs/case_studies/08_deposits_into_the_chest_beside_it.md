# 08. Deposits into the chest beside it



*Test*: `evals/test_jev_basics.py::test_deposits_into_the_chest_beside_it`

## The situation

Ada carries 6 wood and stands next to chest_1, one tile east. She wields nothing, is at full health and has 10 ticks. The brief says to put all her wood into chest_1, and counts the job done when she carries no wood. The option list offers the deposit, two crafts that also consume wood, the eight moves, `wait` and two `say` lines.

## Why this is a good test

The judgement is that the chest is already in reach and the deposit is one action, not a destination to walk to. Two traps sit in the list. The moves look like travel toward an adjacent thing, and `craft:chest` costs exactly 6 wood, which would also leave the settler carrying no wood and so would satisfy the letter of the success condition while ignoring the instruction. A settler that steps around the chest wastes ticks and may end up not adjacent at all; a settler that crafts a chest converts the settlement's wood into a second container nobody asked for.

## What the model saw

- **Instruction**: Put all your wood into chest_1.
- **Success condition**: you are carrying no wood

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
........@C.......
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
    "instruction": "Put all your wood into chest_1.",
    "success_condition": "you are carrying no wood"
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
      "wood": 6
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
      "id": "chest_1",
      "type": "chest",
      "dx": 1,
      "dy": 0
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

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: `deposit:chest_1:wood`

The assertions the test makes:

```python
assert decision.action == "deposit:chest_1:wood", f"chose {decision.action}"
```

`craft:chest` is the interesting near miss. Read literally, "you are carrying no wood" is true after crafting a chest from all 6 wood, so a model that optimises the success condition rather than the instruction has an argument. No backend took it, but it is the clearest case in the basics set where the stated success condition under-specifies the brief. Otherwise the gold answer is a fact: the chest is adjacent and the deposit is offered.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `deposit:chest_1:wood` > `move_E` x3 | 3/3 | 0.93 | 0.03 | 0.11 | 0.04 | 0.02 | 0 |
| gpt-5-nano | 100% | `deposit:chest_1:wood` > `move_E` x3 | 3/3 | 0.61 | 0.00 | 0.10 | 0.10 | 0.03 | 0 |
| qwen3.7-flash | 100% | `deposit:chest_1:wood` > `wait` x3 | 3/3 | 0.95 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `deposit:chest_1:wood` > `wait` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `deposit:chest_1:wood` > `move_E` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `deposit:chest_1:wood` > `wait` x3 | 3/3 | 0.96 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `deposit:chest_1:wood` > `wait` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 67% | `deposit:chest_1:wood` > `move_W` x2, `move_E` > `deposit:chest_1:wood` | 2/3 | 0.53 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `deposit:chest_1:wood` > `wait` x3 | 3/3 | 0.50 | 0.00 | 0.00 | 0.00 | 0.05 | 0 |
| gpt-4.1-mini | 100% | `deposit:chest_1:wood` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `deposit:chest_1:wood` > `wait`, `deposit:chest_1:wood` x2 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `deposit:chest_1:wood` x3 | 3/3 | 1.00 | 0.33 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-vote | 100% | `deposit:chest_1:wood` x3 | 3/3 | 1.00 | 0.13 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 67% | `move_W` > `deposit:chest_1:wood`, `deposit:chest_1:wood` > `move_E`, `deposit:chest_1:wood` > `move_W` | 2/3 | 0.40 | 0.01 | 0.00 | 0.00 | 0.91 | 0 |
| qwen3.7-flash-vote | 100% | `deposit:chest_1:wood` x3 | 3/3 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `deposit:chest_1:wood` > `wait` x3 | 3/3 | 0.95 | 0.09 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `deposit:chest_1:wood` | 46 |
| `move_E` | 1 |
| `move_W` | 1 |

## Notes

Fourteen backends passed every repeat. `gpt-4.1-nano` and `gpt-4.1-nano-logprob` each failed one repeat of three, one choosing `move_E` and the other `move_W`, both moving instead of using an action that was already legal. That is the same failure in two directions, and `move_W` is the worse of the two because it steps away from the chest. Nobody was tempted by `craft:chest`. In the noul columns `qwen3.7-flash` wrote `done` 1.00 while holding 6 wood, and `gpt-4.1-nano-logprob` wrote `danger` 0.91 in an empty field, its second wild danger reading in the basics set. Jev's numbers were 0.03 done and 0.02 danger, with 0.93 confidence on the deposit.
