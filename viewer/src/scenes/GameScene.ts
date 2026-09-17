import Phaser from 'phaser';
import { WebSocketClient, WorldState } from '../network';
import type { ConnectionState, InterpolatedEntity, TrackedObject } from '../network';
import type { SpriteIndex } from '../sprites';
import { getSpriteFrame } from '../sprites';
import { ChunkManager, ViewportTracker } from '../terrain';

const TILE_SIZE = 16;
const SCALE = 3; // Scale up for visibility (16 * 3 = 48px per tile)
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.1;

// Actor sprite assignments for entities
const ENTITY_SPRITE_MAP: Record<string, string> = {
  alice: 'actor-1',
  bob: 'actor-5',
};
const DEFAULT_ACTOR_SPRITE = 'actor-1';

/**
 * Object type to sprite key mapping.
 * Maps backend ObjectType values to sprite keys from TSX files.
 */
const OBJECT_SPRITE_MAP: Record<string, string> = {
  tree: 'oak-tree',
  // Rocks not yet defined in TSX - add keys when sprites are identified
  // rock_small: 'rock-small',
  // rock_medium: 'rock-medium',
  // rock_large: 'rock-large',
  // boulder: 'boulder',
};

// Bush sprites are special - they have state-dependent sprites
const BUSH_SPRITE_FULL = 'berry-bush-full';
const BUSH_SPRITE_EMPTY = 'berry-bush-empty';

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
  private connectionText?: Phaser.GameObjects.Text;

  // Chunk-based terrain
  private chunkManager?: ChunkManager;
  private viewportTracker?: ViewportTracker;
  private worldInitialized: boolean = false;

  // Camera controls
  private cameraFollowing: boolean = true;
  private followTarget?: Phaser.GameObjects.Sprite;
  private positionText?: Phaser.GameObjects.Text;

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
      .text(10, 10, 'Arrow keys/WASD: pan | Scroll: zoom | F: toggle follow | 1-5: jump to location', {
        fontFamily: 'monospace',
        fontSize: '12px',
        color: '#ffffff',
      })
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

    // Setup camera dev tools
    this.setupCameraControls();

    // Setup network
    this.setupNetwork();
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

    // Center camera on world (entities are typically near center)
    this.cameras.main.centerOn(worldWidth / 2, worldHeight / 2);

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

    // Get sprite key for this entity (use entity ID to look up, or default)
    const spriteKey = ENTITY_SPRITE_MAP[entity.entityId] || DEFAULT_ACTOR_SPRITE;
    const spriteData = this.spriteIndex ? getSpriteFrame(this.spriteIndex, spriteKey) : null;

    if (!spriteData) {
      console.warn(`No sprite found for key ${spriteKey}, using fallback`);
      return;
    }

    const sprite = this.add.sprite(posX, posY, spriteData.textureKey, spriteData.frame);
    sprite.setScale(SCALE);
    sprite.setDepth(10); // Above tiles

    // Play idle animation if available
    const animKey = `${spriteKey}-idle`;
    if (this.anims.exists(animKey)) {
      sprite.play(animKey);
    }

    this.entitySprites.set(entity.entityId, sprite);
    console.log(
      `Created sprite for entity ${entity.entityId} (${spriteKey}) at (${entity.currentX}, ${entity.currentY})`
    );

    // If this is the first entity, set it as follow target
    if (this.entitySprites.size === 1) {
      this.followTarget = sprite;
      if (this.cameraFollowing) {
        this.cameras.main.startFollow(sprite, true, 0.1, 0.1);
      }
    }
  }

  private removeEntitySprite(entityId: string): void {
    const sprite = this.entitySprites.get(entityId);
    if (sprite) {
      sprite.destroy();
      this.entitySprites.delete(entityId);
      console.log(`Removed sprite for entity ${entityId}`);
    }
  }

  private createObjectSprite(obj: TrackedObject): void {
    const posX = obj.position.x * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
    const posY = obj.position.y * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

    // Determine sprite key based on object type
    let spriteKey: string | null = null;

    if (obj.objectType === 'bush') {
      // Bushes have state-dependent sprites
      const hasBerry = obj.state.berry_count === '1';
      spriteKey = hasBerry ? BUSH_SPRITE_FULL : BUSH_SPRITE_EMPTY;
    } else {
      // Look up sprite key from object type mapping
      spriteKey = OBJECT_SPRITE_MAP[obj.objectType] ?? null;
    }

    if (!spriteKey) {
      console.warn(`No sprite mapping for object type: ${obj.objectType}`);
      return;
    }

    const spriteData = this.spriteIndex ? getSpriteFrame(this.spriteIndex, spriteKey) : null;

    if (!spriteData) {
      console.warn(`No sprite found for key: ${spriteKey} (object type: ${obj.objectType})`);
      return;
    }

    const sprite = this.add.sprite(posX, posY, spriteData.textureKey, spriteData.frame);
    sprite.setScale(SCALE);
    sprite.setDepth(5); // Between tiles and entities

    this.objectSprites.set(obj.objectId, sprite);
    console.log(
      `Created ${obj.objectType} ${obj.objectId} at (${obj.position.x}, ${obj.position.y})`
    );
  }

  private removeObjectSprite(objectId: string): void {
    const sprite = this.objectSprites.get(objectId);
    if (sprite) {
      sprite.destroy();
      this.objectSprites.delete(objectId);
      console.log(`Removed object ${objectId}`);
    }
  }

  private updateObjectSprite(obj: TrackedObject): void {
    const sprite = this.objectSprites.get(obj.objectId);
    if (!sprite || !this.spriteIndex) return;

    // Only bushes have state-dependent sprite changes currently
    if (obj.objectType === 'bush') {
      const hasBerry = obj.state.berry_count === '1';
      const spriteKey = hasBerry ? BUSH_SPRITE_FULL : BUSH_SPRITE_EMPTY;
      const spriteData = getSpriteFrame(this.spriteIndex, spriteKey);

      if (spriteData) {
        sprite.setTexture(spriteData.textureKey, spriteData.frame);
      }
    }
    // Trees and rocks don't have state-dependent sprites (yet)
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

    // Number keys for quick navigation
    // 1: Top-left, 2: Top-right, 3: Bottom-left, 4: Bottom-right, 5: Center
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
      console.log('Camera: following entity');
    } else {
      this.cameras.main.stopFollow();
      console.log('Camera: free mode');
    }
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

    // Update entity sprite positions from interpolated state
    for (const entity of this.worldState.getEntities()) {
      const sprite = this.entitySprites.get(entity.entityId);
      if (sprite) {
        sprite.x = entity.currentX * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
        sprite.y = entity.currentY * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
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
