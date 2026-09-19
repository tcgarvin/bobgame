# 08 - Building a proper settlement

Status: contract. This document is the source of truth for the building
mechanics; `world/src/world/items.py` is the same contract in code. If the two
disagree, fix both in one change.

Goal: give the settlers what they need to turn a camp into a settlement -
roads, walls, doors, floors, beds, chairs, tables and a workshop table - and a
map that holds enough raw material near the settlement site to build it.

## Materials

| Item | Source | Tool that speeds it up |
|------|--------|------------------------|
| `wood` | `tree` (4 units) | `axe` |
| `stone` | `rock_small/medium/large`, `boulder` (1/2/4/6) | `pickaxe` |
| `fiber` | `reeds` (3 units), grows at the water's edge | none |
| `clay` | `clay_deposit` (6 units), river banks and lake shores | `pickaxe` |

`reeds` and `clay_deposit` are new natural object types placed by the terrain
generator. They use the ordinary `extract` action and the same
work/threshold rules as trees and rocks. Nothing regrows (berries still do).

## Recipes

> **Superseded:** the full recipe table now lives in
> [docs/10_metal_and_sleep.md](10_metal_and_sleep.md) (section 2, "Recipes"),
> which adds a `station` and a `work` count to every recipe and replaces
> `needs_workshop` with `station == "workshop_table"`. The table below is kept
> for the history of this milestone; docs/10 is the source of truth.

A recipe consumes inputs and yields `output_count` units of its item. Recipes
marked **workshop** only succeed while the crafter stands on or next to
(8-neighbourhood) a placed `workshop_table`; otherwise the craft fails with
`"<recipe> needs a workshop table nearby"`.

| Recipe | Inputs | Yields | Where |
|--------|--------|--------|-------|
| `axe` | 2 wood + 1 stone | 1 | hand |
| `pickaxe` | 2 wood + 2 stone | 1 | hand |
| `sword` | 1 wood + 3 stone | 1 | hand |
| `chest` | 6 wood | 1 | hand |
| `message_board` | 4 wood + 1 stone | 1 | hand |
| `plank` | 1 wood | 2 | hand |
| `rope` | 2 fiber | 1 | hand |
| `road` | 2 stone | 4 | hand |
| `wood_wall` | 2 plank | 1 | hand |
| `wood_floor` | 1 plank | 2 | hand |
| `workshop_table` | 4 plank + 2 stone | 1 | hand |
| `stone_wall` | 2 stone + 1 clay | 1 | workshop |
| `stone_floor` | 1 stone + 1 clay | 2 | workshop |
| `door` | 3 plank + 1 rope | 1 | workshop |
| `bed` | 4 plank + 3 fiber | 1 | workshop |
| `chair` | 2 plank | 1 | workshop |
| `table` | 4 plank | 1 | workshop |

Every crafted building item is placeable; the placed object's `object_type`
equals the item kind.

## Layers and placement

Each tile has two object layers:

- **ground layer**: `road`, `wood_floor`, `stone_floor`. Never blocks.
- **structure layer**: everything else (natural objects, chests, boards, walls,
  doors, furniture, item piles).

Placement rules (`PlaceIntent`):

- A tile holds at most one ground-layer object and at most one structure-layer
  object. A structure may stand on a ground object (a bed on a floor).
- Ground kinds may not be placed on a tile that holds a natural object
  (tree, rock, bush, reeds, clay deposit). They may go under anything else.
- Ground kinds may be placed on the placer's own tile with
  `direction = DIRECTION_UNSPECIFIED`. Structure kinds always need a direction.
- The target must be walkable terrain, in bounds. Structure kinds additionally
  need the tile free of entities (existing rule).
- Placed objects carry `owner=<entity_id>` state, as chests do today.

## Blocking

Until now no object blocked movement. New rule: an object type in
`BLOCKING_OBJECT_TYPES` (`wood_wall`, `stone_wall`) makes its tile impassable
for everyone. A `door` is impassable for wolves and passable for everyone else
(`WOLF_BLOCKING_OBJECT_TYPES` = walls + door). Trees, rocks and furniture still
do not block.

- The world owns this: `World` keeps an index of blocking positions, updated in
  `add_object` / `remove_object`, and movement validation (including the
  diagonal corner rule), wolf movement, spawn and respawn placement all respect
  it. A ring of walls with a door is a wolf-proof room.
- Observations report `Tile.walkable = false` for wall tiles, so agent path
  finding needs no new concept. Door tiles stay `walkable = true`.
- The viewer keeps drawing terrain from chunks; walls are ordinary objects.

## Dismantling

`ExtractIntent` on a placed building object (any kind in `BUILDING_KINDS`)
dismantles it: `DISMANTLE_WORK` (3) work units, one per action, no tool bonus,
progress kept in object state like extraction progress. When complete the
object is removed (`ObjectRemoved`) and one unit of the item returns to the
dismantler's inventory. Anyone may dismantle anything. Chests and message
boards stay permanent for now.

## Resting

New intent `RestIntent { string object_id = 1; }` (`Intent.rest = 17`). The
entity must stand on or next to a `bed`. One rester per bed per tick
(lexicographically smallest entity id wins; the others fail with
`"<bed> is taken"`). A successful rest heals `REST_HEAL` (2) health, capped at
max health, and requires food > 0. Action event type: `"rest"`.

## Roads, floors, chairs and tables

Cosmetic for now. They are recorded, replayed and rendered, and they count in
the run analysis, but have no mechanical effect. Ideas for later: road travel
speed, eating at a table, sheltered floor tiles, bed as respawn point, workshop
tiers.

## Map and settlement site

The generator places `reeds` and `clay_deposit`, and clusters trees into groves
and rocks into outcrops so the landscape reads as places rather than noise. The
settlement site finder requires, within `SETTLEMENT_RADIUS` (30) of the centre:

| Resource | Minimum |
|----------|---------|
| trees | 120 |
| rocks | 50 |
| bushes | 12 |
| reeds | 25 |
| clay deposits | 12 |
| water | at least one tile |

and a mostly clear, buildable area (grass or dirt, few natural objects) of at
least 15 x 15 tiles around the centre. `saves/island.npz` is regenerated; old
runs stay replayable because recordings carry their own objects.

## Agents

- `agents/jev_agent/options.py` mirrors the recipe table, offers craft options
  only when they can succeed (inputs and workshop proximity), offers place,
  rest and dismantle options.
- The planner prompt teaches the material chain and what a settlement looks
  like. Because Jev cannot reason about coordinates tick by tick, the planner
  gets a deterministic `build` tool in the spirit of `travel_to`: given a kind
  and a line or rectangle outline, code walks the entity around and places the
  pieces, stopping when it runs out of items or is interrupted by danger.
  A build called with none of the pieces in the pack is not started at all:
  the result says how many the shape needs and the recipe scaled to that
  count (`craft wood_wall 16 times (2 plank each, by hand): 32 plank in all`),
  and a build that runs dry ends its report with `carrying now` and
  `short by` lines of the same form (`BuildExecutor._supply_lines`,
  `supply_text`, `missing_pieces_text` in `jev_agent/build.py`).

## Viewer

New sprites from the DawnLike tileset, keyed in the TSX files and mapped in
`OBJECT_SPRITE_MAP`: `reeds`, `clay-deposit`, `road`, `wood-wall`,
`stone-wall`, `wood-floor`, `stone-floor`, `door`, `bed`, `chair`, `table`,
`workshop-table`. Ground-layer objects render below structure-layer objects,
which render below entities. The object inspector shows the new types.

## Analysis

`tools/analyze_run.py` counts placements by kind, dismantles, rests and
workshop crafts, and adds notable moments for the first workshop table, the
first door and the first bed.
