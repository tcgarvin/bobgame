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

### DawnLike Tileset

Sprites are loaded as spritesheets in `PreloadScene.ts`:
- `floor` - 3 frames (stone floor variants)
- `wall` - 1 frame
- `player0`, `player1` - 2-frame idle animation

Tile size is 16x16, scaled 3x for visibility (48px rendered).

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
npm run dev    # Development server with hot reload
npm run build  # Production build
```

Requires world server running on localhost:8765 for live entity updates.
