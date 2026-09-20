# 18. Danger when low health and wolf

Three health, a wolf biting, nobody else: that is dying.

*Test*: `evals/test_jev_judgement.py::test_danger_when_low_health_and_wolf`

## The situation

Ada has 3 health of 20 and a sword. A wolf at full health, 16 of 16, stands one tile east. The threat block says no other settler is within three tiles and nobody else is on the wolf. Her food is 80 and she is fresh. The brief has not changed to match the emergency: it still says "gather wood", success at five wood, with thirty ticks left.

## Why this is a good test

A wolf bite is three damage a tick, so this settler dies this tick or the next. The judgement is pure arithmetic against the facts every state carries, and the test scores `danger` alone. The failure case is concrete: the reflex never fires, the hard rule that pulls a settler at danger above 0.8 and health below 6 back toward the settlement never fires, and the settler dies mid-brief and drops its pack where it stood.

## What the model saw

- **Instruction**: gather wood
- **Success condition**: you carry 5 wood

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
    "instruction": "gather wood",
    "success_condition": "you carry 5 wood"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "3/20",
    "food": "80/100",
    "fatigue": "0/100 (fresh)",
    "asleep": false,
    "wielded": "sword",
    "inventory": {},
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

**Gold answer**: _none recorded; the test's own assertions are the gold answer_

The assertions the test makes:

```python
assert decision.danger > DANGER_HIGH, f"danger was {decision.danger}"
```

The action is genuinely open and the test does not score it, which is right. Attacking is not obviously correct at 3 health against a 16-health wolf with no ally, because you lose that race. Fleeing is not obviously correct either, since a wolf follows. The tally splits 37 attack against 11 flee and both are defensible. What is not defensible is `danger` below 0.6. One thing worth flagging: Jev also reads stuck 0.61 and lost 0.74 here. That is arguably right, since "gather wood" has become a situation the brief does not cover, but it is also the kind of reading that would end a stint under the `lost` heading rather than the danger one.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `attack:wolf_1` > `move_W` x2, `move_W` > `attack:wolf_1` | - | 0.31 | 0.02 | 0.61 | 0.74 | 0.75 | 0 |
| gpt-5-nano | 33% | `attack:wolf_1` > `move_W` x3 | - | 0.55 | 0.00 | 0.10 | 0.11 | 0.54 | 0 |
| qwen3.7-flash | 100% | `attack:wolf_1` > `move_N` x3 | - | 0.72 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| mistral-nemo | 100% | `move_N` > `move_NW` x3 | - | 0.42 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| llama-3.1-8b | 100% | `move_S` > `wait` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gpt-oss-20b | 100% | `attack:wolf_1` > `wait`, `move_W` > `move_N`, `attack:wolf_1` > `move_W` | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.93 | 0 |
| gemini-2.5-flash-lite | 100% | `attack:wolf_1` > `move_W` x3 | - | 0.83 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gpt-4.1-nano | 100% | `attack:wolf_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.70 | 0 |
| gpt-oss-120b | 100% | `move_W` > `move_N` x3 | - | 0.50 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gpt-4.1-mini | 100% | `attack:wolf_1` > `move_W` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gemini-2.5-flash | 100% | `attack:wolf_1` > `move_W` x3 | - | 0.81 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-5-nano-vote | 100% | `attack:wolf_1` > `wait` x2, `attack:wolf_1` > `move_W` | - | 0.67 | 0.40 | 0.00 | 0.00 | 0.93 | 0 |
| gpt-4.1-nano-vote | 100% | `attack:wolf_1` x3 | - | 1.00 | 0.07 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `attack:wolf_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.87 | 0 |
| qwen3.7-flash-vote | 100% | `attack:wolf_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| qwen3.7-flash-logprob | 100% | `attack:wolf_1` > `move_N` x3 | - | 0.72 | 0.00 | 0.05 | 0.14 | 0.91 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `attack:wolf_1` | 37 |
| `move_W` | 5 |
| `move_N` | 3 |
| `move_S` | 3 |

## Notes

Fifteen of sixteen backends pass, most of them writing 0.87 to 1.00. The one miss is gpt-5-nano at a mean of 0.54 and one pass in three. Its danger standard deviation across the whole run is 0.134, the highest of any backend, so this reads as noise around the threshold rather than a considered answer.

- The honest summary is that this is the easiest noul in the suite. Nine backends score danger AUC 1.00 over the full run. If danger were the only thing the stint needed, almost anything on the list would do.
- Jev's 0.75 is lower than most of the field. That is the recurring shape: Jev's probabilities are compressed toward the middle and its thresholds are set for that scale, while the chat models write 0 or 1 and almost nothing in between. The decile tables make it plain, with gemini-2.5-flash putting 109 of 132 danger answers in the bottom decile, 18 in the top and 5 anywhere else.
- A model that only ever answers 0 or 1 passes every scenario whose gold answer is at an extreme and tells you nothing on the ones in the middle, which is where a real run spends most of its time.
