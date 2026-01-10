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

    // Add berry indicator text (binary: has berry or not)
    const hasBerry = obj.state.berry_count === '1';
    const text = this.add.text(0, 0, hasBerry ? '🫐' : '', {
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
    console.log(`Created bush ${obj.objectId} at (${obj.position.x}, ${obj.position.y}) ${hasBerry ? 'with berry' : 'empty'}`);
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

      // Update text (binary: has berry or not)
      const text = container.getByName('berryText') as Phaser.GameObjects.Text;
      if (text) {
        const hasBerry = obj.state.berry_count === '1';
        text.setText(hasBerry ? '🫐' : '');
      }
    }
  }

  private drawBushGraphics(graphics: Phaser.GameObjects.Graphics, obj: TrackedObject): void {
    const hasBerry = obj.state.berry_count === '1';

    // Bush color based on whether it has a berry
    const bushColor = hasBerry ? 0x228b22 : 0x556b2f; // Forest green (with berry) or dark olive (empty)

    const size = TILE_SIZE * SCALE * 0.8;

    // Draw bush body (circle)
    graphics.fillStyle(bushColor, 1);
    graphics.fillCircle(0, 0, size / 2);

    // Draw a single berry dot if bush has a berry
    if (hasBerry) {
      graphics.fillStyle(0xff0000, 1); // Red berry
      const berrySize = 6;
      graphics.fillCircle(0, 0, berrySize);
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
