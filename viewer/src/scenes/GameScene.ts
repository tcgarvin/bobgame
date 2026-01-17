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
      .text(10, 10, 'Arrow keys/WASD to pan camera\nScroll wheel to zoom', {
        fontFamily: 'monospace',
        fontSize: '14px',
        color: '#ffffff',
      })
      .setScrollFactor(0)
      .setDepth(100);

    this.connectionText = this.add
      .text(10, 50, 'Connecting...', {
        fontFamily: 'monospace',
        fontSize: '14px',
        color: '#ffff00',
      })
      .setScrollFactor(0)
      .setDepth(100);

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

    // Initialize chunk manager
    this.chunkManager = new ChunkManager(this, chunkSize);

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

    // If this is the first entity, follow it with camera
    if (this.entitySprites.size === 1) {
      this.cameras.main.startFollow(sprite, true, 0.1, 0.1);
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

    // Get the appropriate bush sprite based on state
    const hasBerry = obj.state.berry_count === '1';
    const spriteKey = hasBerry ? 'berry-bush-full' : 'berry-bush-empty';
    const spriteData = this.spriteIndex ? getSpriteFrame(this.spriteIndex, spriteKey) : null;

    if (!spriteData) {
      console.warn(`No sprite found for ${spriteKey}`);
      return;
    }

    const sprite = this.add.sprite(posX, posY, spriteData.textureKey, spriteData.frame);
    sprite.setScale(SCALE);
    sprite.setDepth(5); // Between tiles and entities

    this.objectSprites.set(obj.objectId, sprite);
    console.log(
      `Created bush ${obj.objectId} at (${obj.position.x}, ${obj.position.y}) ${hasBerry ? 'with berry' : 'empty'}`
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
    if (sprite && obj.objectType === 'bush' && this.spriteIndex) {
      // Get the appropriate bush sprite based on state
      const hasBerry = obj.state.berry_count === '1';
      const spriteKey = hasBerry ? 'berry-bush-full' : 'berry-bush-empty';
      const spriteData = getSpriteFrame(this.spriteIndex, spriteKey);

      if (spriteData) {
        sprite.setTexture(spriteData.textureKey, spriteData.frame);
      }
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
