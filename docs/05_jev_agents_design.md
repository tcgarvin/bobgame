# Planner + Jev Agents: Design and Contract

This document is the shared contract for the "settlement" experiment: twelve
LLM-driven actors that hand moment-to-moment control to TypeSafe's Jev model,
cooperating to build a settlement on the big island while wolves roam.

Everything here is authoritative for the tracks working in parallel. If a
track needs to deviate, note it in the "Deviations" section at the bottom.

## Terminology

- **Planner**: a pydantic-ai agent (OpenRouter, `z-ai/glm-5.3-flash`) that
  thinks slowly, remembers, and decides what to do next.
- **Brief**: the natural-language instruction plus typed limits the planner
  hands to Jev.
- **Stint**: the stretch of ticks during which Jev (TypeSafe `jev-latest`)
  holds control and picks one action per tick from a closed set.
- **Stint report**: the compressed log of a stint that goes back to the planner.
- **Actor**: an entity of type `player` controlled by an agent process.
- **Settlement**: the site where all actors spawn and respawn.

## Coordinates and directions

`+x` is east, `+y` is south. Eight directions, matching `proto.Direction`.
"Adjacent" always means Chebyshev distance <= 1 (the 8 neighbours).
"Same or adjacent" means Chebyshev distance <= 1 including the entity's own tile.

## Game mechanics (world/)

### Entity stats

| Field | Player | Wolf |
| --- | --- | --- |
| `max_health` | 20 | 10 |
| `max_hunger` | 100 | n/a (hunger stays at max) |
| base attack damage | 2 | 3 |

- Hunger drops by 1 every tick. At hunger 0 the entity loses 1 health every 2 ticks.
- Health regenerates 1 per 5 ticks while hunger > 50 and health < max.
- Eating one berry restores 20 hunger (capped at max). Eating anything else fails.
- New players spawn with health 20, hunger 80.

### Death and respawn

- Health <= 0 → entity dies. `EntityDied` event. The entity's whole inventory
  is dropped as an `item_pile` at the death position (merged into an existing
  pile there). The entity is removed from the position index and marked
  `alive=false`, `status_bits` bit 0 set. It stays in `all_entities()`.
- Players respawn 10 ticks later at the settlement spawn (nearest free walkable
  tile to the settlement centre), full health, hunger 50, empty inventory,
  nothing wielded. `EntityRespawned` event. Viewer gets `entity_spawned`.
- Wolves do not respawn; they are removed (`entity_despawned` to the viewer).
- Dead entities cannot submit intents (reject with reason `dead`).

### Items (inventory kinds)

`berry`, `wood`, `stone`, `axe`, `pickaxe`, `sword`, `chest`, `message_board`.

### Objects (`WorldObject.object_type`)

| type | blocks movement | notes |
| --- | --- | --- |
| `bush` | no | existing; `berry_count` "0"/"1", regenerates |
| `tree` | yes (existing behaviour, keep) | `remaining` wood (default "4"), `progress` |
| `rock_small`, `rock_medium`, `rock_large`, `boulder` | keep existing walkability | `remaining` stone: 1 / 2 / 4 / 6, `progress` |
| `item_pile` | no | `contents` = JSON `{"kind": count, ...}`; removed when empty |
| `chest` | no | `contents` = JSON `{"kind": count}`; `owner` = entity_id who placed it |
| `message_board` | no | `notes` = JSON list of 20 `{"title": str, "text": str, "author": str, "tick": int}` (empty slots are `null`); `owner` |

All object state values are strings (proto `map<string,string>`); JSON where
noted. Terrain objects that already exist (trees, rocks) get `remaining`
lazily: if the key is absent treat it as the default above.

### Actions

Every intent is processed once per tick in this phase order. A tick context
accepts at most one intent per entity per tick (any type).

1. **Movement** (existing claim-resolve-enact). Wolves move here too.
2. **Attack**: target must be alive and adjacent. Damage = base + wielded bonus
   (`sword` +3, `axe` +2, `pickaxe` +1, other 0). Attacks resolve
   simultaneously against pre-tick positions (after movement). `EntityDamaged`
   event to both parties and anyone in view. Kill → death handling.
3. **Extract**: object must be a tree or rock, same or adjacent tile. Adds
   work: 1 unit bare-handed, 3 units with the matching tool wielded (axe for
   trees, pickaxe for rocks). When `progress` reaches 3 it resets and one
   `wood`/`stone` goes to inventory, `remaining` decrements. At `remaining` 0
   the object is removed (`ObjectRemoved`). Conflicts on the same object:
   both progress; lexicographic entity_id gets the unit if both cross the threshold.
4. **Collect** (existing berries), **Pickup** (from `item_pile` on the current
   tile), **Withdraw** (chest same or adjacent). Conflicts: lexicographic id wins.
5. **Drop**, **Deposit** (chest same or adjacent; chest capacity unlimited).
6. **Craft** (instant, consumes inputs): `axe` = 2 wood + 1 stone;
   `pickaxe` = 2 wood + 2 stone; `sword` = 1 wood + 3 stone; `chest` = 6 wood;
   `message_board` = 4 wood + 1 stone.
7. **Equip**: kind must be in inventory (or `""` to unequip). Wielded items
   stay in inventory.
8. **Place**: `chest` or `message_board` from inventory onto the adjacent tile
   in `direction`; must be walkable, in bounds, and hold no entity and no
   object. Creates the object with `owner`. `ObjectAdded` event.
9. **Write note**: board same or adjacent, slot 0..19, title <= 60 chars,
   text <= 500 chars; empty title and text clears the slot. Anyone may edit
   any slot. `ObjectChanged` event with field `notes`.
10. **Eat** (existing, now affects hunger). **Say**: channel `local` produces
    an `Utterance` event for every entity within 10 tiles (including the
    speaker); channel `thought` produces nothing in observations and is
    forwarded to the viewer only.
11. **Wait**: no-op but counts as a submitted intent.
12. **Regeneration / hunger / wolves / respawn** bookkeeping.

Failed actions produce an `EntityActed{success=false, details=<reason>}` event
for the acting entity. Successful ones produce `EntityActed{success=true}` with
`action_type` in: `attack`, `extract`, `collect`, `pickup`, `withdraw`, `drop`,
`deposit`, `craft`, `equip`, `place`, `write_note`, `eat`, `say`.
`details` is a short human string, for example `"chopped tree_12 (+1 wood)"`.

### Wolves

Simulated inside the world server (no gRPC). Every 40 ticks, if fewer than 3
wolves exist, spawn one on a walkable tile that is at least 20 and at most 40
tiles (Chebyshev) from the nearest living player. Wolf ids are `wolf_<n>`.

Behaviour each tick (deterministic, seeded RNG on the world):

- If a living player is within 8 tiles: move one step toward the nearest one
  (greedy with a fallback to any walkable neighbour). If adjacent, attack
  instead of moving.
- Otherwise wander randomly, moving on 50% of ticks.
- If more than 50 tiles from every living player: despawn.

Wolves have hunger fixed at max and never starve.

### Settlement site

`world/src/world/settlement.py` picks, deterministically for a given map, a
walkable grass or dirt tile such that within a 30-tile radius there are at
least 15 trees, 15 rocks (any size), 8 bushes, and at least one water tile
(shallow or deep), preferring sites with the most balanced supply. The chosen
centre is logged and stored on the World (`world.settlement: Position`).

Config: `[world] spawn_mode = "settlement"` makes every configured entity
spawn on the nearest free walkable tile to the settlement centre, ignoring the
entity's configured x/y. Respawns use the same rule.

### Tick timing

`[world] tick_duration_ms` and new `[world] intent_deadline_ms` in the TOML.
The settlement config uses 2000 / 1200 for bad connections; tighten later.

## Observation (world → agent)

- View radius 8 (17x17 tiles) for tiles and objects. Visible entities: those
  within radius 8 only (no longer everyone).
- `self` and `visible_entities` carry health, max_health, hunger, max_hunger,
  wielded, alive.
- `events` holds the previous tick's events that this entity could see: moves
  and actions of entities within radius, utterances within 10 tiles, damage,
  deaths, respawns, object added/removed/changed within radius, and this
  entity's own action results (always).

## Viewer WebSocket messages (world → viewer)

Existing messages are unchanged unless noted.

- `snapshot`: add `settlement: {x, y}`.
- `tick_completed`: add
  - `entity_updates`: list of `{entity_id, position, entity_type, health,
    max_health, hunger, max_hunger, wielded, alive, inventory: {kind: count}}`
    for every entity, every tick.
  - `actions`: list of `{entity_id, action_type, success, details}`.
  - `utterances`: list of `{speaker_id, channel, text, position}` (both channels).
  - `objects_added`: list of ObjectState; `objects_removed`: list of object ids.
- `entity_spawned` / `entity_despawned`: emitted for wolves and respawns; the
  chunk manager is kept in sync.
- New `agent_status`: `{type: "agent_status", entity_id, mode, brief,
  planner_thought, stint: <parsed stint_json or null>}` whenever an agent
  calls `AgentStatusService.ReportStatus`.

## Agents package (agents/)

Module `agents.jev_agent`, run as
`uv run python -m agents.jev_agent --entity <id> [--server host:port]`.

Environment: `TYPESAFE_API_KEY`, `OPENROUTER_API_KEY` (both in the repo `.env`;
the runner passes the environment through). Optional `PLANNER_MODEL`
(default `z-ai/glm-5.3-flash`), `JEV_MODEL` (default `jev-latest`).

### Process structure

An asyncio loop consumes the observation stream. Every tick exactly one intent
is submitted before the deadline:

- **Stint mode**: build Jev state, ask Jev, map the answer to an intent.
- **Planning mode**: submit `WaitIntent` (or `SayIntent` on channel `thought`
  when the planner has produced new text) while the planner runs in a
  background task. The planner never blocks the tick loop.

The agent renews its lease every 10 seconds and reports `AgentStatusReport`
whenever mode, brief, planner thought, or the last Jev decision changes (at
most once per tick).

### World model (agent-side memory)

`WorldModel` keeps everything the agent has ever seen: tiles (walkability and
floor type), objects by id with last-seen tick, entities with last-seen tick
and position, the settlement position, and the agent's own history. Only
tiles that have been observed are known; pathfinding treats unknown tiles as
passable at a higher cost so exploration works.

### Pathfinding tool

A* over the world model with 8-connected moves and the same diagonal-blocking
rule the world uses. Provides `next_step(from, to) -> Direction | None` and
`path_length`. A "travel" is code-owned: it holds a target and each tick
proposes the next step. Jev decides whether to keep following it.

### Stint (Jev executor)

Every tick during a stint (Jev is asked every tick; the planner can set
`check_every` to 2 to ask every other tick and repeat the last action between),
code builds a compact JSON state (target well under 8k tokens):

```
{
  "brief": {"instruction": str, "success_condition": str, "ticks_left": int},
  "self": {"position": [x,y], "health": "14/20", "hunger": "35/100",
           "wielded": "axe", "inventory": {"wood": 3}},
  "settlement": {"dx": -12, "dy": 4},
  "travel": {"target": "tree_9 at dx 3 dy -2", "next_step": "NE", "steps_left": 4} | null,
  "nearby": [ {"id": "tree_9", "type": "tree", "dx": 3, "dy": -2, "remaining": 4}, ... up to ~25, nearest first ],
  "entities": [ {"id": "wolf_1", "type": "wolf", "dx": -2, "dy": 0, "health": "10/10"}, ... ],
  "map": "17 lines of 17 chars: . walkable, # blocked, ~ water, T tree, o rock, b bush, C chest, B board, i item pile, @ self, P player, W wolf, ? unknown",
  "recent": ["t41 move E ok", "t42 extract tree_9 ok (+1 wood)", "t43 attack wolf_1 failed: not adjacent", ... last 8],
  "notes": "free-text hints from the planner, e.g. 'wolves are dangerous below 8 health'"
}
```

Questions in one Jev request:

- `action` (Choice): the legal actions this tick, enumerated by code. Options
  are strings such as `move_N`, `move_NE`, `follow_travel`, `stop_travel`,
  `travel_to:tree_9`, `extract:tree_9`, `collect:bush_3`, `attack:wolf_1`,
  `eat:berry`, `pickup:wood`, `deposit:chest_2:wood`, `withdraw:chest_2:berry`,
  `craft:axe`, `equip:axe`, `place:chest:N`, `say:help`, `say:wolf_here`,
  `say:come_here`, `say:all_good`, `wait`. Only include options that are
  currently legal (walkable directions, adjacent objects, affordable recipes,
  items in inventory). Cap at 40 options; keep `travel_to:` options to the 6
  nearest relevant objects. Each option's criteria text is a one-line
  description.
- `eject` (Noul): "Should control return to the planner now, because the
  brief's success condition is met, the brief has become impossible, or the
  situation needs judgement the brief does not cover?"
- `danger` (Noul): "Is this entity in immediate danger of dying within a few
  ticks?" (used for logging and for a hard rule: if danger > 0.8 and health
  < 6, prefer moving away from the nearest wolf toward the settlement).

Code rules on top of Jev: the stint ends when `eject` >= 0.7 for two
consecutive ticks, when `ticks_left` hits 0, on death, or when the same failed
action repeats 3 times. The final stint report says which.

Per-tick log entry (JSONL, `logs/agent-<id>/stints.jsonl`): tick, position,
stats, state size in tokens (as reported by Jev usage), options count, chosen
action, top-3 probabilities, eject, danger, latency ms, intent result.

### Stint report (code, no LLM)

Compress a stint into ~20 lines: brief, ticks used, why it ended, start and
end position and stats, inventory delta, actions grouped and counted with
successes and failures, notable events (damage, deaths, utterances heard,
objects discovered), and the last 5 raw tick lines.

### Planner (pydantic-ai)

System prompt explains the world, the action costs, the tools, and that Jev
is fast, cheap, and literal, while the planner is slow and expensive. Tools:

- `look()` → world summary from the world model: stats, inventory, settlement
  offset, known objects grouped by type with nearest examples, known chests
  and boards (with contents / notes), visible entities, recent events.
- `start_stint(instruction, success_condition, max_ticks, notes="", check_every=1)`
  → runs the stint to completion and returns the stint report. This is the
  main tool. The tool call resolves only when the stint ends.
- `travel_to(x, y, max_ticks)` → a stint with a preset travel target and a
  brief of "follow the path; react to danger; eject on arrival".
- Direct single-tick actions (each waits one tick and returns the result):
  `move(direction)`, `attack(entity_id)`, `extract(object_id)`, `collect(object_id)`,
  `eat(kind)`, `pickup(kind, amount)`, `drop(kind, amount)`,
  `deposit(object_id, kind, amount)`, `withdraw(object_id, kind, amount)`,
  `craft(recipe)`, `equip(kind)`, `place(kind, direction)`, `wait(ticks)`.
- `say(text)` → local speech (free text).
- `write_note(board_id, slot, title, text)` and `read_board(board_id)`.
- `remember(text)` / `recall()` → the agent's persistent notes file.

The planner loop: each planner turn gets the latest `look()` and the last
stint report in the prompt, thinks, calls tools, and ends its turn with a
one-paragraph reflection which is stored as `planner_thought` and shown in
the viewer. Then the next turn starts immediately. Cap tool calls per turn at
12. Conversation history is trimmed to the last ~20 messages plus the
persistent notes.

Persistent memory: `logs/agent-<id>/memory.md` (planner notes) and the last 10
stint reports.

Shared narrative: the system prompt tells every actor they are one of twelve
settlers whose shared goal is to build a settlement at the spawn site:
gather wood and stone, craft tools, craft and place a chest and a message
board, keep everyone fed, and defend against wolves. Use the board to
coordinate.

### Tests

Unit tests with a fake Jev client (returns scripted answers) covering:
option enumeration, state building and map rendering, stint termination
rules, stint report content, pathfinding, world model updates from
observations. Planner tests mock the model with pydantic-ai's `TestModel`.

## Viewer (viewer/)

- Render new object types: `chest`, `message_board`, `item_pile`, rocks
  (all sizes), and `wolf` entities. Add sprite keys to the Tiled TSX files
  and regenerate `sprite-index.json`.
- Health bar above each entity; hunger as a thin second bar for players.
- Entity picker: a dropdown (HTML overlay) listing entities; selecting one
  makes the camera follow it. Clicking a sprite also selects it. `F` still
  toggles follow. Key `0` jumps to the settlement.
- Agent panel (HTML overlay on the right): for the selected entity show mode,
  brief, planner thought, last Jev decision with the top probabilities and the
  eject/danger values, stats, inventory, wielded item, and the last 10
  actions/utterances. Updated from `entity_updates`, `actions`, `utterances`,
  and `agent_status`.
- Speech bubbles: `local` utterances appear above the speaker for 3 seconds;
  `thought` utterances appear only in the panel.
- Damage flash on `EntityDamaged`-derived data (`entity_updates` health drop).

## Runner and scripts

- `runner/configs/settlement.toml`: default agent module `agents.jev_agent`.
- `world/configs/settlement.toml`: loads `saves/island.npz`, `spawn_mode =
  "settlement"`, 12 entities, tick 2000 / deadline 1200.
- `dev.sh <config>` uses `runner/configs/<config>.toml` when it exists,
  otherwise `foraging.toml`.

## File ownership (parallel tracks)

| Track | Owns |
| --- | --- |
| World mechanics | `world/src/world/{types,state,tick,foraging,conversion,exceptions}.py`, new `items.py`, `crafting.py`, `combat.py`, `containers.py`, `stats.py`, `wolves.py`, `services/action_service.py`, world tests for these |
| World services | `world/src/world/{config,server,settlement}.py`, `services/{observation_service,viewer_ws_service,status_service,__init__}.py`, `chunks.py`, tests for these |
| Agents | everything under `agents/` except the generated proto files |
| Viewer | everything under `viewer/`, the TSX files under `assets/`, `tools/generate_sprite_index.py` |
| Lead | `proto/`, `docs/`, `runner/`, `dev.sh`, root `CLAUDE.md` |

The world mechanics track must expose on `TickContext` a
`submit_<kind>_intent` method for every intent type and a single
`submit_intent(entity_id, intent)` dispatcher that the action service and the
wolf simulation both use. `TickResult` must carry every event needed by the
observation and viewer services: `move_results`, `action_results`
(`entity_id, action_type, success, details`), `damage_events`, `deaths`,
`respawns`, `utterances`, `object_changes`, `objects_added`, `objects_removed`,
`entities_spawned`, `entities_despawned`.

## Deviations

(Tracks append here.)
