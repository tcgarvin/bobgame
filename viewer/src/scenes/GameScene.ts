import Phaser from 'phaser';
import type { MapData } from '../types/map';
import { TileType, createTestRoom } from '../types/map';
import { WebSocketClient, WorldState } from '../network';
import type { ConnectionState, InterpolatedEntity } from '../network';

const TILE_SIZE = 16;
const SCALE = 3; // Scale up for visibility (16 * 3 = 48px per tile)
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.1;

export class GameScene extends Phaser.Scene {
  private cursors?: Phaser.Types.Input.Keyboard.CursorKeys;
  private wasdKeys?: {
    W: Phaser.Input.Keyboard.Key;
    A: Phaser.Input.Keyboard.Key;
    S: Phaser.Input.Keyboard.Key;
    D: Phaser.Input.Keyboard.Key;
  };
  private mapData?: MapData;

  // Network state
  private wsClient?: WebSocketClient;
  private worldState: WorldState = new WorldState();
  private entitySprites: Map<string, Phaser.GameObjects.Sprite> = new Map();
  private connectionText?: Phaser.GameObjects.Text;

  constructor() {
    super({ key: 'GameScene' });
  }

  create(): void {
    // Create the static test room for tiles (entities come from server)
    this.mapData = createTestRoom(10, 10);
    // Remove the hardcoded player entity - we'll get entities from the server
    this.mapData.entities = [];
    this.renderMap();

    // Setup camera controls
    this.setupCamera();

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

    // Create WebSocket client
    this.wsClient = new WebSocketClient((message) => {
      this.worldState.handleMessage(message);
    });

    // Handle connection state changes
    this.wsClient.onStateChange((state) => {
      this.updateConnectionStatus(state);
    });

    // Connect
    this.wsClient.connect();
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

    // Determine sprite key based on entity type
    const spriteKey = entity.entityType === 'player' ? 'player0' : 'player0';

    const sprite = this.add.sprite(posX, posY, spriteKey, 0);
    sprite.setScale(SCALE);
    sprite.setDepth(10); // Above tiles

    // Play idle animation if available
    if (this.anims.exists('player-idle')) {
      sprite.play('player-idle');
    }

    this.entitySprites.set(entity.entityId, sprite);
    console.log(`Created sprite for entity ${entity.entityId} at (${entity.currentX}, ${entity.currentY})`);

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

  private renderMap(): void {
    if (!this.mapData) return;

    for (let y = 0; y < this.mapData.height; y++) {
      for (let x = 0; x < this.mapData.width; x++) {
        const tile = this.mapData.tiles[y][x];
        const posX = x * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
        const posY = y * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

        const spriteKey = tile.type === TileType.WALL ? 'wall' : 'floor';
        const sprite = this.add.sprite(posX, posY, spriteKey, tile.spriteIndex);
        sprite.setScale(SCALE);
      }
    }
  }

  private setupCamera(): void {
    if (!this.mapData) return;

    const worldWidth = this.mapData.width * TILE_SIZE * SCALE;
    const worldHeight = this.mapData.height * TILE_SIZE * SCALE;

    this.cameras.main.setBounds(0, 0, worldWidth, worldHeight);
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
  }
}
