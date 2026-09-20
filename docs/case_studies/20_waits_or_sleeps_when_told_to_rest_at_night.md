# 20. Waits or sleeps when told to rest at night

Night, fatigue 70, a bed one tile away, and a brief that says sleep on it.

*Test*: `evals/test_jev_judgement.py::test_waits_or_sleeps_when_told_to_rest_at_night`

## The situation

It is night, tick 250 of a 300-tick day. Ada is at fatigue 70 of 100 and flagged tired, at full health and food 80, carrying nothing and wielding nothing. A bed stands one tile east, marked `z` on the map. The brief is "sleep on the bed until rested", the success condition is "your fatigue is 0", and there are 120 ticks to do it in. The options include `sleep:bed_1` and `sleep:ground`, each with its recovery rate spelled out.

## Why this is a good test

An easy one by design, testing that the two sleep options are told apart rather than treated as the same verb. At night a bed recovers one fatigue a tick and the ground recovers one every two ticks, so the ground takes twice as long, and the brief names the bed anyway. The wrong answers are all near misses: `sleep:ground` works and is slower, `move_E` steps onto a bed the settler does not need to stand on, and `wait` does nothing while fatigue keeps climbing. A failure would double the cost of every night in a run, or stall a rest stint entirely.

## What the model saw

- **Instruction**: sleep on the bed until rested
- **Success condition**: your fatigue is 0

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
........@z.......
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
    "instruction": "sleep on the bed until rested",
    "success_condition": "your fatigue is 0"
  },
  "self": {
    "name": "ada",
    "position": [
      100,
      100
    ],
    "health": "20/20",
    "food": "80/100",
    "fatigue": "70/100 (tired)",
    "asleep": false,
    "wielded": "",
    "inventory": {},
    "at_workshop_table": false,
    "at_furnace": false,
    "at_anvil": false
  },
  "so_far": {
    "ticks_used": 0,
    "ticks_left": 120,
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
      "id": "bed_1",
      "type": "bed",
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
| `sleep:bed_1` | sleep on the bed bed_1: it recovers 1 fatigue per tick and heals 1 health every 5 ticks while you sleep (fatigue 70/100). You wake at fatigue 0, on damage, at food 0, or on a wake action |
| `sleep:ground` | sleep on the ground where you stand: it recovers 1 fatigue per 2 ticks while you sleep (fatigue 70/100). You wake at fatigue 0, on damage, at food 0, or on a wake action |
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

**Gold answer**: _none recorded; the test's own assertions are the gold answer_

The assertions the test makes:

```python
assert decision.action == "sleep:bed_1", f"chose {decision.action}"
```

There is no real ambiguity in the action, and every backend picked the bed. The one thing worth watching is `done`, which is not scored here: fatigue is 70 and the condition is 0, so `done` should be low, and llama-3.1-8b writes 1.00, gpt-4.1-nano-logprob 0.75, gpt-4.1-nano-vote 0.67 and gpt-5-nano-vote 0.53. Read this scenario as a floor rather than a discriminator.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `sleep:bed_1` > `move_E` x3 | - | 0.96 | 0.02 | 0.18 | 0.05 | 0.03 | 0 |
| gpt-5-nano | 100% | `sleep:bed_1` > `sleep:ground` x3 | - | 0.51 | 0.05 | 0.07 | 0.07 | 0.52 | 0 |
| qwen3.7-flash | 100% | `sleep:bed_1` > `wait` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `sleep:bed_1` > `wait` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 100% | `sleep:bed_1` > `wait` x3 | - | 0.80 | 1.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-20b | 100% | `sleep:bed_1` > `wait` x3 | - | 0.88 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `sleep:bed_1` > `wait` x3 | - | 0.92 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `sleep:bed_1` > `sleep:ground` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `sleep:bed_1` > `wait` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-mini | 100% | `sleep:bed_1` > `wait` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `sleep:bed_1` > `wait` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `sleep:bed_1` x3 | - | 1.00 | 0.53 | 0.00 | 0.00 | 0.07 | 0 |
| gpt-4.1-nano-vote | 100% | `sleep:bed_1` x3 | - | 1.00 | 0.67 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `sleep:bed_1` > `sleep:ground` x3 | - | 0.60 | 0.75 | 0.00 | 0.00 | 0.00 | 0 |
| qwen3.7-flash-vote | 100% | `sleep:bed_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.07 | 0 |
| qwen3.7-flash-logprob | 100% | `sleep:bed_1` > `wait` x3 | - | 0.95 | 0.01 | 0.01 | 0.00 | 0.09 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `sleep:bed_1` | 48 |

## Notes

A sixteen-way tie at 100%, with all 48 top-1 picks going to `sleep:bed_1`. Nothing separates on the action, and the scenario earns its place by what the unscored `done` column shows.

- Four backends would have ended this stint early on Jev's 0.6 gate, on a brief whose condition is plainly unmet, and three of the four are elicitation variants. Both vote and logprob push `done` upward here, which looks like "is the settler doing the right thing" leaking into "is the brief satisfied".
- Jev writes done 0.02 and takes the bed at 0.96 confidence, its highest anywhere in this set. On the scenarios where the answer is obvious, Jev is obvious too, and it costs less than every backend in the table except qwen3.7-flash.
