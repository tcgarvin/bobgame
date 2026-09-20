# 30. Gathers the missing fiber instead of crafting

Rope costs 2 fiber and the pack holds 1, so `craft:rope` is not even offered: the right read is "one more harvest", not "the brief is broken".

*Test*: `evals/test_jev_situations.py::test_gathers_the_missing_fiber_instead_of_crafting`

## The situation

Ada carries a single fiber and no tool, standing next to reeds that hold three more. The brief says to craft a rope and is finished when the pack holds one, with a note spelling out that rope costs two fiber and that reeds_1 is next to you. Because one fiber does not buy a rope, `craft:rope` is not on the option list at all. Thirty ticks are left.

## Why this is a good test

This is the near twin of case 31 and the opposite answer. In both, an option the brief needs is absent from the list, and the question is whether absence means the brief is broken or simply not yet. Here it means not yet: one harvest of the reeds next door supplies the shortfall, and the whole job is perhaps three ticks from done. The tempting failure is to read the missing craft option as impossibility and raise `lost`, which would bounce the brief back to the planner and buy a full planning turn to be told to pick up a reed. The test asserts both halves, the extract and a `lost` below 0.45, because either alone would miss the point.

## What the model saw

- **Instruction**: Craft a rope.
- **Success condition**: you are carrying a rope
- **Notes**: Rope costs 2 fiber. reeds_1 is next to you.

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
........@r.......
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
    "instruction": "Craft a rope.",
    "success_condition": "you are carrying a rope"
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
      "fiber": 1
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
      "id": "reeds_1",
      "type": "reeds",
      "dx": 1,
      "dy": 0,
      "remaining": 3,
      "yields": "fiber"
    }
  ],
  "entities": [],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "notes": "Rope costs 2 fiber. reeds_1 is next to you."
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `extract:reeds_1` | harvest fiber from the reeds at dx 1 dy 0, 3 units left (1 work per action bare-handed, 3 work per unit) |
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

**Gold answer**: extract:reeds_1, lost below 0.45

The assertions the test makes:

```python
assert "craft:rope" not in criteria, "one fiber should not buy a rope"
assert "extract:reeds_1" in top_keys(decision), f"top two {top_keys(decision)}"
assert decision.lost < LOST_LOW, f"lost was {decision.lost}"
```

Very little room, and if anything the scenario is easier than its pair. The brief's `notes` state the recipe cost and name the adjacent reeds, which is more help than a brief usually gives, so the inference the model has to make is nearly done for it. The only alternative action is `wait`, and nothing recommends it. A harder version would drop the notes and see whether the shortfall can be read from the option list alone. As written, the case earns its place as a control rather than a challenge: it checks that a model which correctly raises `lost` on a genuinely impossible brief does not raise it on every brief with a missing option.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:reeds_1` > `move_W`, `extract:reeds_1` > `move_NW`, `extract:reeds_1` > `move_E` | - | 1.00 | 0.03 | 0.22 | 0.14 | 0.02 | 0 |
| gpt-5-nano | 100% | `extract:reeds_1` > `move_E` x2, `extract:reeds_1` > `wait` | - | 0.62 | 0.00 | 0.08 | 0.07 | 0.08 | 0 |
| qwen3.7-flash | 100% | `extract:reeds_1` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `extract:reeds_1` > `move_E` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 67% | `extract:reeds_1` > `move_E` x2, `error` | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 1 |
| gpt-oss-20b | 100% | `extract:reeds_1` > `wait` x2, `extract:reeds_1` | - | 0.97 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `extract:reeds_1` > `wait` x3 | - | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `extract:reeds_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `extract:reeds_1` > `say:all_good` x3 | - | 0.68 | 0.00 | 0.00 | 0.00 | 0.05 | 0 |
| gpt-4.1-mini | 100% | `extract:reeds_1` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `extract:reeds_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `extract:reeds_1` x3 | - | 1.00 | 0.07 | 0.00 | 0.00 | 0.27 | 0 |
| gpt-4.1-nano-vote | 100% | `extract:reeds_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `extract:reeds_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.20 | 0.01 | 0 |
| qwen3.7-flash-vote | 100% | `extract:reeds_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:reeds_1` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:reeds_1` | 47 |
| `error` | 1 |

## Notes

Fifteen of sixteen backends pass. The single miss is llama-3.1-8b at 67%, and that is an errored repeat rather than a wrong answer: it produced no row, which the report counts as a failure. `extract:reeds_1` takes 47 of the 48 top-1 picks. So the scenario does not discriminate on action, and its value is entirely as the negative half of the `lost` pair with case 31. The caution is that most chat models pass it by writing 0.00 for `lost` everywhere, which is not the same as passing it for the right reason: the same models write 0.00 on case 29, where 0.00 is wrong, and the run-level analysis gives nine of them a `lost` AUC of exactly 0.50, the score of a constant. Jev writes 0.14 here and 0.80 on case 29 off one scale, which is what makes its 1.00 AUC on `lost` mean something. Its confidence here, 1.00, is the highest it records across these eleven scenarios.
