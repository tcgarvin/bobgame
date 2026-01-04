import Phaser from 'phaser';
import type { MapData } from '../types/map';
import { TileType, createTestRoom } from '../types/map';
import { WebSocketClient, WorldState } from '../network';
import type { ConnectionState, InterpolatedEntity, TrackedObject } from '../network';

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
  private objectSprites: Map<string, Phaser.GameObjects.Container> = new Map();
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

  private createObjectSprite(obj: TrackedObject): void {
    const posX = obj.position.x * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;
    const posY = obj.position.y * TILE_SIZE * SCALE + (TILE_SIZE * SCALE) / 2;

    // Create a container for the bush (graphics + text)
    const container = this.add.container(posX, posY);
    container.setDepth(5); // Between tiles and entities

    // Draw bush as a green circle
    const graphics = this.add.graphics();
    graphics.setName('bushGraphics');
    this.drawBushGraphics(graphics, obj);
    container.add(graphics);

    // Add berry count text
    const berryCount = parseInt(obj.state.berry_count || '0', 10);
    const text = this.add.text(0, 0, berryCount.toString(), {
      fontFamily: 'monospace',
      fontSize: '12px',
      color: '#ffffff',
      stroke: '#000000',
      strokeThickness: 2,
    });
    text.setName('berryText');
    text.setOrigin(0.5, 0.5);
    container.add(text);

    this.objectSprites.set(obj.objectId, container);
    console.log(`Created bush ${obj.objectId} at (${obj.position.x}, ${obj.position.y}) with ${berryCount} berries`);
  }

  private removeObjectSprite(objectId: string): void {
    const container = this.objectSprites.get(objectId);
    if (container) {
      container.destroy();
      this.objectSprites.delete(objectId);
      console.log(`Removed object ${objectId}`);
    }
  }

  private updateObjectSprite(obj: TrackedObject): void {
    const container = this.objectSprites.get(obj.objectId);
    if (container && obj.objectType === 'bush') {
      // Update graphics
      const graphics = container.getByName('bushGraphics') as Phaser.GameObjects.Graphics;
      if (graphics) {
        graphics.clear();
        this.drawBushGraphics(graphics, obj);
      }

      // Update text
      const text = container.getByName('berryText') as Phaser.GameObjects.Text;
      if (text) {
        const berryCount = parseInt(obj.state.berry_count || '0', 10);
        text.setText(berryCount.toString());
      }
    }
  }

  private drawBushGraphics(graphics: Phaser.GameObjects.Graphics, obj: TrackedObject): void {
    const berryCount = parseInt(obj.state.berry_count || '0', 10);
    const maxBerries = parseInt(obj.state.max_berries || '5', 10);
    const ratio = maxBerries > 0 ? berryCount / maxBerries : 0;

    // Bush color based on berry count
    let bushColor = 0x228b22; // Forest green (full)
    if (ratio <= 0) {
      bushColor = 0x556b2f; // Dark olive (empty)
    } else if (ratio < 0.5) {
      bushColor = 0x6b8e23; // Olive drab (low)
    }

    const size = TILE_SIZE * SCALE * 0.8;

    // Draw bush body (circle)
    graphics.fillStyle(bushColor, 1);
    graphics.fillCircle(0, 0, size / 2);

    // Draw berry dots if bush has berries
    if (berryCount > 0) {
      graphics.fillStyle(0xff0000, 1); // Red berries
      const berrySize = 4;
      const positions = [
        { x: -8, y: -6 },
        { x: 6, y: -8 },
        { x: -6, y: 6 },
        { x: 8, y: 4 },
        { x: 0, y: 8 },
      ];
      for (let i = 0; i < Math.min(berryCount, positions.length); i++) {
        graphics.fillCircle(positions[i].x, positions[i].y, berrySize);
      }
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
