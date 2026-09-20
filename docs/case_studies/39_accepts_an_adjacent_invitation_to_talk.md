# 39. Accepts an adjacent invitation to talk

jory is standing next to you and open to talk, and the brief is to talk to jory: `talk_to:jory` is the one option that does it.

*Test*: `evals/test_jev_situations.py::test_accepts_an_adjacent_invitation_to_talk`

## The situation

jory stands one tile east, open to talk, having just said "Anyone want to talk about the wall?". ada is unarmed, at full health, with an empty pack and 30 ticks in the stint. A tree sits three tiles away to the southeast. The brief says talk to jory about the plan for the wall and counts success as having talked with jory. The state carries the invitation twice, once in the `invitations` list and once in what ada heard, and the option `talk_to:jory` accepts it, opening a two-seat conversation on a free tile next to both of them.

## Why this is a good test

This is the floor test for the invitation mechanic, which exists because settlers coordinated over shouts and opened a conversation about once in eight hundred ticks. Everything lines up here: the brief names jory, jory is adjacent, jory is open, and exactly one option does the thing. The near-misses are the canned lines, `say:come_here` when jory is already here and `say:all_good` which says nothing, and `attack:jory`, which is on the list only because adjacency puts it there. A failure would mean the mechanic is unusable, since an invitation nobody accepts is the same as no invitation at all.

## What the model saw

- **Instruction**: Talk to jory about the plan for the wall.
- **Success condition**: you have talked with jory
- **Open invitations**: jory at dx 1 dy 0 is open to talk: "Anyone want to talk about the wall?"

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
........@P.......
.................
.................
...........T.....
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
    "instruction": "Talk to jory about the plan for the wall.",
    "success_condition": "you have talked with jory"
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
      "id": "tree_1",
      "type": "tree",
      "dx": 3,
      "dy": 3,
      "remaining": 4,
      "yields": "wood"
    }
  ],
  "entities": [
    {
      "id": "jory",
      "type": "player",
      "dx": 1,
      "dy": 0,
      "health": "20/20",
      "wielded": ""
    }
  ],
  "map_legend": ". walkable, # blocked or wall, ~ water, T tree, o rock, B bush with a berry, b bush with no berry, r reeds, y clay, v ore vein, C chest, M message board, X workshop table, F furnace, A anvil, + door, z bed, f furniture, , road or floor, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": [],
  "invitations": [
    "jory at dx 1 dy 0 is open to talk: \"Anyone want to talk about the wall?\""
  ],
  "heard": [
    "jory: Anyone want to talk about the wall?"
  ]
}
```

**The options it had to choose between**:

| key | what it does |
| --- | --- |
| `wait` | do nothing this tick and hold position |
| `attack:jory` | attack the adjacent player jory (health 20/20) |
| `talk_to:jory` | accept jory's invitation to talk: a conversation with the two of you starts on a free tile next to you both |
| `move_N` | step one tile N onto known ground |
| `move_S` | step one tile S onto known ground |
| `move_SW` | step one tile SW onto known ground |
| `move_W` | step one tile W onto known ground |
| `move_NW` | step one tile NW onto known ground |
| `say:come_here` | say "Come to me." out loud to anyone nearby |
| `say:all_good` | say "All good here." out loud to anyone nearby |
| `step_towards:tree_1` | one step toward tree_1 (tree) at dx 3 dy 3, 3 tiles away |

### The five questions asked of every backend

| question | wording |
| --- | --- |
| action (Choice over the options above) | You are controlling a settler in a survival world. Given the state, which single action should the settler take this tick to make progress on the brief while staying alive? |
| done (Noul) | Is the brief's success condition met right now, as far as the state shows? |
| stuck (Noul) | Has the brief become impossible, or does the situation need a judgement the brief does not cover? Being far from a target that has a step option toward it is neither. |
| lost (Noul) | Is progress on the brief impossible from here because something it needs is missing from this state: a target that is not on the map or that no step option leads toward, or an item or station the brief needs that is not here and cannot be got here? A named place with a step option toward it is not missing, even when it is far away. |
| danger (Noul) | Is this settler in immediate danger of dying within the next few ticks? |

## What we expect

**Gold answer**: talk_to:jory

The assertions the test makes:

```python
assert "talk_to:jory" in criteria, f"no invitation offered in {sorted(criteria)}"
assert decision.action == "talk_to:jory", f"chose {decision.action}"
```

There is essentially no ambiguity in the action, and that is deliberate. What the scenario measures is whether a model can pick the one option whose description matches the brief's verb, not whether it can judge that a conversation is worth the ticks it blocks.

The number worth watching is `done`. The success condition is "you have talked with jory" and nothing has been said yet, so `done` should be near zero even though the subject of the conversation is sitting right there saying hello. Jev wrote 0.10.

One thing the option list makes plain: `attack:jory` is offered on every tick that a settler stands next to another settler, all run long. This scenario is the cheapest place to check that nobody takes it.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `talk_to:jory` > `move_W`, `talk_to:jory` > `wait`, `talk_to:jory` > `attack:jory` | - | 1.00 | 0.10 | 0.11 | 0.04 | 0.03 | 0 |
| gpt-5-nano | 100% | `talk_to:jory` > `say:all_good` x3 | - | 0.52 | 0.22 | 0.10 | 0.07 | 0.03 | 0 |
| qwen3.7-flash | 100% | `talk_to:jory` > `move_NW` x2, `talk_to:jory` > `move_N` | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 100% | `talk_to:jory` > `wait` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llama-3.1-8b | 67% | `talk_to:jory` > `say:all_good` x2, `error` | - | 0.80 | 1.00 | 0.00 | 0.00 | 0.00 | 1 |
| gpt-oss-20b | 100% | `talk_to:jory` > `say:all_good` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash-lite | 100% | `talk_to:jory` > `say:all_good` x3 | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 100% | `talk_to:jory` > `say:come_here` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 100% | `talk_to:jory` > `say:come_here` x3 | - | 0.70 | 0.05 | 0.02 | 0.01 | 0.05 | 0 |
| gpt-4.1-mini | 100% | `talk_to:jory` > `say:all_good` x3 | - | 0.90 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `talk_to:jory` > `wait` x3 | - | 0.97 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 100% | `talk_to:jory` x3 | - | 1.00 | 0.73 | 0.00 | 0.00 | 0.07 | 0 |
| gpt-4.1-nano-vote | 100% | `talk_to:jory` x3 | - | 1.00 | 0.47 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 100% | `talk_to:jory` > `say:come_here` x3 | - | 0.60 | 0.02 | 0.00 | 0.03 | 0.00 | 0 |
| qwen3.7-flash-vote | 67% | `talk_to:jory` x2, `error` | - | 1.00 | 0.60 | 0.00 | 0.00 | 0.00 | 1 |
| qwen3.7-flash-logprob | 100% | `talk_to:jory` > `wait`, `talk_to:jory` > `move_NW`, `talk_to:jory` > `move_N` | - | 0.95 | 0.07 | 0.00 | 0.00 | 0.00 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `talk_to:jory` | 46 |
| `error` | 2 |

## Notes

Every one of the 46 answers that came back is `talk_to:jory`, and the two missing rows are failed calls from llama-3.1-8b and qwen3.7-flash-vote, not wrong choices. Jev answered at confidence 1.00, its highest in this block. No backend attacked jory.

- The failure mode is not in the action column, it is in `done`. llama-3.1-8b wrote 1.00 for a conversation that has not happened, gpt-5-nano-vote 0.73, qwen3.7-flash-vote 0.60 and gpt-4.1-nano-vote 0.47. Two ticks of `done` above 0.6 ends the stint before the conversation opens and reports to the planner that the settler has talked.
- Both vote variants are among the inflators, which matches the pattern across this block: voting yes or no five times at temperature 1 rounds a soft judgement into a confident one.
- Jev's value here is not the action, which everyone got, but that its `done` stayed low while its action confidence was maximal. Those are two separate questions and its answers to them do not contaminate each other.
