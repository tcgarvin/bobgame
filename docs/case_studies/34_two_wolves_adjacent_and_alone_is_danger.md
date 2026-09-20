# 34. Two wolves adjacent and alone is danger

Six damage a tick against 14 health and no ally: three ticks to live, and the sword kills neither wolf in that time.

*Test*: `evals/test_jev_situations.py::test_two_wolves_adjacent_and_alone_is_danger`  ·  **expected to fail** (`xfail`)

## The situation

ada stands at the settlement site in daylight with 14 of 20 health, a sword in hand and an empty pack. A wolf sits one tile east and another one tile west, both at 16 health, and no other settler is within three tiles. The brief says to gather wood from the trees near the site and counts success as carrying 6 wood; there is no tree in view. The options are to attack either wolf, to step north or south out of the pincer, to wait, to shout "Wolves on me, help!", or to say one of two canned lines.

## Why this is a good test

This scenario grades one number, not the move. Two wolves biting for 3 each is 6 damage a tick against 14 health, which is three ticks to live, and a sword does not kill a 16 health wolf in three ticks. The tempting reading is that a settler with a weapon and a full stomach is fine, because nothing in the state says "dying": health is not critical, food is high, fatigue is zero. Attacking looks productive, which is exactly why `danger` has to be read apart from the action: the stint and the reflex brief act on the number, not the choice. A settler that rates this calm keeps swinging while its planner is asleep and dies with the brief still saying gather wood.

## What the model saw

- **Instruction**: Gather wood from the trees near the site.
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
.......W@W.......
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
    "instruction": "Gather wood from the trees near the site.",
    "success_condition": "you are carrying 6 wood"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "14/20",
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
    "ticks_left": 40,
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
      "health": "16/20",
      "wielded": ""
    },
    {
      "id": "wolf_2",
      "type": "wolf",
      "dx": -1,
      "dy": 0,
      "health": "16/20",
      "wielded": ""
    }
  ],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "threat": {
    "wolves_in_view": 2,
    "nearest_wolf": {
      "id": "wolf_1",
      "dx": 1,
      "dy": 0,
      "health": "16/20",
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
| `shout:0` | shout "Wolves on me, help!" - every settler within 60 tiles hears it and where it came from |
| `attack:wolf_1` | attack the adjacent wolf wolf_1 (health 16/20, 0 other settlers next to it) |
| `attack:wolf_2` | attack the adjacent wolf wolf_2 (health 16/20, 0 other settlers next to it) |
| `move_N` | step one tile N onto known ground |
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

**Gold answer**: danger above 0.6

The assertions the test makes:

```python
assert decision.danger > DANGER_HIGH, f"danger was {decision.danger}"
```

The action is genuinely open and the test does not grade it. Attacking wolf_1, stepping north out of the pincer, and shouting for help are all defensible, and which is best depends on whether one step actually breaks contact with both wolves.

The debatable part is the gate. Jev put `danger` at 0.51, just under the 0.6 the judgement suite uses, and the scenario is marked xfail for that alone. 0.51 is not "this settler is calm", it is "this is a coin flip", which is an arguable reading of 14 health when one step might break contact. Jev's `stuck` at 0.59 and `lost` at 0.72 would eject the stint anyway, so the live behaviour is right and only the number fails. Worth saying plainly: a threshold assertion on a single noul is the thinnest test in this suite, and this is where that shows.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 0% | `attack:wolf_1` > `move_N` x3 | - | 0.35 | 0.02 | 0.59 | 0.72 | 0.51 | 0 |
| gpt-5-nano | 33% | `attack:wolf_1` > `attack:wolf_2` x3 | - | 0.40 | 0.00 | 0.05 | 0.02 | 0.50 | 0 |
| qwen3.7-flash | 33% | `move_N` > `move_S` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.33 | 0 |
| mistral-nemo | 100% | `move_N` > `move_S`, `move_S` > `attack:wolf_1` x2 | - | 0.47 | 0.00 | 0.00 | 0.00 | 0.80 | 0 |
| llama-3.1-8b | 0% | `move_N` > `wait` x2, `error` | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.15 | 1 |
| gpt-oss-20b | 100% | `attack:wolf_1` > `attack:wolf_2` x3 | - | 0.73 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gemini-2.5-flash-lite | 100% | `attack:wolf_1` > `attack:wolf_2` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 0.80 | 0 |
| gpt-4.1-nano | 0% | `move_N` > `move_S` x3 | - | 0.40 | 0.00 | 0.03 | 0.02 | 0.23 | 0 |
| gpt-oss-120b | 100% | `attack:wolf_1` > `attack:wolf_2` x3 | - | 0.55 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gpt-4.1-mini | 100% | `shout:0` > `attack:wolf_1` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.90 | 0 |
| gemini-2.5-flash | 100% | `attack:wolf_1` > `attack:wolf_2` x3 | - | 0.40 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-5-nano-vote | 100% | `attack:wolf_1` x2, `attack:wolf_1` > `attack:wolf_2` | - | 0.93 | 0.33 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano-vote | 100% | `attack:wolf_1` > `move_S`, `attack:wolf_1` > `move_N` x2 | - | 0.60 | 0.07 | 0.00 | 0.00 | 1.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `move_N` > `move_S` x3 | - | 0.40 | 0.00 | 0.01 | 0.04 | 0.91 | 0 |
| qwen3.7-flash-vote | 100% | `move_N` > `attack:wolf_1`, `attack:wolf_1` > `move_N`, `attack:wolf_1` > `move_S` | - | 0.47 | 0.00 | 0.00 | 0.00 | 1.00 | 0 |
| qwen3.7-flash-logprob | 100% | `move_N` > `move_S` x2, `attack:wolf_1` > `attack:wolf_2` | - | 0.55 | 0.00 | 0.04 | 0.04 | 0.88 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `attack:wolf_1` | 28 |
| `move_N` | 14 |
| `shout:0` | 3 |
| `move_S` | 2 |
| `error` | 1 |

## Notes

Nine backends cleared the bar. gemini-2.5-flash wrote 1.00, gpt-oss-20b, gpt-oss-120b, gemini-2.5-flash-lite and gpt-4.1-mini wrote 0.90, mistral-nemo 0.80. The clear failures are llama-3.1-8b at 0.15 and gpt-4.1-nano at 0.23; gpt-5-nano and qwen3.7-flash cleared 0.6 in one repeat of three.

- The failure is scale, not ordering. gpt-4.1-nano has a `danger` AUC of 1.00 in the matrix analysis and an own-best threshold of 0.20: it knows this is the most dangerous state it was shown, it just never writes a big number. That is Approach C failing while Approach A passes, in one scenario.
- Elicitation fixed it for the self-reporters. Both vote variants and both logprob variants passed every repeat, with gpt-4.1-nano going from 0.23 to 0.91 under logprobs and 1.00 under voting. If the plan were to deploy one of these models, this is the scenario that says you would have to retune the stint's cut-offs or change how you ask.
- For Jev the reverse holds. Its `danger` ordering is perfect across the suite (AUC 1.00, own-threshold separation 100%) and its scale is a real distribution rather than a written-down guess. What fails here is the fixed 0.6 line sitting just above this particular state.
