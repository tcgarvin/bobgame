import Phaser from 'phaser';
import type { MapData } from '../types/map';
import { TileType, createTestRoom } from '../types/map';

const TILE_SIZE = 16;
const SCALE = 3;  // Scale up for visibility (16 * 3 = 48px per tile)
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.1;

export class GameScene extends Phaser.Scene {
  private player?: Phaser.GameObjects.Sprite;
  private cursors?: Phaser.Types.Input.Keyboard.CursorKeys;
  private wasdKeys?: { W: Phaser.Input.Keyboard.Key; A: Phaser.Input.Keyboard.Key; S: Phaser.Input.Keyboard.Key; D: Phaser.Input.Keyboard.Key };
  private mapData?: MapData;

  constructor() {
    super({ key: 'GameScene' });
  }

  create(): void {
    // Create the test room using our map data structure
    this.mapData = createTestRoom(10, 10);
    this.renderMap();

    // Create player from map entity
    this.createPlayer();

    // Setup camera controls
    this.setupCamera();

    // Setup keyboard controls
    this.setupKeyboardControls();

    // Setup scroll wheel zoom
    this.setupScrollZoom();

    // Display instructions
    this.add.text(10, 10, 'Arrow keys/WASD to pan camera\nScroll wheel to zoom', {
      fontFamily: 'monospace',
      fontSize: '14px',
      color: '#ffffff',
    }).setScrollFactor(0).setDepth(100);
  }

  private renderMap(): void {
    if (!this.mapData) return;

    for (let y = 0; y < this.mapData.height; y++) {
      for (let x = 0; x < this.mapData.width; x++) {
        const tile = this.mapData.tiles[y][x];
        const posX = x * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;
        const posY = y * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;

        const spriteKey = tile.type === TileType.WALL ? 'wall' : 'floor';
        const sprite = this.add.sprite(posX, posY, spriteKey, tile.spriteIndex);
        sprite.setScale(SCALE);
      }
    }
  }

  private createPlayer(): void {
    if (!this.mapData) return;

    const playerEntity = this.mapData.entities.find(e => e.id === 'player');
    if (!playerEntity) return;

    const centerX = playerEntity.position.x * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;
    const centerY = playerEntity.position.y * TILE_SIZE * SCALE + TILE_SIZE * SCALE / 2;

    this.player = this.add.sprite(centerX, centerY, playerEntity.spriteKey, playerEntity.spriteFrame);
    this.player.setScale(SCALE);
    this.player.play('player-idle');
  }

  private setupCamera(): void {
    if (!this.mapData) return;

    const worldWidth = this.mapData.width * TILE_SIZE * SCALE;
    const worldHeight = this.mapData.height * TILE_SIZE * SCALE;

    this.cameras.main.setBounds(0, 0, worldWidth, worldHeight);

    if (this.player) {
      this.cameras.main.startFollow(this.player, true, 0.1, 0.1);
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
    this.input.on('wheel', (_pointer: Phaser.Input.Pointer, _gameObjects: Phaser.GameObjects.GameObject[], _deltaX: number, deltaY: number) => {
      const camera = this.cameras.main;

      if (deltaY > 0) {
        // Zoom out
        camera.zoom = Math.max(MIN_ZOOM, camera.zoom - ZOOM_STEP);
      } else if (deltaY < 0) {
        // Zoom in
        camera.zoom = Math.min(MAX_ZOOM, camera.zoom + ZOOM_STEP);
      }
    });
  }

  update(): void {
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
}
