# 33. Sleeps on the ground before collapsing

Fatigue 97 at midday: three more ticks of work and the body falls over where it stands. Sleeping badly now beats collapsing in the open.

*Test*: `evals/test_jev_situations.py::test_sleeps_on_the_ground_before_collapsing`

## The situation

Ada is at fatigue 97 of 100 at midday, otherwise in good shape: full health, food at 80, an axe in hand and an empty pack. A tree holding four wood is one tile east. The brief is to chop tree_1 until the pack holds six wood, with forty ticks. `sleep:ground` is on the option list, described as recovering one fatigue per four ticks by day.

## Why this is a good test

The arithmetic is all in `facts` and it does not favour the obvious action. Fatigue rises one every four ticks by day, so collapse is about twelve ticks off. Being tired already halves the work an axe adds per extract, so the chopping is slow, and at 100 the body drops where it stands and sleeps until fatigue falls to 70, which by day is well over a hundred ticks of lying in the open, outside the brief and outside anyone's plan. Deliberate ground sleep is a bad option taken to avoid a worse one. The failure is a settler that collapses mid-task in the open, where a wolf can find it asleep.

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
    "fatigue": "97/100 (tired)",
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
| `sleep:ground` | sleep on the ground where you stand: it recovers 1 fatigue per 4 ticks while you sleep (fatigue 97/100). You wake at fatigue 0, on damage, at food 0, or on a wake action |
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

**Gold answer**: sleep:ground in the top two, or stuck above 0.5

The assertions the test makes:

```python
assert "sleep:ground" in criteria, "ground sleep should be offered above fatigue 0"
assert (
    "sleep:ground" in top_keys(decision) or decision.stuck > STUCK_OR_LOST_HIGH
), f"top two {top_keys(decision)}, stuck {decision.stuck}"
```

The gate is wide: either `sleep:ground` in the top two or `stuck` above 0.5, and the top-two allowance again does most of the work, since no backend chose sleep as its top action and every pass comes from ranking it second behind chopping. Whether second place is really the right behaviour is arguable. A settler that keeps chopping while rating sleep second will still collapse, because the same reasoning recurs next tick. There is also a real case for chopping: forty ticks remain, the tree needs about four extracts, and finishing the brief before sleeping is a coherent plan that `extract:tree_1` first encodes. The scenario would be sharper at fatigue 99, or at night, when ground sleep is twice as effective and the brief cannot be finished in time anyway. One tension the test leaves open on purpose: sleeping is not progress on the brief at all, so it can equally be read as a situation the brief does not cover, which is why `stuck` above 0.5 is accepted as an alternative pass.

## Results

| backend | pass rate | top two per repeat | top-1 = gold | confidence | done | stuck | lost | danger | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **jev** | 100% | `extract:tree_1` > `sleep:ground` x3 | - | 0.70 | 0.02 | 0.31 | 0.06 | 0.05 | 0 |
| gpt-5-nano | 0% | `extract:tree_1` > `move_N` x3 | - | 0.66 | 0.02 | 0.08 | 0.07 | 0.33 | 0 |
| qwen3.7-flash | 100% | `extract:tree_1` > `sleep:ground` x3 | - | 0.95 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| mistral-nemo | 67% | `extract:tree_1` > `sleep:ground` x2, `extract:tree_1` > `move_N` | - | 0.80 | 0.00 | 0.00 | 0.00 | 0.60 | 0 |
| llama-3.1-8b | 67% | `extract:tree_1` > `sleep:ground` x2, `error` | - | 0.79 | 0.00 | 0.00 | 0.00 | 0.50 | 1 |
| gpt-oss-20b | 0% | `extract:tree_1` > `wait` x3 | - | 0.81 | 0.00 | 0.00 | 0.33 | 0.02 | 0 |
| gemini-2.5-flash-lite | 0% | `extract:tree_1` > `wait` x3 | - | 0.82 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano | 0% | `extract:tree_1` > `move_W` x3 | - | 0.63 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-oss-120b | 0% | `extract:tree_1` > `wait` x3 | - | 0.68 | 0.00 | 0.00 | 0.00 | 0.05 | 0 |
| gpt-4.1-mini | 0% | `extract:tree_1` > `wait` x3 | - | 0.70 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gemini-2.5-flash | 100% | `extract:tree_1` > `sleep:ground` x3 | - | 0.91 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-5-nano-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.13 | 0.00 | 0.00 | 0.73 | 0 |
| gpt-4.1-nano-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| gpt-4.1-nano-logprob | 0% | `extract:tree_1` > `move_W` x3 | - | 0.60 | 0.00 | 0.00 | 0.00 | 0.67 | 0 |
| qwen3.7-flash-vote | 0% | `extract:tree_1` x3 | - | 1.00 | 0.00 | 0.00 | 0.00 | 0.40 | 0 |
| qwen3.7-flash-logprob | 100% | `extract:tree_1` > `sleep:ground` x3 | - | 0.95 | 0.00 | 0.04 | 0.00 | 0.55 | 0 |

**What every backend chose (top-1), all repeats together**:

| action | times chosen |
| --- | --- |
| `extract:tree_1` | 47 |
| `error` | 1 |

## Notes

Six backends score anything at all: Jev, qwen3.7-flash, gemini-2.5-flash and qwen under logprob at 100%, mistral-nemo and llama-3.1-8b at 67%, and llama's miss is an errored repeat rather than a wrong answer. All of them pass the same way, `extract:tree_1` first and `sleep:ground` second. The failures are a single shared mistake: the second slot goes to `wait` or a move direction, so fatigue 97 never entered the ranking at all. gpt-oss-20b, gpt-oss-120b, gpt-4.1-mini and gemini-2.5-flash-lite all rank `wait` second; gpt-4.1-nano and gpt-5-nano rank a move. None of them raise `stuck` either, so the second route to a pass goes unused. Elicitation hurts for the same structural reason as case 32: the vote backends return one action and no second place, and qwen falls from 100% in json to 0% under vote while holding 100% under logprob. On Jev, the detail worth keeping is that it is the only backend that combines the pass with a non-trivial `stuck`, at 0.31. It is registering that the brief and the body are in tension without yet calling the stint. Its confidence here is 0.70, the second lowest it records in these eleven scenarios behind only the blocked journey in case 29, which is the right shape for a genuinely close call.
