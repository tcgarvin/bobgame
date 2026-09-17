# Viewer Project Notes

Development notes and patterns for the Phaser 3 viewer.

## TypeScript Configuration

### verbatimModuleSyntax

The tsconfig has `verbatimModuleSyntax: true` which requires explicit `type` keyword for type-only imports:

```typescript
// WRONG - will fail with TS1484
import { ViewerMessage } from './types';

// CORRECT
import type { ViewerMessage } from './types';

// Mixed imports work too
import { WebSocketClient, WorldState } from '../network';
import type { ConnectionState, InterpolatedEntity } from '../network';
```

This applies to all interfaces and types that aren't used as values.

## Asset Loading

### Sprite Index

Sprites are defined in Tiled TSX files (`assets/dawnlike-tileset/`) and indexed via:
```bash
python tools/generate_sprite_index.py
```

Output: `viewer/public/assets/sprite-index.json`

Index format:
```json
{
  "actor-1": {
    "spritesheet": "Characters/Player0.png",
    "frame": 0,
    "columns": 8,
    "animationFrames": [
      {"spritesheet": "Characters/Player0.png", "frame": 0},
      {"spritesheet": "Characters/Player1.png", "frame": 0}
    ]
  },
  "berry-bush-full": {
    "spritesheet": "Objects/Ground0.png",
    "frame": 35,
    "columns": 8
  }
}
```

### DawnLike Tileset

Tile size is 16x16, scaled 3x for visibility (48px rendered).

Spritesheets are symlinked from `assets/dawnlike-tileset/` to `viewer/public/assets/dawnlike/`:
- `Characters/` - Player and NPC sprites (Player0.png, Player1.png for animation pairs)
- `Objects/` - Tiles, terrain, vegetation (Floor.png, Ground0.png, Tree0.png, etc.)

### Sprite Loading Flow

1. **PreloadScene** loads `sprite-index.json` and identifies all required spritesheets
2. Spritesheets are loaded from `assets/dawnlike/` (symlink to the tileset)
3. Animations are auto-created for sprites with `animationFrames` (e.g., `actor-1-idle`)
4. **GameScene** accesses the sprite index via `registry.get('spriteIndex')`

Key sprite mappings:
- Entities: `actor-1`..`actor-12` (assigned per entity ID in `ENTITY_SPRITE_MAP`,
  with a deterministic hash fallback), `wolf`
- Floor tiles: `grass-full`
- Wall tiles: `dirt-full`
- Bushes: `berry-bush-full`, `berry-bush-empty`
- Objects: `oak-tree`, `rock-small`, `rock-medium`, `rock-large`, `boulder`,
  `chest-closed`, `message-board`, `item-pile`
- Building materials and buildings (docs/08_building.md): `reeds`,
  `clay-deposit`, `road`, `wood-floor`, `stone-floor`, `wood-wall`,
  `stone-wall`, `door`, `bed`, `chair`, `table`, `workshop-table`
- Item icons: `axe`, `pickaxe`, `sword`

Sprite keys are kebab-case; `OBJECT_SPRITE_MAP` in `GameScene` maps the
server's snake_case `object_type` onto them.

### Object layers and depth

A tile may hold one ground-layer object (`road`, `wood_floor`, `stone_floor`)
and one structure-layer object, so both have to draw. Object sprites are keyed
by `object_id`, never by position, and the two layers differ only in depth:
ground objects at 4, structures at 5, entities at 10. Adding a building type
means adding it to `OBJECT_SPRITE_MAP` and, if it lies flat, to
`GROUND_LAYER_TYPES`.

Walls are single sprites — a seamless full-block tile from `Wall.png`, not an
autotile — so no neighbour lookup is needed and a wall looks the same however
it is placed. Doors use one closed-door sprite.

## HTML Overlay UI

The entity picker and agent panel are plain DOM, not Phaser: the markup and
styles live in `index.html` and `src/ui/OverlayUI.ts` wires them to
`WorldState`. `GameScene` owns the keys (`F` follow, `P` panel, `0`
settlement) and the camera; `OverlayUI` never touches Phaser.

`WorldState` is the single source of truth for the overlay: per-entity stats,
the 10-entry action/utterance ring buffer, the latest `agent_status`, the
settlement position and the selection. It calls `onStateUpdate` after any
message that changes what the overlay shows.

## Replay Mode and Deep Links

The viewer renders a recorded run with the same code as a live world: the
replay server (`world/src/world/replay`, port 8766) speaks the live protocol
plus the control messages in [docs/07_replay.md](../docs/07_replay.md).

### Deep-link parameters

The whole visible state lives in the query string, and `GameScene` keeps the
address bar in sync with `history.replaceState` (throttled to ~4 Hz, only when
something changed). Parsing and building live in `src/DeepLink.ts`.

| param | meaning |
| --- | --- |
| `run` | replay mode: connect to the replay server and `open_run` |
| `ws` | WebSocket URL override (defaults: live `ws://localhost:8765`, replay `ws://localhost:8766`) |
| `tick` | seek here after the snapshot (replay only) |
| `entity` | select this entity, camera follows it |
| `x`, `y` | put the camera on this tile, follow off |
| `zoom` | camera zoom |
| `play` | `1` to start playing after the seek |
| `speed` | playback speed (0.5, 1, 2, 5, 10, 25) |
| `panel` | `1` / `0` to show or hide the agent panel |
| `object` | select this object id and open its inspector |

Entity and object selection are mutually exclusive, so a link never carries
both. Example: `http://localhost:5173/?run=fake&tick=50&entity=ada`.

### Replay bar and inspectors

- `src/ui/ReplayBar.ts` — the bottom transport bar (run id, jump-to-start,
  ±1, ±10, play/pause, jump-to-end, tick input, slider, speed select, the
  notable-events dropdown from `run_index`, and "copy link"). In live mode
  everything but "copy link" is hidden, and that button copies a *replay* link
  for the run currently being watched.
- Keys (replay only): space play/pause, `,` / `.` step ±1, `[` / `]` step ±10.
  They are suppressed while a form field has focus, and focusing the tick input
  disables the Phaser keyboard so `F`/`P`/digits do not fire.
- `src/ui/ObjectPanel.ts` — clicking a chest, item pile, message board, bush,
  reeds, clay deposit or any placed building opens an inspector. The data comes
  from `ObjectState.state`, where `contents` and `notes` are JSON strings,
  `owner` is an entity id, and `progress` is extraction or dismantle work.
  Trees and rocks are deliberately not clickable: there are far too many of
  them to make every one interactive.
- `src/ui/OverlayUI.ts` — the agent panel: brief (with success condition,
  ticks used of max, notes), planner thought, the Jev decision with every
  option's probability as a bar (chosen one highlighted) plus confidence,
  eject, danger, latency and input tokens. In replay mode it adds collapsible
  "What Jev saw", "Planner turn" and "Memory" sections fed by `agent_detail`,
  which is requested when the selection or the tick changes (debounced to
  ~250 ms while playing).

`WorldState` owns the replay state as well: `isReplay()`, `getReplayStatus()`,
`getRunId()`, `getRunIndex()`, `getAgentDetail(entityId, tickId)` and the
object selection. A second `snapshot` (every seek sends one) clears entities,
objects, logs and agent statuses but keeps the selection; `GameScene` does not
recentre the camera or rebuild the `ChunkManager` on it.

### Fake replay server

`scripts/fake_replay_server.mjs` serves a synthetic 64x64 run (three settlers,
a wolf, a chest, an item pile, a message board, a bush, a built hut with a
walled workshop, a road, reeds and a clay deposit, 200 ticks of scripted
movement, actions, chat, `agent_status`, `agent_detail` and `run_index`) so the
replay UI can be developed without a recording. It is the only thing the `ws`
devDependency is for.

```bash
cd viewer
npm run fake-replay     # ws://localhost:8766, run id "fake", ticks 0-199
npm run dev             # then open the link below
# http://localhost:5173/?run=fake&tick=50&entity=ada
```

## Network Integration

### WebSocket Connection

The viewer connects to the world server's WebSocket on port 8765 (configurable):

```typescript
// Default connection
const client = new WebSocketClient(handler);

// Custom URL
const client = new WebSocketClient(handler, {
  url: 'ws://localhost:8765',
  reconnectDelayMs: 1000,
  maxReconnectAttempts: 10,
});
```

### WorldState Interpolation

Movement is interpolated at 60fps despite 1Hz tick rate:
- On `tick_started`: Record start position and time
- On `tick_completed`: Update target position
- Each frame: Interpolate using ease-out curve

```typescript
// Ease-out for smoother deceleration
function easeOutQuad(t: number): number {
  return t * (2 - t);
}
```

## Running the Viewer

```bash
cd viewer
npm run dev           # Development server with hot reload
npm run build         # Production build (tsc strict + vite)
npm run fake-replay   # Synthetic replay server for UI work (see above)
```

Requires the world server on localhost:8765 for live entity updates, or the
replay server on localhost:8766 (or `npm run fake-replay`) for replay mode.

## Thinking bubble

`GameScene.updateThoughtBubble` draws a small animated "..." bubble to the upper
right of any entity whose latest `agent_status` has mode `planning`, in live and
replay mode alike. It hides while a speech bubble is showing, when the entity is
dead, and as soon as the mode changes (a stint started). Spoken utterances on
the `shout` channel get the same speech bubble as `local` ones.

## Conversations and reflex (docs/09_conversation_and_reflex.md)

- `src/conversation.ts` has the shared, defensive parsing for a `conversation`
  object's `participants` and `transcript` state (both JSON strings); it is
  used by `GameScene`, `OverlayUI` and `ObjectPanel` so the three don't drift.
- A `conversation` object has no sprite: there is no tileset entry for it, and
  the contract says to draw one with Phaser graphics instead of touching the
  tileset. `GameScene.createConversationMarker` draws a small speech-bubble
  shape on the anchor tile (clickable, opens the object inspector) and
  `updateConversationLines` redraws, every frame, a line from the anchor to
  each current participant's interpolated position, thicker and gold for
  whoever `state.speaker` says has the turn. Both are keyed by object id and
  cleaned up on `removed` (including the blanket removal a replay snapshot
  sends for every existing object before seeking).
- Utterances on the `conversation` channel get the same speech bubble as
  `local`/`shout` (`SPEECH_BUBBLE_CHANNELS` in `GameScene`).
- The agent panel (`OverlayUI`) shows `reflex` and `conversation` as their own
  mode-badge colors (`.mode-badge.reflex`/`.mode-badge.conversation` in
  `index.html`), and adds a "Conversation" section — participants, speaker,
  transcript — whenever the selected entity is a participant in one, found by
  scanning `WorldState.getObjects()` for a `conversation` object that lists it.
- The replay object inspector (`ObjectPanel`) formats a selected conversation's
  participants, speaker and transcript as readable rows instead of raw JSON.
- Not verified live: no world-side `conversation` object exists yet to test
  against (concurrent work), and the fake replay server/browser automation
  were unavailable in this pass, so the marker, lines and panels are checked
  by `tsc`/`vite build` and code reading only. `scripts/fake_replay_server.mjs`
  would be the place to add a scripted conversation object for a real check.
