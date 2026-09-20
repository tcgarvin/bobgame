/**
 * GENERATED FILE - DO NOT EDIT.
 *
 * Written by `tools/generate_rules_ts.py` from `bobgame_rules` (repo root
 * `rules/`), the one definition of the game's rules. Change a rule there and
 * regenerate:
 *
 *     cd tools && uv run python generate_rules_ts.py
 */

/** Terrain floor codes, as stored in a saved map and sent to the viewer. */
export const FloorType = {
  DEEP_WATER: 0,
  SHALLOW_WATER: 1,
  SAND: 2,
  GRASS: 3,
  DIRT: 4,
  MOUNTAIN: 5,
  STONE: 6,
} as const;

export type FloorType = (typeof FloorType)[keyof typeof FloorType];

/** Every object type the world can place; each needs a sprite. */
export const OBJECT_TYPES: ReadonlySet<string> = new Set([
  'anvil',
  'bed',
  'boulder',
  'bush',
  'chair',
  'chest',
  'clay_deposit',
  'copper_vein',
  'door',
  'furnace',
  'iron_vein',
  'item_pile',
  'message_board',
  'reeds',
  'road',
  'rock_large',
  'rock_medium',
  'rock_small',
  'sign',
  'stone_floor',
  'stone_wall',
  'table',
  'tree',
  'wood_floor',
  'wood_wall',
  'workshop_table',
]);

/** Placed buildings: they carry an owner and can be dismantled. */
export const BUILDING_TYPES: ReadonlySet<string> = new Set([
  'anvil',
  'bed',
  'chair',
  'door',
  'furnace',
  'road',
  'sign',
  'stone_floor',
  'stone_wall',
  'table',
  'wood_floor',
  'wood_wall',
  'workshop_table',
]);

/** Ground-layer buildings: they lie under structures and never block. */
export const GROUND_LAYER_TYPES: ReadonlySet<string> = new Set([
  'road',
  'stone_floor',
  'wood_floor',
]);

/** Object types that stop someone walking through (a door stops only wolves). */
export const BLOCKING_TYPES: ReadonlySet<string> = new Set([
  'door',
  'stone_wall',
  'wood_wall',
]);

/** Crafting stations, which hold per-settler craft progress. */
export const STATION_TYPES: ReadonlySet<string> = new Set([
  'anvil',
  'furnace',
  'workshop_table',
]);

/** Natural objects worked with `extract` rather than opened. */
export const EXTRACTABLE_TYPES: ReadonlySet<string> = new Set([
  'boulder',
  'clay_deposit',
  'copper_vein',
  'iron_vein',
  'reeds',
  'rock_large',
  'rock_medium',
  'rock_small',
  'tree',
]);

/** Work units (extract actions) needed to dismantle a building. */
export const DISMANTLE_WORK = 3;

/** Work units for one unit of material from a natural object. */
export const EXTRACT_THRESHOLD = 3;

/** Characters a sign holds on its single line. */
export const SIGN_TEXT_MAX = 80;

/** Fatigue at which a settler counts as tired. */
export const TIRED_FATIGUE = 60;

/** Daytime is the first two thirds of a day; night is the rest. */
export const NIGHT_START_FRACTION = 0.6666666666666666;
