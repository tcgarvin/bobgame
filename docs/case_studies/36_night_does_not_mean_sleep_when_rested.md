# 36. Night does not mean sleep when rested

Night, fatigue 30, a tree in reach: darkness alone is no reason to stop, and `sleep:ground` is offered at any fatigue above zero.

*Test*: `evals/test_jev_situations.py::test_night_does_not_mean_sleep_when_rested`

## The situation

It is tick 250 of a 300 tick day, so the state says night. ada has an axe, full health, food at 80 and fatigue at 30 out of 100, which still counts as fresh. A tree with 4 wood in it stands one tile east and nothing else is in view. The brief says chop tree_1 for wood, with success at 6 wood. Because fatigue is above zero, the option list offers `sleep:ground` alongside the work.

## Why this is a good test

This is the negative control for the day clock. The state says `night: true` in plain words and the option list dangles sleep, so a model that treats darkness as an instruction will stop working every evening. The physics say otherwise: tired starts at 60 and collapse at 100, and this settler is at 30, so sleeping buys almost nothing and costs the rest of a 40 tick stint. A failure here scales badly. Twelve settlers that all read night as bedtime give up a third of every day, and the settlement never gets built.

## What the model saw

- **Instruction**: Chop tree_1 for wood.
- **Success condition**: you are carrying 6 wood

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
    "instruction": "Chop tree_1 for wood.",
    "success_condition": "you are carrying 6 wood"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "20/20",
    "food": "80/100",
    "fatigue": "30/100 (fresh)",
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
    "tick_of_day": "250/300",
    "night": true
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
| `sleep:ground` | sleep on the ground where you stand: it recovers 1 fatigue per 2 ticks while you sleep (fatigue 30/100). You wake at fatigue 0, on damage, at food 0, or on a wake action |
| `extract:tree_1` | harvest wood from the tree at dx 1 dy 0, 4 units left (3 work per action with the axe, 3 work per unit) |
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

**Gold answer**: extract:tree_1

The assertions the test makes:

```python
assert "extract:tree_1" in top_keys(decision), f"top two {top_keys(decision)}"
```

`sleep:ground` is defensible in a longer view, because ground sleep recovers twice as fast at night as by day, so if a settler is going to sleep at all, now is the cheap time. The brief and the stint are what rule it out: the instruction says chop and there are 40 ticks to do it in.

This is an easy scenario and it does not discriminate between backends. It earns its place as the matched negative to case study 33, where a settler close to collapse should sleep on the ground. All four nouls should sit near zero here; Jev's `stuck` at 0.28 is the only number above the noise and it stays well clear of the gate.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:tree_1` > `sleep:ground` x3 | - | 0.97 | 0.02 | 0.28 | 0.05 | 0.03 | 0 |
| gpt-5-nano | 100% | `extract:tree_1` > `move_N` x3 | - | 0.84 | 0.07 | 0.08 | 0.08 | 0.22 | 0 |
| qwen3.7-flash | 100% | `extract:tree_1` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `extract:tree_1` > `move_N` x3 | - | 0.67 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 67% | `extract:tree_1` > `move_S` x2, `error` | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |
| gpt-oss-20b | 100% | `extract:tree_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `extract:tree_1` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `extract:tree_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `extract:tree_1` > `wait` x3 | - | 0.78 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `extract:tree_1` > `wait` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `extract:tree_1` > `wait` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `extract:tree_1` x3 | - | 1.00 | 0.27 | 0.00 | 0.00 | 0.27 | 0 |
| gpt-4.1-nano-vote | 100% | `extract:tree_1` x3 | - | 1.00 | 0.07 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `extract:tree_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:tree_1` > `wait` x3 | - | 0.95 | 0.00 | 0.01 | 0.00 | 0.02 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:tree_1` | 47 |
| `error` | 1 |

## Notes

Everybody passed. 47 of the 48 top-1 answers are `extract:tree_1`, and the one row that is not an answer is llama-3.1-8b's single failed API call rather than a wrong choice.

- There is no failure mode to name. The value of the scenario is that a backend that failed it would have been reading the clock as an order.
- Jev is the most confident backend at 0.97 and the only one whose runner-up was `sleep:ground`. That is the right shape for a second choice: it saw the option, weighed it, and put it below the work, rather than not seeing it. Most chat models ranked `wait` or a random compass step second, which says less about their reading of the night.
