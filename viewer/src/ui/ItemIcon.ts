/**
 * Item icons for the HTML overlay, drawn from the same sprite index the Phaser
 * scenes use (`viewer/public/assets/sprite-index.json`).
 *
 * Phaser cannot draw into the DOM overlay, so the icon is a plain element with
 * the spritesheet as its background, offset to the sprite's frame. DawnLike
 * tiles are 16x16; the tile is scaled with a transform instead of
 * `background-size` so the sheet's pixel dimensions never have to be known.
 */

import type { SpriteIndex } from '../sprites/SpriteIndex';

/** DawnLike tile size in pixels. */
const TILE_PX = 16;

/** How much bigger than 16x16 the panel icons are drawn. */
const ICON_SCALE = 2;

/** Where the spritesheets are served from (public/assets/dawnlike). */
const SHEET_BASE_URL = '/assets/dawnlike/';

/**
 * Material tiers in item names (`copper_pickaxe`, `iron_sword`): the sprite
 * index only has the bare tools, so the tier is stripped for the lookup and
 * shown as a word next to the icon.
 */
const ITEM_TIERS = ['copper', 'iron', 'stone', 'wood'];

export interface WieldedItem {
  /** The sprite-index key, e.g. `pickaxe`. */
  spriteKey: string;
  /** The tier word, or '' when the item name carries none. */
  tier: string;
}

/**
 * Split an item name into its sprite key and tier word: `copper_pickaxe` ->
 * `{spriteKey: 'pickaxe', tier: 'copper'}`, `axe` -> `{spriteKey: 'axe', tier: ''}`.
 */
export function splitItemName(itemName: string): WieldedItem {
  const name = itemName.trim();
  const underscore = name.indexOf('_');
  if (underscore > 0) {
    const head = name.slice(0, underscore);
    if (ITEM_TIERS.includes(head)) {
      return { spriteKey: name.slice(underscore + 1).replace(/_/g, '-'), tier: head };
    }
  }
  return { spriteKey: name.replace(/_/g, '-'), tier: '' };
}

/**
 * A 2x-scaled icon element for an item name, or null when the sprite index has
 * no entry for it (nothing wielded, or an item with no icon yet).
 */
export function createItemIcon(
  spriteIndex: SpriteIndex | undefined,
  itemName: string
): { element: HTMLElement; tier: string } | null {
  if (!itemName || !spriteIndex) return null;
  const { spriteKey, tier } = splitItemName(itemName);
  const entry = spriteIndex[spriteKey];
  if (!entry) return null;

  const column = entry.columns > 0 ? entry.frame % entry.columns : 0;
  const row = entry.columns > 0 ? Math.floor(entry.frame / entry.columns) : 0;

  const tile = document.createElement('span');
  tile.className = 'item-icon';
  tile.style.backgroundImage = `url("${SHEET_BASE_URL}${entry.spritesheet}")`;
  tile.style.backgroundPosition = `-${column * TILE_PX}px -${row * TILE_PX}px`;
  tile.style.transform = `scale(${ICON_SCALE})`;

  const box = document.createElement('span');
  box.className = 'item-icon-box';
  box.style.width = `${TILE_PX * ICON_SCALE}px`;
  box.style.height = `${TILE_PX * ICON_SCALE}px`;
  box.title = itemName;
  box.appendChild(tile);
  return { element: box, tier };
}
