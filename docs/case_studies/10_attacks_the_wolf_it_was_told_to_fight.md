# 10. Attacks the wolf it was told to fight



*Test*: `evals/test_jev_basics.py::test_attacks_the_wolf_it_was_told_to_fight`

## The situation

Ada stands with a sword wielded and a spare in her pack, at full health, food 80, fresh. A wolf at 16 of 16 health is one tile east. The state's threat block says one wolf in view, no other settlers next to it and none within three tiles of her: she is alone. The brief says to attack wolf_1 until it is dead. The option list offers the attack, five moves away, `wait` and two `say` lines.

## Why this is a good test

The judgement is following a fighting brief while the state is telling the settler that a solo fight is expensive. The physics in the facts block are explicit: the wolf has 16 health and bites for 3 every tick, so a lone swordsman wins but loses about 12 of 20 health doing it. The tempting wrong answers are the retreat moves and `say:come_here`, which is the reasonable-sounding call for help the cooperation update put in the world. A settler that retreats here does not escape, it just takes bites while walking, and a stint that never closes with the wolf leaves it alive next to the settlement.

## What the model saw

- **Instruction**: Attack wolf_1 until it is dead.
- **Success condition**: wolf_1 is dead

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
........@W.......
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
    "instruction": "Attack wolf_1 until it is dead.",
    "success_condition": "wolf_1 is dead"
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
    "wielded": "sword",
    "inventory": {
      "sword": 1
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
  "nearby": [],
  "entities": [
    {
      "id": "wolf_1",
      "type": "wolf",
      "dx": 1,
      "dy": 0,
      "health": "16/16",
      "wielded": ""
    }
  ],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "threat": {
    "wolves_in_view": 1,
    "nearest_wolf": {
      "id": "wolf_1",
      "dx": 1,
      "dy": 0,
      "health": "16/16",
      "settlers_next_to_it": 0
    },
    "settlers_within_3_of_you": 0
  }
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `attack:wolf_1` | attack the adjacent wolf wolf_1 (health 16/16, 0 other settlers next to it) |
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

**Gold answer**: `attack:wolf_1`

The assertions the test makes:

```python
assert decision.action == "attack:wolf_1", f"chose {decision.action}"
```

`say:come_here` is genuinely defensible and would be the better move if the brief were open. The brief is not open: it names the wolf and says fight it until it dies, and the planner has already made the judgement that this fight is worth taking. So the gold answer is obedience to the brief, not the best move in the abstract. The `danger` noul is the honest ambiguity here. The question asks about dying within the next few ticks, and a full-health settler with a sword against one wolf is not about to die, but she is going to lose more than half her health. This is why the analysis's label table leaves this scenario out of the `danger` negatives entirely: neither 0.00 nor 1.00 is the right answer.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `attack:wolf_1` > `move_W` x3 | 3/3 | 0.93 | 0.02 | 0.20 | 0.06 | 0.38 | 0 |
| gpt-5-nano | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.58 | 0.00 | 0.03 | 0.03 | 0.35 | 0 |
| qwen3.7-flash | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| llama-3.1-8b | 100% | `attack:wolf_1` > `move_S` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.87 | 0.00 | 0.00 | 0.00 | 0.07 | 0 |
| gemini-2.5-flash-lite | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `attack:wolf_1` > `move_W` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-oss-120b | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.68 | 0.00 | 0.00 | 0.00 | 0.20 | 0 |
| gpt-4.1-mini | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.85 | 0.00 | 0.00 | 0.00 | 0.70 | 0 |
| gemini-2.5-flash | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.92 | 0.00 | 0.00 | 0.00 | 0.63 | 0 |
| gpt-5-nano-vote | 100% | `attack:wolf_1` x3 | 3/3 | 1.00 | 0.40 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano-vote | 100% | `attack:wolf_1` x3 | 3/3 | 1.00 | 0.93 | 0.00 | 0.00 | 0.40 | 0 |
| gpt-4.1-nano-logprob | 100% | `attack:wolf_1` > `move_W` x3 | 3/3 | 0.70 | 0.12 | 0.00 | 0.08 | 0.47 | 0 |
| qwen3.7-flash-vote | 100% | `attack:wolf_1` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| qwen3.7-flash-logprob | 100% | `attack:wolf_1` > `wait` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.17 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `attack:wolf_1` | 48 |

## Notes

Every backend passed on the action, 48 of 48 on `attack:wolf_1`, so the whole result is in the `danger` column, and it spreads further than any other basics scenario. `qwen3.7-flash`, `llama-3.1-8b`, `gemini-2.5-flash-lite` and `gpt-4.1-nano-vote` sit at or near the bottom, and `mistral-nemo` at 0.90, `gpt-5-nano-vote` at 1.00 and `qwen3.7-flash-vote` at 1.00 sit at the top. A 1.00 says the settler is about to die when she is at 20 of 20 with a sword. Jev returned 0.38: above the 0.02 it gives every peaceful scene in this set, below its own 0.6 gate, and the only number in the table that reads like a measurement of how bad the fight is rather than a vote on whether a wolf is scary. The vote elicitation made the spread worse, pushing both vote backends to the extreme their base model had avoided.
