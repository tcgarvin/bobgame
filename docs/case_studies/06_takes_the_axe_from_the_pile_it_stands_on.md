# 06. Takes the axe from the pile it stands on



*Test*: `evals/test_jev_basics.py::test_takes_the_axe_from_the_pile_it_stands_on`

## The situation

Ada stands on item_pile_1, which holds one axe and four wood. Her pack is empty and she wields nothing. The brief says to pick up the axe from the pile she is standing on, and counts the job done when she carries an axe. Both `pickup:axe` and `pickup:wood` are on the option list, each labelled with how much is available, alongside the eight moves, `wait` and two `say` lines.

## Why this is a good test

This is the underfoot case again with a distractor inside it. The pile holds four wood and one axe, and wood is the thing settlers usually want, so `pickup:wood` is the tempting key. The judgement is that the brief names the axe and the count does not matter. A settler that takes wood spends a tick, fills its pack with the wrong item and still has no axe, and because the pile persists it can do that four times in a row without anything looking broken.

## What the model saw

- **Instruction**: Pick up the axe from the pile you are standing on.
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
........@........
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
    "instruction": "Pick up the axe from the pile you are standing on.",
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
    "inventory": {},
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
      "id": "item_pile_1",
      "type": "item_pile",
      "dx": 0,
      "dy": 0,
      "contents": {
        "axe": 1,
        "wood": 4
      }
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
| `pickup:axe` | pick up axe from the pile here (1 available) |
| `pickup:wood` | pick up wood from the pile here (4 available) |
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

**Gold answer**: `pickup:axe`

The assertions the test makes:

```python
assert decision.action == "pickup:axe", f"chose {decision.action}"
```

`pickup:wood` is defensible only if you ignore the brief, which is the whole point of the test. The gold answer is a fact, not a convention: the success condition names the axe. `done` should be low, and this is the noul most at risk of a false positive, because the axe is visible in the `contents` of the pile underfoot and a careless reading of "is the success condition met" can turn "there is an axe here" into "I am carrying an axe".

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `pickup:axe` > `move_NE`, `pickup:axe` > `move_W`, `pickup:axe` > `say:all_good` | 3/3 | 1.00 | 0.03 | 0.11 | 0.03 | 0.02 | 0 |
| gpt-5-nano | 100% | `pickup:axe` > `pickup:wood` x2, `pickup:axe` > `move_E` | 3/3 | 0.75 | 0.00 | 0.08 | 0.05 | 0.08 | 0 |
| qwen3.7-flash | 100% | `pickup:axe` > `wait` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `pickup:axe` > `wait` x3 | 3/3 | 0.83 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `pickup:axe` > `move_N` x3 | 3/3 | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `pickup:axe` > `pickup:wood` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `pickup:axe` > `wait` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `pickup:axe` > `move_W` x3 | 3/3 | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `pickup:axe` > `pickup:wood` x3 | 3/3 | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `pickup:axe` > `pickup:wood` x3 | 3/3 | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `pickup:axe` > `pickup:wood` x3 | 3/3 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `pickup:axe` x3 | 3/3 | 1.00 | 0.60 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-vote | 100% | `pickup:axe` x3 | 3/3 | 1.00 | 0.73 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `pickup:axe` > `move_N`, `pickup:axe` > `move_W` x2 | 3/3 | 0.91 | 0.74 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `pickup:axe` x3 | 3/3 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-logprob | 100% | `pickup:axe` > `wait` x3 | 3/3 | 0.95 | 0.00 | 0.00 | 0.00 | 0.01 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `pickup:axe` | 48 |

## Notes

Every backend passed on the action, 48 of 48 on `pickup:axe`, with `gpt-oss-20b`, `gpt-oss-120b`, `gpt-4.1-mini` and `gemini-2.5-flash` all putting `pickup:wood` second. The false-`done` risk showed up exactly as expected, and only in the chat models: `qwen3.7-flash-vote` wrote 1.00, `gpt-4.1-nano-logprob` 0.74, `gpt-4.1-nano-vote` 0.73 and `gpt-5-nano-vote` 0.60, all for a settler with an empty pack. Every one of those is over Jev's 0.6 cut-off, so on the drop-in thresholds four backends would end this stint on the tick before the axe was picked up. Jev wrote 0.03. The elicitation variants made it worse rather than better here: both vote backends scored higher on `done` than their plain JSON counterparts.
