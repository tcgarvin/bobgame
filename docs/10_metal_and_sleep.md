# 10 - Stations, the metal tier, and sleep

Status: contract. The numbers and tables below live in one place in code: the
dependency-free `bobgame_rules` package at the repo root (`rules/`). The world
(`world/src/world/items.py`, `crafting.py`, `sleep.py`, `stats.py`) and the
agents (`agents/src/agents/jev_agent/items.py`) both import from it and
re-export under their own names, so there is nothing to keep in step by hand;
the viewer reads the same definitions through
`tools/generate_rules_ts.py`. Change a number in `rules/`. Builds on docs/08
(building) and docs/05 (settlement mechanics).

Goal: deepen the crafting tree (five levels instead of two), make some work take
time and a place, give settlers a reason to travel for ore, and add a body clock
so that a settlement needs shelter and beds without making early survival
impossible. Food stays as it is (berries) until farming exists.

All agent-facing text follows "tools, not rules": describe what a mechanic does
and its numbers, never when to use it.

## 1. Stations and work

`Recipe` gains two fields:

- `station: str` - `""` for hand crafting, otherwise an object type that must be
  placed on or next to (8-neighbourhood) the crafter: `workshop_table`,
  `furnace` or `anvil`. `needs_workshop` is replaced by `station == "workshop_table"`.
  Failure detail: `"<recipe> needs a <station> nearby"`.
- `work: int` (default 1) - craft actions needed. Hand recipes are always
  `work == 1` and complete instantly, as today. A station recipe with `work > 1`
  keeps progress in the **station object's** state under the key
  `craft:<entity_id>` with value `<recipe>:<done>`. Each successful craft action
  with the inputs present adds one; at `done == work` the inputs are consumed,
  the output is added and the key is removed. Inputs are checked on every action
  (so a settler cannot start a smelt and give the ore away) but only consumed on
  completion. Crafting a different recipe at the same station resets that
  settler's progress. Progress survives the settler walking away and coming
  back; it is per settler and per station.

Action event details: instant crafts keep `"crafted <kind>"` (`" xN"` for
multi-output). Multi-tick crafts report `"<recipe> 2/4"` on progress and
`"crafted <kind>"` on completion. Analysis greps for `"crafted "`.

Every station is a placeable building item (`BUILDING_KINDS`), structure layer,
does not block, dismantles like any building (returns the item).

## 2. Materials and the metal tier

New natural objects placed by the terrain generator (see section 5):

| Object | Units | Yields | Requires wielding |
|--------|-------|--------|-------------------|
| `copper_vein` | 4 | `copper_ore` | `pickaxe`, `copper_pickaxe` or `iron_pickaxe` |
| `iron_vein` | 4 | `iron_ore` | `copper_pickaxe` or `iron_pickaxe` |

Extracting a vein without an acceptable tool fails with
`"<object> needs a <tool list>"`. Trees, rocks, reeds and clay still work bare.

### Tools and tiers

`EXTRACT_TOOL` becomes `EXTRACT_TOOLS: object_type -> frozenset of tool kinds`
that speed the extraction. `EXTRACT_WORK_BY_TOOL` gives the work units one
action adds when wielding that tool:

| Tool | Work per action | Damage bonus |
|------|-----------------|--------------|
| bare hands | 1 | 0 |
| `axe`, `pickaxe` | 3 | 2, 1 |
| `copper_axe`, `copper_pickaxe` | 4 | 2, 1 |
| `iron_axe`, `iron_pickaxe` | 5 | 3, 2 |
| `sword` | - | 3 |
| `iron_sword` | - | 5 |

There is no copper sword: copper improves gathering, iron improves fighting.
An `iron_sword` deals 7 per hit, so a lone iron-armed settler kills a wolf in
three hits and takes 9 damage. That is deliberate: it is the reward at the end
of a chain that needs a workshop, a furnace, an ore trip, charcoal and an
anvil. Everything below iron keeps the docs/05 balance where nobody wins alone.

Axes still speed trees, pickaxes still speed rocks and clay; the tier only
changes work per action. `EXTRACT_THRESHOLD` stays 3.

### Recipes

The full table replaces the one in docs/08 (unchanged rows kept for reference).

| Recipe | Inputs | Yields | Station | Work |
|--------|--------|--------|---------|------|
| `axe` | 2 wood + 1 stone | 1 | hand | 1 |
| `pickaxe` | 2 wood + 2 stone | 1 | hand | 1 |
| `sword` | 1 wood + 3 stone | 1 | hand | 1 |
| `chest` | 6 wood | 1 | hand | 1 |
| `message_board` | 4 wood + 1 stone | 1 | hand | 1 |
| `plank` | 1 wood | 2 | hand | 1 |
| `rope` | 2 fiber | 1 | hand | 1 |
| `road` | 2 stone | 4 | hand | 1 |
| `wood_wall` | 2 plank | 1 | hand | 1 |
| `wood_floor` | 1 plank | 2 | hand | 1 |
| `workshop_table` | 4 plank + 2 stone | 1 | hand | 1 |
| `stone_wall` | 2 stone + 1 clay | 1 | workshop_table | 1 |
| `stone_floor` | 1 stone + 1 clay | 2 | workshop_table | 1 |
| `door` | 3 plank + 1 rope | 1 | workshop_table | 1 |
| `bed` | 4 plank + 3 fiber | 1 | workshop_table | 1 |
| `chair` | 2 plank | 1 | workshop_table | 1 |
| `table` | 4 plank | 1 | workshop_table | 1 |
| `furnace` | 8 stone + 4 clay | 1 | workshop_table | 3 |
| `charcoal` | 3 wood | 2 | furnace | 2 |
| `copper_ingot` | 2 copper_ore + 1 charcoal | 1 | furnace | 3 |
| `iron_ingot` | 2 iron_ore + 2 charcoal | 1 | furnace | 4 |
| `copper_axe` | 2 plank + 2 copper_ingot | 1 | workshop_table | 2 |
| `copper_pickaxe` | 2 plank + 2 copper_ingot | 1 | workshop_table | 2 |
| `anvil` | 5 iron_ingot + 2 stone | 1 | workshop_table | 4 |
| `iron_axe` | 2 plank + 2 iron_ingot | 1 | anvil | 2 |
| `iron_pickaxe` | 2 plank + 2 iron_ingot | 1 | anvil | 2 |
| `iron_sword` | 1 plank + 3 iron_ingot | 1 | anvil | 3 |

New item kinds: `copper_ore`, `iron_ore`, `charcoal`, `copper_ingot`,
`iron_ingot`, `copper_axe`, `copper_pickaxe`, `iron_axe`, `iron_pickaxe`,
`iron_sword`, `furnace`, `anvil`. New wieldable kinds: the five metal tools.

## 3. The day

The world has a clock. `day_length_ticks` is a world config value (settlement:
300, so a day is 10 minutes at 2 s ticks; default 300). `tick_of_day =
tick % day_length`, `day = tick // day_length`. The first `NIGHT_START_FRACTION`
(2/3) of a day is daytime, the rest is night: with 300 ticks, ticks 0-199 are
day and 200-299 night.

The clock is reported everywhere the tick is:

- `Observation.clock` (`WorldClock { int64 day; int32 tick_of_day; int32
  day_length; bool night; }`).
- The viewer WebSocket tick payload gets `clock: {day, tick_of_day, day_length,
  night}` and the recording's per-tick record carries the same, so replay shows
  it.
- `meta.json` records `day_length_ticks`.

Nothing else changes with the light for now (vision, wolves). Later options:
shorter sight at night, wolves bolder at night.

## 4. Fatigue and sleep

### Fatigue

Players (not wolves) have `fatigue` (0 = fresh) and `max_fatigue` (100), on the
`Entity` message (`fatigue = 13`, `max_fatigue = 14`, `asleep = 15`). New
players spawn at 0; respawn sets `RESPAWN_FATIGUE` (30).

While awake, fatigue rises 1 point every `FATIGUE_INTERVAL_DAY` (4) ticks by
day and every `FATIGUE_INTERVAL_NIGHT` (3) ticks at night. A full day awake
costs 50 and a sleepless night adds 33, so a settler who never sleeps is tired
from its first night and collapses around the middle of its second day. (The
first live run used 3/2 and half the settlers collapsed on night one.)

| Fatigue | State | Effect |
|---------|-------|--------|
| 0-59 | fresh | none |
| 60-99 | tired | extraction work per action is halved (integer, minimum 1, so bare-handed work and dismantling are unaffected); attack damage is 1 less (minimum 1); no health regeneration; multi-tick crafting progress is unaffected |
| 100 | exhausted | collapses: falls asleep where it stands (ground rules), and **damage does not wake it**. Wakes when fatigue drops to `COLLAPSE_WAKE_FATIGUE` (70) or below |

### Sleeping

New intents: `SleepIntent { string object_id = 1; }` (`Intent.sleep = 20`) and
`WakeIntent {}` (`Intent.wake = 21`). `object_id` names a `bed` on or next to
the sleeper; empty means sleeping on the ground where the entity stands.
One sleeper per bed (lexicographically smallest entity id wins on the same
tick; a bed already occupied by a sleeper fails with `"<bed> is taken"`).
Sleeping needs food above `HUNGRY_WAKE_FOOD` (20) and fatigue at least
`MIN_SLEEP_FATIGUE` (20). Below the food line the refusal names both numbers:
`"too hungry to sleep: food F, and a sleeper wakes at food 20"`. Below the
fatigue line it says `"not tired enough to sleep: fatigue F, and sleep needs
fatigue 20"`.

**Changed 2026-09-20 (hamlet round 4).** The fatigue floor used to be 1; one
settler took ten sleeps of one or two ticks at fatigue 1. `MIN_SLEEP_FATIGUE`
gates the `SleepIntent` only: collapse still happens at `max_fatigue` whatever
it says, and Jev's `sleep:` options are withheld below it
(`agents/.../options/`, `items.MIN_SLEEP_FATIGUE`).

**Changed 2026-09-20 (hamlet round 2).** The line used to be food 0 both
ways. A settler called `sleep` at food 39 and health 2, the world kept him
asleep for 156 ticks because only food 0 woke a sleeper, and he died four
ticks after waking. One number now reads both ways: you cannot lie down at or
below 20 food, and a sleeper that falls to 20 is woken.

Sleep is a state, not an action: `Entity.asleep` stays true across ticks. While
asleep, every intent other than `wake` fails with `"asleep"` and the entity
does not move. The world stores where it sleeps (`bed` object id or `""`).

Fatigue recovery while asleep (`recovery_rate(on_bed, night)` returns
`(points, ticks)`: that many points shed every that many ticks):

| Where | Night | Day |
|-------|-------|-----|
| bed | 1 per tick (1.00/tick) | 2 per 3 ticks (0.67/tick) |
| ground | 2 per 3 ticks (0.67/tick) | 1 per 2 ticks (0.50/tick) |

A bed is strictly faster than the ground in both periods, and night is
strictly faster than day in both places.

The ground rates were raised on 2026-09-20 (Hamlet-run fixes, round 3): at the
old 1-per-2/1-per-4 a bedless settler had to sleep about 233 of a day's 300
ticks to clear the ~83 fatigue a day costs, and lost ~50 food doing it. Round 4
raised the day rates again — ground day 1 per 3 -> 1 per 2, and bed day 1 per 2
-> 2 per 3 to keep the bed ahead of it — because 31-45% of all settler-ticks
were still spent asleep and 70% of those were in daylight, finishing at the
slow day rate a debt the 100-tick night could not clear.

A bed sleeper also heals 1 health every `REGEN_INTERVAL_TICKS` (5) regardless
of food and fatigue. A ground sleeper heals only through the ordinary regen
rules. Food drops at the normal rate while asleep.

Waking: a voluntary sleeper wakes when fatigue reaches 0, when it takes any
damage, when its food falls to `HUNGRY_WAKE_FOOD` (20), when the bed it sleeps
on is removed, or on `WakeIntent`. Waking ends the sleep, so the hunger wake
happens at most once per sleep. A collapsed sleeper wakes only at fatigue 70 or on bed removal
(it never was on a bed). Waking is reported as an action event
`("wake", true, "woke up: <reason>")` where reason is one of `rested`,
`damaged`, `hungry`, `bed removed`, `asked`. Falling asleep is
`("sleep", true, "asleep on <bed id|the ground>")` and collapse is
`("collapse", true, "collapsed from exhaustion")`.

**The new moon is the one exception**, on both sides. On a new-moon night
(contract: [docs/14_new_moon_and_saves.md](14_new_moon_and_saves.md), section
1) the world puts every awake settler to sleep where it stands at
`night_start_tick`, in a free adjacent bed if there is one and on the ground
otherwise, regardless of `MIN_SLEEP_FATIGUE` and `HUNGRY_WAKE_FOOD`; it is a
sleep, not a collapse. For the `NEW_MOON_STILL_TICKS` (6) ticks that follow,
nothing wakes a sleeper — not rest, damage, hunger or a dismantled bed — and a
`WakeIntent` is refused with `"new moon"`. After the still window the ordinary
rules above apply again, so a rested or hungry settler gets up then.

`RestIntent` (the instant 2-health rest on a bed) stays as it is.

Tick order: sleep and wake intents are processed after the movement and action
phases; the fatigue phase runs next to the food phase (accumulate for awake
players, recover for sleepers, collapse checks, automatic wakes).

### Viewer

- A day/night tint over the map scaled by `tick_of_day` (clear by day, a dusk
  ramp over the last 10% of daytime, dark blue at night) plus a small clock
  readout in the overlay (`day 2, 143/300, night`).
- Sleeping entities show a "z" marker above the sprite; collapsed ones show it
  in red.
- The agent panel shows fatigue next to health and food.
- New sprites (DawnLike, keyed in the TSX files, mapped in `OBJECT_SPRITE_MAP`):
  `copper-vein`, `iron-vein`, `furnace`, `anvil`. The object inspector shows
  vein units remaining and station craft progress.

## 5. Map

The generator places `copper_vein` and `iron_vein` inside rock outcrops on
higher ground (hills/slopes), never within `ORE_EXCLUSION_RADIUS` (60) of the
settlement site, so that ore is a trip. Targets for the 4000x4000 island: about
150 copper veins and 80 iron veins, in clusters of 3-8. The site finder adds a
requirement: at least 3 copper veins and 2 iron veins within `ORE_SEARCH_RADIUS`
(200) of the centre. `saves/island.npz` is regenerated; old runs stay replayable.

How the two rules are made consistent, given that the site is chosen from the
finished map while the exclusion is defined against the chosen site: the
generator places veins, then runs `terrain.objects.fit_ore_to_settlement`, which

1. chooses the site with the ordinary resource requirements only
   (`settlement.select_site(..., require_ore=False)`);
2. adds one copper and one iron cluster in the ring 80-150 tiles from the site
   when the random placement left it short of the requirement - the island's
   ore districts are sparse enough (about 27 copper and 15 iron clusters) that a
   site chosen for wood, stone, reeds and clay rarely has both metals in reach;
3. deletes every vein within 60 tiles (Chebyshev) of the site;
4. re-runs the finder **with** the ore requirement and fails loudly if the site
   moved.

That works because the requirement counts veins in the **ring** between the
exclusion and search radii rather than the whole disc, and because veins feed
neither the buildable mask nor the site score: adding ore in the ring and
deleting ore inside the exclusion radius cannot change which tile wins, so the
world picks the same site again when it loads the saved map. A map with no
qualifying site at all (a small test island) keeps its veins untouched.

Vein placement is driven by `outcrop_field` x `highland_field` (ridged noise,
slope or nearness to mountains) x a slow "ore bearing" noise, so most outcrops
are barren and the metal sits in a few inland districts; low coastal ground gets
almost none. Cluster seeds are at least `vein_cluster_spacing` (60) apart and a
cluster takes the best tiles within `vein_cluster_radius` (5) of its seed.
Densities are configured per million tiles (`copper_vein_density` 9.4,
`iron_vein_density` 5.0), so smaller maps scale down.

With seed 12345 the island carries 151 copper and 87 iron veins, the settlement
site is (1539, 974), and the nearest copper and iron are 187 and 121 tiles away.

`tools/visualize_world.py` colours veins distinctly.

## 6. Agents

- `jev_agent/items.py` re-exports the kinds, recipes (with station and work),
  tool tiers and vein tool requirements from `bobgame_rules`, the same package
  the world reads, and adds only the agent-side prose around them.
- Options: craft options are offered when inputs are present and the station is
  adjacent; extraction of veins only with an acceptable tool; `sleep` on the
  ground and on an adjacent bed (offered whenever fatigue > 0, with the
  recovery numbers in the description); `wake` only while asleep. Jev state
  gains `fatigue` (number and word: fresh/tired/exhausted), `asleep`, and the
  clock (`day`, `tick_of_day`, `night`).
- While the entity is asleep the agent does not call Jev or the planner: it
  waits for the observation to show `asleep == false`, then resumes. The
  reflex brief cannot fire while asleep; a bite wakes the settler and then the
  reflex fires as usual.
- Planner: `craft(recipe)` submits craft actions until the recipe completes or
  fails, reporting progress; `sleep(where)` submits the intent and returns when
  the settler wakes (or at the tool budget), reporting the wake reason and the
  fatigue; the system prompt gains the physics of fatigue, the day, sleep, the
  stations, the veins and the tool tiers, with numbers, following docs/05
  "tools, not rules". The prompt's recipe table is generated from `items.py`.
- Every planner tool result shows the clock next to the tick.

## 7. Analysis

`tools/analyze_run.py` counts: smelts (charcoal, ingots), stations placed
(furnace, anvil), metal tools crafted, vein extractions, sleeps by kind (bed vs
ground), collapses, and wakes by reason. Notable moments: first furnace, first
ingot, first iron tool, first collapse, first death while asleep. The run
summary reports how many settlers were asleep at each night's midpoint.
