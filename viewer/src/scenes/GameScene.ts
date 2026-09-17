import Phaser from 'phaser';
import { WebSocketClient, WorldState } from '../network';
import type {
  ConnectionState,
  InterpolatedEntity,
  TrackedObject,
  UtteranceEvent,
} from '../network';
import type { SpriteIndex } from '../sprites';
import { getSpriteFrame } from '../sprites';
import { ChunkManager, ViewportTracker } from '../terrain';
import { OverlayUI } from '../ui';

const TILE_SIZE = 16;
const SCALE = 3; // Scale up for visibility (16 * 3 = 48px per tile)
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.1;

const PLAYER_TYPE = 'player';
const WOLF_TYPE = 'wolf';
const WOLF_SPRITE = 'wolf';

/** Sprite keys available for actors, used by name and by the hash fallback. */
const ACTOR_SPRITES = [
  'actor-1',
  'actor-2',
  'actor-3',
  'actor-4',
  'actor-5',
  'actor-6',
  'actor-7',
  'actor-8',
  'actor-9',
  'actor-10',
  'actor-11',
  'actor-12',
];

// Actor sprite assignments for entities
const ENTITY_SPRITE_MAP: Record<string, string> = {
  alice: 'actor-1',
  bob: 'actor-5',
  // The twelve settlers.
  ada: 'actor-1',
  bram: 'actor-2',
  cleo: 'actor-3',
  dov: 'actor-4',
  esme: 'actor-5',
  finn: 'actor-6',
  greta: 'actor-7',
  hale: 'actor-8',
  iris: 'actor-9',
  jory: 'actor-10',
  kai: 'actor-11',
  lena: 'actor-12',
};

/**
 * Object type to sprite key mapping.
 * Maps backend ObjectType values to sprite keys from TSX files.
 */
const OBJECT_SPRITE_MAP: Record<string, string> = {
  tree: 'oak-tree',
  rock_small: 'rock-small',
  rock_medium: 'rock-medium',
  rock_large: 'rock-large',
  boulder: 'boulder',
  chest: 'chest-closed',
  message_board: 'message-board',
  item_pile: 'item-pile',
};

// Bush sprites are special - they have state-dependent sprites
const BUSH_SPRITE_FULL = 'berry-bush-full';
const BUSH_SPRITE_EMPTY = 'berry-bush-empty';

const BAR_WIDTH = TILE_SIZE * SCALE - 8;
const HEALTH_BAR_HEIGHT = 4;
const HUNGER_BAR_HEIGHT = 2;
const SPEECH_BUBBLE_MS = 3000;
const DAMAGE_FLASH_MS = 350;

/** Deterministic fallback sprite for entity ids we have no mapping for. */
function hashToActorSprite(entityId: string): string {
  let hash = 0;
  for (let i = 0; i < entityId.length; i++) {
    hash = (hash * 31 + entityId.charCodeAt(i)) >>> 0;
  }
  return ACTOR_SPRITES[hash % ACTOR_SPRITES.length];
}

function spriteKeyForEntity(entity: InterpolatedEntity): string {
  if (entity.entityType === WOLF_TYPE) return WOLF_SPRITE;
  return ENTITY_SPRITE_MAP[entity.entityId] ?? hashToActorSprite(entity.entityId);
}

export class GameScene extends Phaser.Scene {
  private cursors?: Phaser.Types.Input.Keyboard.CursorKeys;
  private wasdKeys?: {
    W: Phaser.Input.Keyboard.Key;
    A: Phaser.Input.Keyboard.Key;
    S: Phaser.Input.Keyboard.Key;
    D: Phaser.Input.Keyboard.Key;
  };
  private spriteIndex?: SpriteIndex;

  // Network state
  private wsClient?: WebSocketClient;
  private worldState: WorldState = new WorldState();
  private entitySprites: Map<string, Phaser.GameObjects.Sprite> = new Map();
  private objectSprites: Map<string, Phaser.GameObjects.Sprite> = new Map();
  private speechBubbles: Map<string, Phaser.GameObjects.Text> = new Map();
  private damageFlashUntil: Map<string, number> = new Map();
  private connectionText?: Phaser.GameObjects.Text;
  private statusBars?: Phaser.GameObjects.Graphics;

  // Chunk-based terrain
  private chunkManager?: ChunkManager;
  private viewportTracker?: ViewportTracker;
  private worldInitialized: boolean = false;

  // Camera controls
  private cameraFollowing: boolean = true;
  private followTarget?: Phaser.GameObjects.Sprite;
  private positionText?: Phaser.GameObjects.Text;

  // HTML overlay
  private overlay?: OverlayUI;

  constructor() {
    super({ key: 'GameScene' });
  }

  create(): void {
    // Get sprite index from registry
    this.spriteIndex = this.registry.get('spriteIndex') as SpriteIndex;

    if (!this.spriteIndex) {
      console.error('Sprite index not found in registry');
    }

    // Setup keyboard controls
    this.setupKeyboardControls();

    // Setup scroll wheel zoom
    this.setupScrollZoom();

    // Display instructions and connection status
    this.add
      .text(
        10,
        10,
        'Arrows/WASD: pan | Scroll: zoom | F: follow | P: panel | 0: settlement | 1-5: jump',
        {
          fontFamily: 'monospace',
          fontSize: '12px',
          color: '#ffffff',
        }
      )
      .setScrollFactor(0)
      .setDepth(100);

    this.connectionText = this.add
      .text(10, 30, 'Connecting...', {
        fontFamily: 'monospace',
        fontSize: '12px',
        color: '#ffff00',
      })
      .setScrollFactor(0)
      .setDepth(100);

    this.positionText = this.add
      .text(10, 50, 'Pos: (0, 0) Tile: (0, 0)', {
        fontFamily: 'monospace',
        fontSize: '12px',
        color: '#aaaaaa',
      })
      .setScrollFactor(0)
      .setDepth(100);

    // Graphics layer for health/hunger bars and the selection ring
    this.statusBars = this.add.graphics();
    this.statusBars.setDepth(15);

    // Setup camera dev tools
    this.setupCameraControls();

    // HTML overlay (entity picker + agent panel)
    this.setupOverlay();

    // Setup network
    this.setupNetwork();
  }

  private setupOverlay(): void {
    this.overlay = new OverlayUI(this.worldState, {
      onSelectEntity: (entityId) => this.selectEntity(entityId),
    });
    this.overlay.setFollowing(this.cameraFollowing);
    this.overlay.refresh();
  }

  /**
   * Select an entity: updates the panel, makes it the follow target and
   * starts camera follow.
   */
  private selectEntity(entityId: string): void {
    this.worldState.setSelectedEntity(entityId);

    const sprite = entityId ? this.entitySprites.get(entityId) : undefined;
    if (sprite) {
      this.followTarget = sprite;
      this.toggleCameraFollow(true);
    }
    this.overlay?.refresh();
  }

  private setupNetwork(): void {
    // Handle entity changes
    this.worldState.onEntityChange((action, entity) => {
      if (action === 'added') {
        this.createEntitySprite(entity);
      } else {
        this.removeEntitySprite(entity.entityId);
      }
    });

    // Flash entities red when they lose health
    this.worldState.onEntityStats((entity, healthDelta) => {
      if (healthDelta < 0) {
        this.damageFlashUntil.set(entity.entityId, this.time.now + DAMAGE_FLASH_MS);
        this.entitySprites.get(entity.entityId)?.setTint(0xff4444);
      }
    });

    // Speech bubbles for local utterances
    this.worldState.onUtterance((utterance) => this.showSpeechBubble(utterance));

    // Keep the HTML overlay in sync
    this.worldState.onStateUpdate(() => this.overlay?.refresh());

    // Handle object changes
    this.worldState.onObjectChange((action, obj) => {
      if (action === 'added') {
        this.createObjectSprite(obj);
      } else if (action === 'removed') {
        this.removeObjectSprite(obj.objectId);
      } else if (action === 'updated') {
        this.updateObjectSprite(obj);
      }
    });

    // Handle chunk changes
    this.worldState.onChunkChange((action, chunkX, chunkY, terrain, version, changes) => {
      if (!this.chunkManager) return;

      if (action === 'loaded' && terrain && version !== undefined) {
        this.chunkManager.loadChunk(chunkX, chunkY, terrain, version);
      } else if (action === 'unloaded') {
        this.chunkManager.unloadChunk(chunkX, chunkY);
      } else if (action === 'terrain_updated' && changes) {
        for (const change of changes) {
          this.chunkManager.updateTile(chunkX, chunkY, change.x, change.y, change.floor_type);
        }
      }
    });

    // Create WebSocket client
    this.wsClient = new WebSocketClient((message) => {
      this.worldState.handleMessage(message);

      // Initialize world after first snapshot
      if (!this.worldInitialized && this.worldState.isInitialized()) {
        this.initializeWorld();
      }
    });

    // Handle connection state changes
    this.wsClient.onStateChange((state) => {
      this.updateConnectionStatus(state);
    });

    // Connect
    this.wsClient.connect();
  }

  private initializeWorld(): void {
    const worldSize = this.worldState.getWorldSize();
    const chunkSize = this.worldState.getChunkSize();

    console.log(`Initializing world: ${worldSize.width}x${worldSize.height}, chunk_size=${chunkSize}`);

    // Initialize chunk manager with sprite index for terrain mappings
    this.chunkManager = new ChunkManager(this, chunkSize, this.spriteIndex);

    // Setup camera bounds
    const worldWidth = worldSize.width * TILE_SIZE * SCALE;
    const worldHeight = worldSize.height * TILE_SIZE * SCALE;
    this.cameras.main.setBounds(0, 0, worldWidth, worldHeight);

    // Center on the settlement when the server knows one, else on the world
    const settlement = this.worldState.getSettlement();
    if (settlement) {
      this.cameras.main.centerOn(
        settlement.x * TILE_SIZE * SCALE,
        settlement.y * TILE_SIZE * SCALE
      );
    } else {
      this.cameras.main.centerOn(worldWidth / 2, worldHeight / 2);
    }

    // Initialize viewport tracker for chunk subscriptions
    this.viewportTracker = new ViewportTracker(this, chunkSize, (chunks) => {
      this.wsClient?.subscribeChunks(chunks);
    });

    // Request initial chunks for current viewport
    this.viewportTracker.forceUpdate();

    this.worldInitialized = true;
  }

  private updateConnectionStatus(state: ConnectionState): void {
    if (!this.connectionText) return;

    switch (state) {
      case 'connected':
        this.connectionText.setText('Connected');
        this.connectionText.setColor('#00ff00');
        break;
      case 'connecting':
        this.connectionText.setText('Connecting...');
        this.connectionText.setColor('#ffff00');
        break;
      case 'reconnecting':
        this.connectionText.setText('Reconnecting...');
        this.connectionText.setColor('#ff8800');
        break;
      case 'disconnected':
        this.connectionText.setText('Disconnected');
        this.connectionText.setColor('#ff0000');
        break;
    }
  }

  private createEntitySprite(entity: InterpolatedEntity): void {
    const posX = entity.currentX * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
    const posY = entity.currentY * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

    const spriteKey = spriteKeyForEntity(entity);
    const spriteData = this.spriteIndex ? getSpriteFrame(this.spriteIndex, spriteKey) : null;

    if (!spriteData) {
      console.warn(`No sprite found for key ${spriteKey}, using fallback`);
      return;
    }

    const sprite = this.add.sprite(posX, posY, spriteData.textureKey, spriteData.frame);
    sprite.setScale(SCALE);
    sprite.setDepth(10); // Above tiles
    sprite.setVisible(entity.alive);

    // Play idle animation if available
    const animKey = `${spriteKey}-idle`;
    if (this.anims.exists(animKey)) {
      sprite.play(animKey);
    }

    // Clicking a sprite selects that entity
    sprite.setInteractive({ useHandCursor: true });
    sprite.on('pointerdown', () => this.selectEntity(entity.entityId));

    this.entitySprites.set(entity.entityId, sprite);
    console.log(
      `Created sprite for entity ${entity.entityId} (${spriteKey}) at (${entity.currentX}, ${entity.currentY})`
    );

    // If this is the first entity, set it as follow target
    if (!this.followTarget) {
      this.followTarget = sprite;
      if (this.cameraFollowing) {
        this.cameras.main.startFollow(sprite, true, 0.1, 0.1);
      }
    }

    // Re-attach the camera if this sprite belongs to the selected entity
    if (this.worldState.getSelectedEntityId() === entity.entityId) {
      this.followTarget = sprite;
      if (this.cameraFollowing) {
        this.cameras.main.startFollow(sprite, true, 0.1, 0.1);
      }
    }
  }

  private removeEntitySprite(entityId: string): void {
    const sprite = this.entitySprites.get(entityId);
    if (sprite) {
      if (this.followTarget === sprite) {
        this.cameras.main.stopFollow();
        this.followTarget = undefined;
      }
      sprite.destroy();
      this.entitySprites.delete(entityId);
      console.log(`Removed sprite for entity ${entityId}`);
    }
    this.speechBubbles.get(entityId)?.destroy();
    this.speechBubbles.delete(entityId);
    this.damageFlashUntil.delete(entityId);
  }

  /** Show a `local` utterance above the speaker for a few seconds. */
  private showSpeechBubble(utterance: UtteranceEvent): void {
    if (utterance.channel !== 'local') return;

    const existing = this.speechBubbles.get(utterance.speaker_id);
    if (existing) {
      existing.destroy();
    }

    const text = this.add.text(0, 0, utterance.text, {
      fontFamily: 'monospace',
      fontSize: '12px',
      color: '#ffffff',
      backgroundColor: '#000000cc',
      padding: { x: 4, y: 2 },
      wordWrap: { width: 180 },
      align: 'center',
    });
    text.setOrigin(0.5, 1);
    text.setDepth(30);

    this.speechBubbles.set(utterance.speaker_id, text);
    this.time.delayedCall(SPEECH_BUBBLE_MS, () => {
      if (this.speechBubbles.get(utterance.speaker_id) === text) {
        this.speechBubbles.delete(utterance.speaker_id);
      }
      text.destroy();
    });
  }

  private createObjectSprite(obj: TrackedObject): void {
    const posX = obj.position.x * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
    const posY = obj.position.y * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

    const spriteKey = this.objectSpriteKey(obj);
    if (!spriteKey) {
      console.warn(`No sprite mapping for object type: ${obj.objectType}`);
      return;
    }

    const spriteData = this.spriteIndex ? getSpriteFrame(this.spriteIndex, spriteKey) : null;

    if (!spriteData) {
      console.warn(`No sprite found for key: ${spriteKey} (object type: ${obj.objectType})`);
      return;
    }

    const existing = this.objectSprites.get(obj.objectId);
    if (existing) {
      existing.destroy();
    }

    const sprite = this.add.sprite(posX, posY, spriteData.textureKey, spriteData.frame);
    sprite.setScale(SCALE);
    sprite.setDepth(5); // Between tiles and entities

    const animKey = `${spriteKey}-idle`;
    if (this.anims.exists(animKey)) {
      sprite.play(animKey);
    }

    this.objectSprites.set(obj.objectId, sprite);
  }

  private objectSpriteKey(obj: TrackedObject): string | null {
    if (obj.objectType === 'bush') {
      // Bushes have state-dependent sprites
      return obj.state.berry_count === '1' ? BUSH_SPRITE_FULL : BUSH_SPRITE_EMPTY;
    }
    return OBJECT_SPRITE_MAP[obj.objectType] ?? null;
  }

  private removeObjectSprite(objectId: string): void {
    const sprite = this.objectSprites.get(objectId);
    if (sprite) {
      sprite.destroy();
      this.objectSprites.delete(objectId);
    }
  }

  private updateObjectSprite(obj: TrackedObject): void {
    const sprite = this.objectSprites.get(obj.objectId);
    if (!sprite || !this.spriteIndex) {
      // The object may have been added by tick_completed before we saw it.
      this.createObjectSprite(obj);
      return;
    }

    const spriteKey = this.objectSpriteKey(obj);
    if (!spriteKey) return;

    const spriteData = getSpriteFrame(this.spriteIndex, spriteKey);
    if (spriteData) {
      sprite.setTexture(spriteData.textureKey, spriteData.frame);
    }
  }

  private setupKeyboardControls(): void {
    if (!this.input.keyboard) return;

    // Arrow keys
    this.cursors = this.input.keyboard.createCursorKeys();

    // WASD keys
    this.wasdKeys = {
      W: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.W),
      A: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.A),
      S: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.S),
      D: this.input.keyboard.addKey(Phaser.Input.Keyboard.KeyCodes.D),
    };
  }

  private setupCameraControls(): void {
    if (!this.input.keyboard) return;

    // F key to toggle camera follow
    this.input.keyboard.on('keydown-F', () => {
      this.toggleCameraFollow();
    });

    // P key toggles the agent panel
    this.input.keyboard.on('keydown-P', () => {
      this.overlay?.togglePanel();
    });

    // Number keys for quick navigation
    // 0: settlement, 1: Top-left, 2: Top-right, 3: Bottom-left, 4: Bottom-right, 5: Center
    this.input.keyboard.on('keydown-ZERO', () => this.jumpToSettlement());
    this.input.keyboard.on('keydown-ONE', () => this.jumpToCorner('top-left'));
    this.input.keyboard.on('keydown-TWO', () => this.jumpToCorner('top-right'));
    this.input.keyboard.on('keydown-THREE', () => this.jumpToCorner('bottom-left'));
    this.input.keyboard.on('keydown-FOUR', () => this.jumpToCorner('bottom-right'));
    this.input.keyboard.on('keydown-FIVE', () => this.jumpToCorner('center'));

    // Expose camera controls to window for console access
    (window as unknown as Record<string, unknown>).cam = {
      goto: (tileX: number, tileY: number) => this.gotoTile(tileX, tileY),
      follow: () => this.toggleCameraFollow(true),
      free: () => this.toggleCameraFollow(false),
      select: (entityId: string) => this.selectEntity(entityId),
      settlement: () => this.jumpToSettlement(),
      zoom: (level: number) => {
        this.cameras.main.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, level));
      },
      pos: () => {
        const cam = this.cameras.main;
        const tileX = Math.floor(cam.scrollX / (TILE_SIZE * SCALE));
        const tileY = Math.floor(cam.scrollY / (TILE_SIZE * SCALE));
        return { x: cam.scrollX, y: cam.scrollY, tileX, tileY };
      },
    };
    console.log('Camera controls available: cam.goto(x,y), cam.follow(), cam.free(), cam.zoom(n), cam.pos()');
  }

  private toggleCameraFollow(follow?: boolean): void {
    if (follow !== undefined) {
      this.cameraFollowing = follow;
    } else {
      this.cameraFollowing = !this.cameraFollowing;
    }

    if (this.cameraFollowing && this.followTarget) {
      this.cameras.main.startFollow(this.followTarget, true, 0.1, 0.1);
    } else {
      this.cameras.main.stopFollow();
    }
    this.overlay?.setFollowing(this.cameraFollowing);
  }

  private jumpToSettlement(): void {
    const settlement = this.worldState.getSettlement();
    if (!settlement) {
      console.warn('No settlement position known for this world');
      return;
    }
    this.gotoTile(settlement.x, settlement.y);
  }

  private jumpToCorner(corner: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | 'center'): void {
    if (!this.worldInitialized) return;

    const worldSize = this.worldState.getWorldSize();
    const margin = 100; // Tiles from edge

    let tileX: number, tileY: number;
    switch (corner) {
      case 'top-left':
        tileX = margin;
        tileY = margin;
        break;
      case 'top-right':
        tileX = worldSize.width - margin;
        tileY = margin;
        break;
      case 'bottom-left':
        tileX = margin;
        tileY = worldSize.height - margin;
        break;
      case 'bottom-right':
        tileX = worldSize.width - margin;
        tileY = worldSize.height - margin;
        break;
      case 'center':
        tileX = worldSize.width / 2;
        tileY = worldSize.height / 2;
        break;
    }

    this.gotoTile(tileX, tileY);
  }

  private gotoTile(tileX: number, tileY: number): void {
    // Stop following when jumping
    this.toggleCameraFollow(false);

    const worldX = tileX * TILE_SIZE * SCALE;
    const worldY = tileY * TILE_SIZE * SCALE;
    this.cameras.main.centerOn(worldX, worldY);
    console.log(`Camera: jumped to tile (${tileX}, ${tileY})`);
  }

  private setupScrollZoom(): void {
    this.input.on(
      'wheel',
      (
        _pointer: Phaser.Input.Pointer,
        _gameObjects: Phaser.GameObjects.GameObject[],
        _deltaX: number,
        deltaY: number
      ) => {
        const camera = this.cameras.main;

        if (deltaY > 0) {
          // Zoom out
          camera.zoom = Math.max(MIN_ZOOM, camera.zoom - ZOOM_STEP);
        } else if (deltaY < 0) {
          // Zoom in
          camera.zoom = Math.min(MAX_ZOOM, camera.zoom + ZOOM_STEP);
        }
      }
    );
  }

  update(_time: number, delta: number): void {
    // Update world state interpolation
    this.worldState.update(delta);

    const selectedId = this.worldState.getSelectedEntityId();
    this.statusBars?.clear();

    // Update entity sprite positions from interpolated state
    for (const entity of this.worldState.getEntities()) {
      const sprite = this.entitySprites.get(entity.entityId);
      if (!sprite) continue;

      const x = entity.currentX * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
      const y = entity.currentY * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
      sprite.x = x;
      sprite.y = y;
      sprite.setVisible(entity.alive);

      // Clear the damage flash once it has run its course
      const flashUntil = this.damageFlashUntil.get(entity.entityId);
      if (flashUntil !== undefined && this.time.now >= flashUntil) {
        this.damageFlashUntil.delete(entity.entityId);
        sprite.clearTint();
      }

      const bubble = this.speechBubbles.get(entity.entityId);
      if (bubble) {
        bubble.x = x;
        bubble.y = y - (TILE_SIZE * SCALE) / 2 - 14;
      }

      if (entity.alive) {
        this.drawEntityBars(entity, x, y, entity.entityId === selectedId);
      }
    }

    // Update viewport tracker (requests new chunks when camera moves)
    this.viewportTracker?.update();

    // Update position display
    if (this.positionText) {
      const cam = this.cameras.main;
      const centerX = cam.scrollX + cam.width / 2;
      const centerY = cam.scrollY + cam.height / 2;
      const tileX = Math.floor(centerX / (TILE_SIZE * SCALE));
      const tileY = Math.floor(centerY / (TILE_SIZE * SCALE));
      const followStatus = this.cameraFollowing ? ' [Following]' : ' [Free]';
      this.positionText.setText(`Tile: (${tileX}, ${tileY}) Zoom: ${cam.zoom.toFixed(1)}x${followStatus}`);
    }

    // Camera panning
    const camSpeed = 5;

    // Arrow key panning
    if (this.cursors) {
      if (this.cursors.left.isDown) {
        this.cameras.main.scrollX -= camSpeed;
      }
      if (this.cursors.right.isDown) {
        this.cameras.main.scrollX += camSpeed;
      }
      if (this.cursors.up.isDown) {
        this.cameras.main.scrollY -= camSpeed;
      }
      if (this.cursors.down.isDown) {
        this.cameras.main.scrollY += camSpeed;
      }
    }

    // WASD panning
    if (this.wasdKeys) {
      if (this.wasdKeys.A.isDown) {
        this.cameras.main.scrollX -= camSpeed;
      }
      if (this.wasdKeys.D.isDown) {
        this.cameras.main.scrollX += camSpeed;
      }
      if (this.wasdKeys.W.isDown) {
        this.cameras.main.scrollY -= camSpeed;
      }
      if (this.wasdKeys.S.isDown) {
        this.cameras.main.scrollY += camSpeed;
      }
    }
  }

  /**
   * Draw the health bar (and, for players, the thin hunger bar) above an
   * entity, plus a selection outline for the currently selected one.
   */
  private drawEntityBars(
    entity: InterpolatedEntity,
    x: number,
    y: number,
    selected: boolean
  ): void {
    const g = this.statusBars;
    if (!g) return;

    const left = x - BAR_WIDTH / 2;
    const top = y - (TILE_SIZE * SCALE) / 2 - HEALTH_BAR_HEIGHT - 3;

    const healthRatio =
      entity.maxHealth > 0 ? Math.max(0, Math.min(1, entity.health / entity.maxHealth)) : 0;

    g.fillStyle(0x7a1f1f, 1);
    g.fillRect(left, top, BAR_WIDTH, HEALTH_BAR_HEIGHT);
    g.fillStyle(0x3fbf5f, 1);
    g.fillRect(left, top, BAR_WIDTH * healthRatio, HEALTH_BAR_HEIGHT);

    if (entity.entityType === PLAYER_TYPE) {
      const hungerRatio =
        entity.maxHunger > 0 ? Math.max(0, Math.min(1, entity.hunger / entity.maxHunger)) : 0;
      const hungerTop = top + HEALTH_BAR_HEIGHT + 1;
      g.fillStyle(0x3a2a14, 1);
      g.fillRect(left, hungerTop, BAR_WIDTH, HUNGER_BAR_HEIGHT);
      g.fillStyle(0xe0913a, 1);
      g.fillRect(left, hungerTop, BAR_WIDTH * hungerRatio, HUNGER_BAR_HEIGHT);
    }

    if (selected) {
      const size = TILE_SIZE * SCALE;
      g.lineStyle(2, 0xffe066, 1);
      g.strokeRect(x - size / 2, y - size / 2, size, size);
    }
  }

  shutdown(): void {
    // Cleanup network on scene shutdown
    if (this.wsClient) {
      this.wsClient.disconnect();
    }
    // Cleanup terrain chunks
    if (this.chunkManager) {
      this.chunkManager.clear();
    }
  }
}
